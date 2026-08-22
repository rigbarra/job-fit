import logging
import random
import time
from collections.abc import Callable
from typing import Any

from config.settings import settings
from src.database.repository import get_match_results_count_today

logger = logging.getLogger(__name__)


class RateLimitError(Exception):
    """Excepción lanzada cuando la API del LLM retorna un error 429 (Too Many Requests)."""


class DailyQuotaExhaustedError(Exception):
    """Excepción lanzada cuando se alcanza el límite de evaluaciones diarias configuradas."""


# Rastreo en memoria de llamadas en el último minuto de la ejecución actual
_last_calls: list[float] = []


def check_daily_quota() -> bool:
    """
    Verifica si aún nos queda cuota disponible el día de hoy consultando
    la base de datos para ver cuántas evaluaciones se han guardado hoy.
    """
    try:
        calls_today = get_match_results_count_today()
        max_calls = settings.llm_max_calls_per_day
        logger.info(f"Cuota LLM: {calls_today}/{max_calls} llamadas realizadas hoy.")
        return calls_today < max_calls
    except Exception as e:
        logger.error(f"Error verificando cuota diaria en BD: {e}")
        return True  # Fallback optimista


def enforce_rpm():
    """Enfuerza el límite de peticiones por minuto (RPM) de forma local."""
    global _last_calls
    max_rpm = settings.llm_max_calls_per_minute
    now = time.time()

    # Mantener solo las marcas de tiempo dentro de los últimos 60 segundos
    _last_calls = [t for t in _last_calls if now - t < 60]

    if len(_last_calls) >= max_rpm:
        # Calcular tiempo de espera para que expire el registro más antiguo
        sleep_time = 60.0 - (now - _last_calls[0]) + 0.5  # Margen de seguridad
        if sleep_time > 0:
            logger.warning(
                f"Límite de RPM alcanzado ({max_rpm} req/min). Esperando {sleep_time:.2f} segundos..."
            )
            time.sleep(sleep_time)
            # Actualizar el listado después del sleep
            now = time.time()
            _last_calls = [t for t in _last_calls if now - t < 60]

    # Registrar llamada actual
    _last_calls.append(time.time())


def call_with_retry(api_func: Callable[[], Any], max_retries: int = 2) -> Any:
    """
    Ejecuta una llamada de la API del LLM controlando la cuota diaria,
    los límites de RPM y manejando reintentos con backoff exponencial y jitter.
    """
    # 1. Validar cuota diaria antes de enviar peticiones
    if not check_daily_quota():
        raise DailyQuotaExhaustedError(
            f"Cuota diaria de llamadas ({settings.llm_max_calls_per_day}) alcanzada."
        )

    # 2. Controlar RPM localmente
    enforce_rpm()

    # 3. Intentar ejecución de la llamada con reintentos moderados
    retries = 0
    while True:
        try:
            return api_func()
        except RateLimitError as e:
            # Error 429
            retries += 1
            if retries > max_retries:
                logger.error(
                    f"LLM API: Se superó el máximo de reintentos ({max_retries}) tras RateLimitError."
                )
                raise e

            # Backoff exponencial corto: 2^retries + ruido aleatorio de jitter (0 a 1 segundo)
            sleep_time = (2**retries) + random.uniform(0.5, 1.5)
            logger.warning(
                f"LLM API: Error 429 (Rate Limit). Reintentando ({retries}/{max_retries}) en {sleep_time:.2f}s..."
            )
            time.sleep(sleep_time)

        except Exception as e:
            logger.error(f"Error inesperado en llamada de LLM: {e}")
            raise e
