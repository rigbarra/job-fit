from src.agent.embedding import compute_semantic_similarity
from src.agent.filter import should_evaluate_job
from src.database.models import Job


def test_compute_semantic_similarity():
    """Valida el cálculo de similitud semántica local con FastEmbed."""
    job_text = "Senior Data Engineer requiring Python, SQL, dbt and AWS."
    sim = compute_semantic_similarity(job_text)
    assert sim is not None
    assert sim > 60.0


def test_should_evaluate_job_fails_low_semantic_similarity(mocker):
    """Valida el descarte de vacantes con baja similitud semántica vectorial."""
    mocker.patch("src.agent.filter.compute_semantic_similarity", return_value=40.0)
    job = Job(
        title="Senior Data Engineer",
        company="TechCorp",
        location="Remote",
        description="Looking for a Data Engineer with SQL and Python.",
        url="https://example.com/job/low-sim",
        source="test",
    )
    passed, reason = should_evaluate_job(job)
    assert not passed
    assert "Vector Semántico Local" in reason


def test_should_evaluate_job_passes():
    """Valida que una vacante adecuada para el perfil pase el pre-filtrado."""
    job = Job(
        title="Senior Data Engineer",
        company="TechCorp",
        location="Remote",
        description="Looking for a Senior Data Engineer. Required: Python, SQL, and AWS Step Functions.",
        url="https://example.com/job/ok",
        source="test",
        salary="USD $4000 - $6000 / mes",
    )
    passed, reason = should_evaluate_job(job)
    assert passed
    assert reason == ""


def test_should_evaluate_job_passes_ai_engineer():
    """Valida que una vacante de AI Engineer con SQL y Python pase el filtro."""
    job = Job(
        title="AI Engineer",
        company="AI Labs Chile",
        location="Santiago, Chile",
        description="Construcción de agentes y RAG. Requerido: Python, SQL y APIs LLM.",
        url="https://example.com/job/ai-ok",
        source="test",
        salary="CLP $4.000.000 / mes",
    )
    passed, reason = should_evaluate_job(job)
    assert passed
    assert reason == ""


def test_should_evaluate_job_passes_bairesdev_for_market_study():
    """Valida que BairesDev pasa el filtro algorítmico local para evaluarse en el estudio de mercado."""
    job = Job(
        title="Senior Data Engineer",
        company="BairesDev",
        location="Remote",
        description="Data Engineer role requiring SQL, Python, and AWS.",
        url="https://example.com/job/bairesdev",
        source="test",
    )
    passed, reason = should_evaluate_job(job)
    assert passed
    assert reason == ""


def test_should_evaluate_job_leadership_exclusion():
    """Valida que todos los roles Lead/Manager/Jefe/Director se descarten estrictamente."""
    job_lead_de = Job(
        title="Lead Data Engineer",
        company="TechCorp",
        location="Remote",
        description="SQL, Python.",
        url="https://example.com/lead-de",
        source="test",
    )
    passed, reason = should_evaluate_job(job_lead_de)
    assert not passed
    assert "liderazgo" in reason.lower()

    job_bi_lead = Job(
        title="Lead BI Analyst",
        company="TechCorp",
        location="Remote",
        description="Power BI, SQL, Tableau.",
        url="https://example.com/lead-bi",
        source="test",
    )
    passed_bi, reason_bi = should_evaluate_job(job_bi_lead)
    assert not passed_bi
    assert "liderazgo" in reason_bi.lower()


def test_should_evaluate_job_fails_title_not_data():
    """Valida el descarte si el título no coincide con el rubro de datos."""
    job = Job(
        title="Remote Office Assistant",
        company="AdminCorp",
        location="Remote",
        description="Assist the office with administrative tasks. Requires SQL and Python.",
        url="https://example.com/job/fail-title",
        source="test",
    )
    passed, reason = should_evaluate_job(job)
    assert not passed
    assert "El título" in reason
    assert "Assistant" in reason


def test_should_evaluate_job_fails_title_blacklist():
    """Valida el descarte si el título contiene palabras en la lista negra (ej. Scientist, Manager)."""
    job = Job(
        title="Data Scientist",
        company="AdminCorp",
        location="Remote",
        description="Data Science position. Requires SQL and Python.",
        url="https://example.com/job/fail-blacklist",
        source="test",
    )
    passed, reason = should_evaluate_job(job)
    assert not passed
    assert "término excluido" in reason


def test_should_evaluate_job_passes_description_with_pipeline():
    """Con OR logic, una descripción que tenga 'pipeline' pasa aunque no tenga SQL ni Python."""
    job = Job(
        title="Data Engineer Specialist",
        company="CloudTech",
        location="Remote",
        description="Manage pipelines and API integrations using AWS Glue.",
        url="https://example.com/job/desc-pipeline",
        source="test",
    )
    passed, _ = should_evaluate_job(job)
    assert passed


def test_should_evaluate_job_fails_description_no_data_keywords():
    """Una descripción sin NINGUNA palabra clave de datos debe ser descartada."""
    job = Job(
        title="Data Engineer Specialist",
        company="CloudTech",
        location="Remote",
        description="Manage projects and coordinate teams with agile methodologies.",
        url="https://example.com/job/fail-desc-none",
        source="test",
    )
    passed, reason = should_evaluate_job(job)
    assert not passed
    assert "ninguna palabra clave" in reason


def test_should_evaluate_job_fails_junior_experience():
    """Valida el descarte si la oferta está dirigida a juniors (0 a 2 años de experiencia)."""
    job = Job(
        title="Data Engineer",
        company="TechCorp",
        location="Remote",
        description="Entry level role for graduates. Required: Python, SQL. 0-2 years of experience.",
        url="https://example.com/job/junior",
        source="test",
    )
    passed, reason = should_evaluate_job(job)
    assert not passed
    assert "perfiles junior" in reason


def test_should_evaluate_job_fails_low_salary():
    """Valida el descarte si el salario es inferior al mínimo de $2.5M CLP o $2500 USD."""
    job = Job(
        title="Data Engineer",
        company="TechCorp",
        location="Remote",
        description="Required: Python, SQL.",
        url="https://example.com/job/low-salary",
        source="test",
        salary="USD $1500 - $2000 / mes",
    )
    passed, reason = should_evaluate_job(job)
    assert not passed
    assert "Salario máximo" in reason


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
    assert "híbrida/física" in reason


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
    assert "residencia local obligatoria" in reason


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


def test_should_evaluate_job_fails_control_de_gestion_in_description():
    """Valida el descarte de vacantes que mencionan 'Control de Gestión' en la descripción."""
    job = Job(
        title="Analista de Datos Senior",
        company="Empresa Test",
        location="Santiago, Chile",
        description="El candidato reportará al área de Control de Gestión construyendo tableros en SQL y Python.",
        url="https://example.com/job/cdg-test",
        source="test",
    )
    passed, reason = should_evaluate_job(job)
    assert not passed
    assert "Control de Gestión" in reason


def test_should_evaluate_job_fails_foreign_city_without_remote():
    """Valida el descarte de vacantes en ciudades extranjeras (ej. Chicago) que no especifican trabajo remoto."""
    job = Job(
        title="Applied AI Engineer I – Supply Chain",
        company="Motorola Solutions",
        location="Chicago, Illinois, Estados Unidos",
        description="At Motorola Solutions we build technologies to help protect people. Requires SQL and Python.",
        url="https://example.com/job/motorola-chicago",
        source="test",
    )
    passed, reason = should_evaluate_job(job)
    assert not passed
    assert "no especifica trabajo remoto" in reason
