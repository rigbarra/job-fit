from config.settings import settings
from src.agent.providers import GeminiProvider, OpenRouterProvider, get_llm_provider


def test_get_llm_provider_openrouter(mocker):
    """Verifica que el proveedor por defecto sea OpenRouterProvider."""
    mocker.patch.object(settings, "llm_provider", "openrouter")
    provider = get_llm_provider()
    assert isinstance(provider, OpenRouterProvider)


def test_get_llm_provider_gemini(mocker):
    """Verifica la selección de GeminiProvider para Google Gemini / Antigravity."""
    mocker.patch.object(settings, "llm_provider", "gemini")
    provider = get_llm_provider()
    assert isinstance(provider, GeminiProvider)
