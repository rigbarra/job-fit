import json

from src.agent.evaluator import evaluate_job
from src.database.models import Job


def test_evaluate_job_tier_2(mocker):
    """Verifica la evaluación del LLM para un match de nivel Tier 2 (retoque) con OpenRouter."""
    # 1. Crear datos de entrada simulados
    job = Job(
        id=99,
        title="Analytics Engineer",
        company="MockCorp",
        location="Remote",
        description="Requerimos experiencia en dbt, Python y Airflow.",
        url="https://example.com/job/99",
        source="test",
    )

    mock_json_response = {
        "score": 75.0,
        "rationale": "El candidato tiene buena base en Python y dbt, pero le falta Airflow.",
        "missing_keywords": ["Airflow"],
        "adapted_title": "Senior Data Platform Engineer",
        "adapted_summary": "Analytics Engineer con experiencia en Snowflake y dbt...",
    }

    # 2. Configurar mock de curl_cffi.requests.post para OpenRouter
    mock_response = mocker.Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(mock_json_response, ensure_ascii=False)}}]
    }

    # Mockear requests.post y settings de LLM en evaluator
    mocker.patch("src.agent.providers.requests.post", return_value=mock_response)
    from config.settings import settings
    mocker.patch.object(settings, "llm_api_key", "sk-or-testkey")
    mocker.patch.object(settings, "llm_provider", "openrouter")

    # 3. Ejecutar la evaluación
    match_result = evaluate_job(job)

    # 4. Aserciones
    assert match_result.job_id == 99
    assert match_result.score == 75.0
    assert match_result.tier == 2  # Coincide con rango 60-84
    assert "Airflow" in match_result.rationale
    assert json.loads(match_result.missing_keywords) == ["Airflow"]
    assert (
        match_result.adapted_summary == "Analytics Engineer con experiencia en Snowflake y dbt..."
    )

    assert match_result.adapted_title == 'Senior Data Platform Engineer'


def test_evaluate_job_tier_3(mocker):
    """Verifica la evaluación del LLM para un descarte (Tier 3) con OpenRouter."""
    job = Job(
        id=100,
        title="Senior Java Developer",
        company="OtherCorp",
        location="Remote",
        description="Senior Java backend microservices architect.",
        url="https://example.com/job/100",
        source="test",
    )

    mock_json_response = {
        "score": 25.0,
        "rationale": "El perfil del candidato está enfocado en Data/Analytics y no tiene experiencia en Java ni microservicios.",
        "missing_keywords": ["Java", "Spring Boot", "Microservicios"],
        "adapted_summary": None,
        "adapted_bullets": None,
    }

    mock_response = mocker.Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": json.dumps(mock_json_response, ensure_ascii=False)}}]
    }

    mocker.patch("src.agent.providers.requests.post", return_value=mock_response)
    from config.settings import settings
    mocker.patch.object(settings, "llm_api_key", "sk-or-testkey")
    mocker.patch.object(settings, "llm_provider", "openrouter")

    match_result = evaluate_job(job)

    assert match_result.score == 25.0
    assert match_result.tier == 3
    assert match_result.adapted_summary is None
    assert match_result.adapted_bullets is None
