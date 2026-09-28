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


def test_gemini_adaptive_failover(mocker):
    """Verifica que GeminiProvider conmute en caliente al siguiente modelo si el primero falla con 503/429."""
    provider = GeminiProvider(api_key="AIzaSy_fake", model="gemini-3.8-flash")
    assert provider.current_model == "gemini-3.8-flash"

    # Simular: primera llamada a gemini-3.8-flash falla con 503, segunda llamada (gemini-3.6-flash) triunfa
    mock_post = mocker.patch("src.agent.providers._http_post_json")
    mock_post.side_effect = [
        RuntimeError("Error HTTP 503: Service Unavailable"),
        {"candidates": [{"content": {"parts": [{"text": "{\"score\": 90}"}]}}]},
    ]

    res = provider.generate("System", "User")
    assert res == '{"score": 90}'
    # Debe haber conmutado el modelo activo a gemini-3.6-flash
    assert provider.current_model == "gemini-3.6-flash"
    assert mock_post.call_count == 2

    # Siguiente llamada: debe usar directamente el modelo activo conmutado (gemini-3.6-flash)
    mock_post.side_effect = [
        {"candidates": [{"content": {"parts": [{"text": "{\"score\": 85}"}]}}]},
    ]
    res2 = provider.generate("System", "User 2")
    assert res2 == '{"score": 85}'
    assert provider.current_model == "gemini-3.6-flash"
    assert mock_post.call_count == 3
