import time
import random
import logging
from typing import Callable, Any
from google.api_core import exceptions
from config.settings import settings
from src.database.repository import get_match_results_count_today

logger = logging.getLogger(__name__)

class GeminiQuotaManager:
    # Rastreo en memoria de llamadas en el último minuto de la ejecución actual
    _last_calls = []

    @classmethod
    def check_daily_quota(cls) -> bool:
        """
        Verifica si aún nos queda cuota disponible el día de hoy consultando
        la base de datos para ver cuántas evaluaciones se han guardado hoy.
        """
        try:
            calls_today = get_match_results_count_today()
            max_calls = settings.llm_max_calls_per_day
            logger.info(f"Cuota Gemini: {calls_today}/{max_calls} llamadas realizadas hoy.")
            return calls_today < max_calls
        except Exception as e:
            logger.error(f"Error verificando cuota diaria en BD: {e}")
            return True  # Fallback optimista para no romper el pipeline en fallos menores de base de datos

    @classmethod
    def enforce_rpm(cls):
        """Enfuerza el límite de peticiones por minuto (RPM) de forma local."""
        max_rpm = settings.llm_max_calls_per_minute
        now = time.time()
        
        # Mantener solo las marcas de tiempo dentro de los últimos 60 segundos
        cls._last_calls = [t for t in cls._last_calls if now - t < 60]
        
        if len(cls._last_calls) >= max_rpm:
            # Calcular tiempo de espera para que expire el registro más antiguo
            sleep_time = 60.0 - (now - cls._last_calls[0]) + 0.5  # Margen de seguridad de 0.5s
            if sleep_time > 0:
                logger.warning(f"Límite de RPM alcanzado ({max_rpm} req/min). Esperando {sleep_time:.2f} segundos...")
                time.sleep(sleep_time)
                # Actualizar el listado después del sleep
                now = time.time()
                cls._last_calls = [t for t in cls._last_calls if now - t < 60]
        
        # Registrar llamada actual
        cls._last_calls.append(time.time())

    @classmethod
    def call_with_retry(cls, api_func: Callable[[], Any], max_retries: int = 5) -> Any:
        """
        Ejecuta una llamada de la API de Gemini controlando la cuota diaria,
        los límites de RPM y manejando reintentos con backoff exponencial y jitter.
        """
        # 1. Validar cuota diaria antes de enviar peticiones
        if not cls.check_daily_quota():
            raise RuntimeError("Cuota diaria de llamadas Gemini API agotada en el Free Tier.")
            
        # 2. Controlar RPM localmente
        cls.enforce_rpm()
        
        # 3. Intentar ejecución de la llamada
        retries = 0
        while True:
            try:
                return api_func()
            except exceptions.ResourceExhausted as e:
                # Error 429 (Resource Exhausted o Cuota alcanzada)
                retries += 1
                if retries > max_retries:
                    logger.error(f"Gemini API: Se superó el máximo de reintentos ({max_retries}) tras ResourceExhausted.")
                    raise e
                
                # Backoff exponencial: 2^retries + ruido aleatorio de jitter (0 a 1 segundo)
                sleep_time = (2 ** retries) + random.uniform(0.0, 1.0)
                logger.warning(f"Gemini API: Error 429 ResourceExhausted. Reintentando ({retries}/{max_retries}) en {sleep_time:.2f}s...")
                time.sleep(sleep_time)
                
            except exceptions.GoogleAPICallError as e:
                # Otros errores de API de Google (400, 500, etc.)
                logger.error(f"Google API Call Error: {e.message} (Código: {e.code})")
                raise e
            except Exception as e:
                logger.error(f"Error inesperado en llamada de LLM: {e}")
                raise e
