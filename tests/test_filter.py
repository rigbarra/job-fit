from src.agent.filter import should_evaluate_job
from src.database.models import Job


def test_should_evaluate_job_passes():
    """Valida que una vacante adecuada para el perfil pase el pre-filtrado."""
    job = Job(
        title="Senior Data Engineer",
        company="TechCorp",
        location="Remote",
        description="Looking for a Senior Data Engineer. Required: Python, SQL, and AWS Step Functions.",
        url="https://example.com/job/ok",
        source="test",
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
        source="test",
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
        url="https://example.com/job/fail-desc",
        source="test",
    )
    passed, reason = should_evaluate_job(job)
    assert not passed
    assert "La descripción no contiene la palabra clave obligatoria 'sql'" in reason


def test_should_evaluate_job_fails_international_hybrid():
    """Valida el descarte de vacantes híbridas o presenciales ubicadas fuera de Chile."""
    job = Job(
        title="Analytics Engineer",
        company="GlobalTech Argentina",
        location="Buenos Aires, Argentina",
        description="Puesto híbrido 2 días en oficina Buenos Aires. Requiere SQL, Python, dbt.",
        url="https://example.com/job/hybrid-intl",
        source="test",
    )
    passed, reason = should_evaluate_job(job)
    assert not passed
    assert "100% remota" in reason or "híbrida" in reason


def test_should_evaluate_job_fails_international_domestic_restriction():
    """Valida el descarte de vacantes internacionales que exigen visa o residencia en EE.UU."""
    job = Job(
        title="Data Engineer",
        company="US Tech Corp",
        location="Remote",
        description="Must reside in the US. No visa sponsorship provided. Requires SQL and Python.",
        url="https://example.com/job/us-only",
        source="test",
    )
    passed, reason = should_evaluate_job(job)
    assert not passed
    assert "sin sponsorship/visa" in reason or "autorización de trabajo local" in reason


def test_should_evaluate_job_fails_chile_3plus_days_onsite():
    """Valida el descarte de vacantes en Chile que exigen 3 o más días presenciales."""
    job = Job(
        title="Ingeniero de Datos",
        company="Banco Local Chile",
        location="Santiago, Chile",
        description="Modalidad híbrida exigiendo 3 días presenciales en la oficina de Las Condes. Requiere SQL y Python.",
        url="https://example.com/job/chile-3days",
        source="test",
    )
    passed, reason = should_evaluate_job(job)
    assert not passed
    assert "3 o más días presenciales" in reason


def test_should_evaluate_job_passes_chile_hybrid_general():
    """Valida que una vacante híbrida en Chile (general o <=2 días) sea aceptada."""
    job = Job(
        title="Analytics Engineer",
        company="Retail Chile",
        location="Santiago, Chile",
        description="Trabajo en modalidad híbrida en Santiago. Manejo de SQL, Python y Power BI.",
        url="https://example.com/job/chile-hybrid-ok",
        source="test",
    )
    passed, reason = should_evaluate_job(job)
    assert passed
    assert reason == ""
