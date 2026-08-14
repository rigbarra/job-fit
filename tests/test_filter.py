import pytest
from src.database.models import Job
from src.agent.filter import should_evaluate_job

def test_should_evaluate_job_passes():
    """Valida que una vacante adecuada para el perfil pase el pre-filtrado."""
    job = Job(
        title="Senior Data Engineer",
        company="TechCorp",
        location="Remote",
        description="Looking for a Senior Data Engineer. Required: Python, SQL, and AWS Step Functions.",
        url="https://example.com/job/ok",
        source="test"
    )
    passed, reason = should_evaluate_job(job)
    assert passed
    assert reason == ""

def test_should_evaluate_job_fails_title():
    """Valida el descarte si el título no coincide con el rubro de datos."""
    job = Job(
        title="Remote Office Assistant",
        company="AdminCorp",
        location="Remote",
        description="Assist the office with administrative tasks. Requires SQL data entry knowledge.",
        url="https://example.com/job/fail-title",
        source="test"
    )
    passed, reason = should_evaluate_job(job)
    assert not passed
    assert "El título" in reason
    assert "Assistant" in reason

def test_should_evaluate_job_fails_description():
    """Valida el descarte si la descripción no contiene palabras obligatorias (SQL)."""
    job = Job(
        title="Data Engineer Specialist",
        company="CloudTech",
        location="Remote",
        description="Manage pipelines and API integrations using Python and AWS Glue.",
        # Falta "SQL" en la descripción
        url="https://example.com/job/fail-desc",
        source="test"
    )
    passed, reason = should_evaluate_job(job)
    assert not passed
    assert "La descripción no contiene la palabra clave obligatoria 'sql'" in reason
