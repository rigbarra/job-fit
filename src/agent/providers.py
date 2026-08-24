import logging
from abc import ABC, abstractmethod
from typing import Any

from curl_cffi import requests

from config.settings import settings

logger = logging.getLogger(__name__)


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
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }

        response = requests.post(self.url, headers=headers, json=payload, timeout=45)
        if response.status_code == 429:
            raise RuntimeError("Rate limit o cuota agotada en OpenRouter (HTTP 429)")
        elif response.status_code != 200:
            raise RuntimeError(
                f"Error en OpenRouter API ({response.status_code}): {response.text[:300]}"
            )

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("Respuesta vacía recibida de OpenRouter API")

        return choices[0].get("message", {}).get("content", "")


class GeminiProvider(BaseLLMProvider):
    """Proveedor nativo para Google Gemini API (Gemini 2.0 Flash / 1.5 Pro)."""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model if model and "gemini" in model else "gemini-2.0-flash"
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError("API Key no provista para Gemini.")

        headers = {"Content-Type": "application/json"}
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

        response = requests.post(self.url, headers=headers, json=payload, timeout=45)
        if response.status_code == 429:
            raise RuntimeError("Rate limit alcanzado en Google Gemini API (HTTP 429)")
        elif response.status_code != 200:
            raise RuntimeError(
                f"Error en Gemini API ({response.status_code}): {response.text[:300]}"
            )

        data = response.json()
        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Respuesta vacía recibida de Google Gemini API")

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise RuntimeError("No se encontraron partes de texto en la respuesta de Gemini API")

        return parts[0].get("text", "")


class OpenAIProvider(BaseLLMProvider):
    """Proveedor para OpenAI o endpoints compatibles (vLLM, Ollama, LiteLLM)."""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.base_url = settings.openai_api_base.rstrip("/")
        self.url = f"{self.base_url}/chat/completions"

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }

        response = requests.post(self.url, headers=headers, json=payload, timeout=45)
        if response.status_code != 200:
            raise RuntimeError(
                f"Error en OpenAI API ({response.status_code}): {response.text[:300]}"
            )

        data = response.json()
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
    elif provider_name == "openai":
        logger.info(f"Usando proveedor de LLM: OpenAI Compatible ({model})")
        return OpenAIProvider(api_key, model)
    else:
        logger.info(f"Usando proveedor de LLM: OpenRouter ({model})")
        return OpenRouterProvider(api_key, model)
