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
