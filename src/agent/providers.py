import json
import logging
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

from config.settings import settings
from src.agent.quota import RateLimitError

logger = logging.getLogger(__name__)


import time

def _http_post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int = 60, retries: int = 2) -> dict:
    """Envía un POST HTTP con JSON usando urllib.request con reintentos automáticos."""
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")

    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as he:
            err_body = he.read().decode("utf-8", errors="ignore")
            if he.code == 429:
                if attempt < retries:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                raise RateLimitError(f"Rate limit alcanzado (HTTP 429): {err_body[:200]}")
            if attempt < retries and he.code in (500, 502, 503, 504):
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"Error HTTP {he.code}: {err_body[:300]}")
        except (urllib.error.URLError, TimeoutError) as ue:
            if attempt < retries:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise RuntimeError(f"Error de conexión con el proveedor LLM tras reintentos: {ue}")


class BaseLLMProvider(ABC):
    """Interfaz abstracta para proveedores de modelos LLM."""

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Envía el prompt al modelo y retorna la respuesta como texto bruto."""


class OpenRouterProvider(BaseLLMProvider):
    """Proveedor para la API de OpenRouter (gratuito o de pago)."""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.url = "https://openrouter.ai/api/v1/chat/completions"

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError("API Key no provista para OpenRouter.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://github.com/rigbarra/job-fit",
            "X-Title": "job-fit-evaluator",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }

        data = _http_post_json(self.url, headers, payload, timeout=30)
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("Respuesta vacía recibida de OpenRouter API")

        return choices[0].get("message", {}).get("content", "")


class GeminiProvider(BaseLLMProvider):
    """
    Proveedor nativo para Google Gemini API con pool resiliente y conmutación
    inteligente en caliente (Adaptive Failover). Si el modelo principal experimenta
    saturación (503) o límite de cuota (429), conmuta automáticamente al siguiente
    modelo y recuerda cuál está operativo para las siguientes llamadas.
    """

    DEFAULT_FALLBACK_MODELS = [
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3.7-flash",
        "gemini-flash-latest",
    ]

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        # Construir lista ordenada de modelos candidatos sin duplicados
        candidates = [model] + self.DEFAULT_FALLBACK_MODELS
        seen = set()
        self.models = [m for m in candidates if m and not (m in seen or seen.add(m))]
        self.active_idx = 0

    @property
    def current_model(self) -> str:
        return self.models[self.active_idx]

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError("API Key no provista para Gemini.")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        }
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": f"Instrucciones del sistema:\n{system_prompt}\n\nEntrada del usuario:\n{user_prompt}"}
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }

        last_error = None
        for i in range(len(self.models)):
            idx = (self.active_idx + i) % len(self.models)
            model = self.models[idx]
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
            try:
                data = _http_post_json(url, headers, payload, timeout=45, retries=1)
                candidates = data.get("candidates", [])
                if not candidates:
                    raise RuntimeError("Respuesta vacía recibida de Google Gemini API")

                parts = candidates[0].get("content", {}).get("parts", [])
                if not parts:
                    raise RuntimeError("No se encontraron partes de texto en la respuesta de Gemini API")

                # Si conmutamos con éxito a otro modelo, recordarlo como activo para llamadas futuras
                if idx != self.active_idx:
                    logger.info(f"LLM Failover: Conmutado con éxito a '{model}' como modelo activo.")
                    self.active_idx = idx

                return parts[0].get("text", "")
            except (RateLimitError, RuntimeError) as ex:
                err_str = str(ex)
                is_transient = any(
                    code in err_str
                    for code in ["429", "503", "500", "502", "504", "UNAVAILABLE", "RESOURCE_EXHAUSTED"]
                )
                if is_transient and len(self.models) > 1:
                    next_idx = (idx + 1) % len(self.models)
                    next_model = self.models[next_idx]
                    logger.warning(
                        f"LLM Provider: Modelo '{model}' temporalmente no disponible ({err_str[:80]}...). "
                        f"Conmutando en caliente a '{next_model}'..."
                    )
                    last_error = ex
                    continue
                raise ex

        if last_error:
            raise last_error
        raise RuntimeError("No se pudo obtener respuesta de ningún modelo en el pool de Gemini.")


class OpenAIProvider(BaseLLMProvider):
    """Proveedor para OpenAI o endpoints compatibles (DeepSeek, Groq, Ollama, vLLM)."""

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        self.api_key = api_key
        self.model = model
        target_base = base_url or settings.llm_base_url or settings.openai_api_base
        self.base_url = target_base.rstrip("/")
        self.url = f"{self.base_url}/chat/completions"

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }

        data = _http_post_json(self.url, headers, payload, timeout=30)
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("Respuesta vacía recibida de OpenAI API")

        return choices[0].get("message", {}).get("content", "")


def get_llm_provider() -> BaseLLMProvider:
    """Factory para instanciar el proveedor de LLM según la configuración o auto-detección."""
    api_key = settings.llm_api_key or settings.openrouter_api_key
    model = settings.llm_model
    # Fallback si se usa el modelo anterior de openrouter
    if not settings.llm_api_key and settings.openrouter_model:
        model = settings.openrouter_model
        
    provider_name = (settings.llm_provider or "").lower().strip()

    if not api_key:
        raise RuntimeError("No se ha configurado LLM_API_KEY u OPENROUTER_API_KEY en el archivo .env")

    # Auto-detección del proveedor basado en el prefijo de la API Key si no está configurado explícitamente
    if not provider_name:
        if api_key.startswith(("AIzaSy", "AQ.")):
            provider_name = "gemini"
        elif api_key.startswith("sk-or-"):
            provider_name = "openrouter"
        elif api_key.startswith("sk-") or "localhost" in settings.openai_api_base:
            provider_name = "openai"
        else:
            provider_name = "openrouter"

    if provider_name == "gemini":
        logger.info(f"Usando proveedor de LLM: Google Gemini ({model})")
        return GeminiProvider(api_key, model)
    elif provider_name == "openrouter" or api_key.startswith("sk-or-"):
        logger.info(f"Usando proveedor de LLM: OpenRouter ({model})")
        return OpenRouterProvider(api_key, model)
    elif (
        provider_name in ("openai", "deepseek", "groq", "ollama")
        or settings.llm_base_url is not None
        or api_key.startswith("sk-")
    ):
        logger.info(f"Usando proveedor de LLM: OpenAI Compatible / {provider_name or 'Custom'} ({model})")
        return OpenAIProvider(api_key, model, base_url=settings.llm_base_url)
    else:
        logger.info(f"Usando proveedor de LLM: OpenRouter ({model})")
        return OpenRouterProvider(api_key, model)
