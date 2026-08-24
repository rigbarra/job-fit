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

    def __init__(self):
        self.api_key = settings.openrouter_api_key
        self.model = settings.openrouter_model
        self.url = "https://openrouter.ai/api/v1/chat/completions"

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        if not self.api_key or self.api_key == "tu_api_key_de_openrouter_aqui":
            raise RuntimeError("OPENROUTER_API_KEY no está configurada en .env.")

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

    def __init__(self):
        self.api_key = settings.gemini_api_key
        self.model = settings.gemini_model
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY no está configurada en .env.")

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

    def __init__(self):
        self.api_key = settings.openai_api_key or "sk-dummy"
        self.model = settings.openai_model
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
    """Factory para instanciar el proveedor de LLM según la configuración."""
    provider_name = (settings.llm_provider or "openrouter").lower().strip()

    if provider_name in ["gemini", "antigravity"]:
        logger.info(f"Usando proveedor de LLM: Google Gemini ({settings.gemini_model})")
        return GeminiProvider()
    elif provider_name in ["openai", "vllm", "ollama"]:
        logger.info(f"Usando proveedor de LLM: OpenAI Compatible ({settings.openai_model})")
        return OpenAIProvider()
    else:
        logger.info(f"Usando proveedor de LLM: OpenRouter ({settings.openrouter_model})")
        return OpenRouterProvider()
