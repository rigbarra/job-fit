import src.database.repository as repo
from src.database.models import Job, MatchResult
from src.database.repository import init_db, save_job
from src.market_engine.analytics import generate_market_study_report
from sqlmodel import Session


def test_generate_market_study_report(tmp_path):
    """Valida la generación del estudio de mercado a partir de las ofertas en BD."""
    init_db()
    # 1. Insertar vacantes de prueba
    job1 = Job(
        title="Senior Data Engineer",
        company="TechCorp Chile",
        location="Santiago, Chile",
        description="Data engineering with PySpark and AWS.",
        url="https://example.com/job/test1",
        source="test",
        salary="CLP $3.800.000 / mes",
        min_salary=3800000.0,
        max_salary=3800000.0,
        salary_currency="CLP",
        modality="Remoto 100%",
        country="Chile",
    )
    job2 = Job(
        title="Analytics Engineer",
        company="US Tech",
        location="Remote",
        description="Analytics engineering with dbt and Snowflake.",
        url="https://example.com/job/test2",
        source="test",
        salary="USD $5000 / mes",
        min_salary=5000.0,
        max_salary=5000.0,
        salary_currency="USD",
        modality="Remoto 100%",
        country="Internacional / Remote",
    )

    save_job(job1)
    save_job(job2)

    with Session(repo.engine) as session:
        mr1 = MatchResult(
            job_id=job1.id,
            score=85.0,
            tier=1,
            rationale="Good fit",
            missing_keywords="[]",
            key_technologies='["PySpark", "AWS"]',
            recommended_salary_ask="$4.000.000 CLP / mes",
            seniority_level="Senior",
        )
        session.add(mr1)
        session.commit()

    test_out = str(tmp_path / "test_market_study.md")
    file_path, text = generate_market_study_report(output_path=test_out)

    assert file_path == test_out
    assert "Estudio Histórico de Mercado Laboral" in text
    assert "Data Engineer" in text
    assert "Analytics Engineer" in text
    assert "Mercado Laboral Chile" in text
    assert "Mercado Internacional" in text


def test_extract_and_normalize_salary_hourly():
    """Valida la conversión de tarifa horaria CLP a mensual full-time (160h/mes)."""
    from src.market_engine.analytics import extract_and_normalize_salary

    job = Job(
        title="Data Engineer",
        company="Mining Corp",
        location="Santiago, Chile",
        description="Sueldo: $14.000 - $20.000 la hora para soporte de pipelines.",
        url="https://example.com/hourly",
        source="indeed",
    )
    sal = extract_and_normalize_salary(job)
    assert sal is not None
    assert sal["is_hourly"] is True
    assert sal["clp_min"] == 14000 * 160  # 2.240.000
    assert sal["clp_max"] == 20000 * 160  # 3.200.000


def test_extract_and_normalize_salary_annual_usd():
    """Valida la mensualización de salario anual en USD."""
    from src.market_engine.analytics import extract_and_normalize_salary, USD_TO_CLP

    job = Job(
        title="Analytics Engineer",
        company="Global Remote",
        location="Remote",
        description="The base salary range is $90,000 - $120,000 annual.",
        url="https://example.com/annual-usd",
        source="linkedin",
    )
    sal = extract_and_normalize_salary(job)
    assert sal is not None
    assert sal["usd_min"] == round(90000 / 12)  # 7500
    assert sal["usd_max"] == round(120000 / 12)  # 10000
    assert sal["clp_min"] == round((90000 / 12) * USD_TO_CLP)


def test_extract_and_normalize_salary_discards_benefits_and_foreign_currencies():
    """Valida que beneficios no salariales y monedas foráneas locales sean descartadas."""
    from src.market_engine.analytics import extract_and_normalize_salary

    # 1. Parental leave
    job_parental = Job(
        title="Senior Data Engineer",
        company="US Tech",
        location="Chicago, IL",
        description="We offer paid parental leave & adoption assistance (up to $10,000).",
        url="https://example.com/parental",
        source="linkedin",
    )
    assert extract_and_normalize_salary(job_parental) is None

    # 2. Colombia COP sin USD
    job_cop = Job(
        title="Data Analyst",
        company="Bogotá Data",
        location="Bogotá, Distrito Capital, Colombia",
        description="Salario: $8.801.000 pesos",
        url="https://example.com/cop",
        source="linkedin",
    )
    assert extract_and_normalize_salary(job_cop) is None
