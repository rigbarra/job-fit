from config.settings import settings
from src.agent.providers import GeminiProvider, OpenRouterProvider, get_llm_provider


def test_get_llm_provider_openrouter(mocker):
    """Verifica que el proveedor sea OpenRouterProvider."""
    mocker.patch.object(settings, "llm_provider", "openrouter")
    mocker.patch.object(settings, "llm_api_key", "sk-or-testkey")
    provider = get_llm_provider()
    assert isinstance(provider, OpenRouterProvider)


def test_get_llm_provider_gemini(mocker):
    """Verifica la selección de GeminiProvider para Google Gemini."""
    mocker.patch.object(settings, "llm_provider", "gemini")
    mocker.patch.object(settings, "llm_api_key", "AIzaSy-testkey")
    provider = get_llm_provider()
    assert isinstance(provider, GeminiProvider)


def test_get_llm_provider_autodetect_gemini(mocker):
    """Verifica la auto-detección de Gemini basado en la API Key tradicional."""
    mocker.patch.object(settings, "llm_provider", None)
    mocker.patch.object(settings, "llm_api_key", "AIzaSy_SomeSecretKey")
    provider = get_llm_provider()
    assert isinstance(provider, GeminiProvider)


def test_get_llm_provider_autodetect_gemini_aq(mocker):
    """Verifica la auto-detección de Gemini basado en el nuevo formato de API Key (AQ.)."""
    mocker.patch.object(settings, "llm_provider", None)
    mocker.patch.object(settings, "llm_api_key", "AQ.Ab8_SomeSecureKey")
    provider = get_llm_provider()
    assert isinstance(provider, GeminiProvider)


def test_get_llm_provider_autodetect_openrouter(mocker):
    """Verifica la auto-detección de OpenRouter basado en la API Key."""
    mocker.patch.object(settings, "llm_provider", None)
    mocker.patch.object(settings, "llm_api_key", "sk-or-v1-SomeKey")
    provider = get_llm_provider()
    assert isinstance(provider, OpenRouterProvider)


def test_get_llm_provider_deepseek_or_openai(mocker):
    """Verifica la selección de OpenAICompatibleProvider para DeepSeek/OpenAI."""
    from src.agent.providers import OpenAIProvider
    mocker.patch.object(settings, "llm_provider", "deepseek")
    mocker.patch.object(settings, "llm_api_key", "sk-deepseek-12345")
    mocker.patch.object(settings, "llm_base_url", "https://api.deepseek.com/v1")
    provider = get_llm_provider()
    assert isinstance(provider, OpenAIProvider)
    assert provider.base_url == "https://api.deepseek.com/v1"
