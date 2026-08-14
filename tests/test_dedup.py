from src.database.models import Job, MatchResult
from src.database.repository import (
    get_pending_jobs,
    is_duplicate,
    save_job,
    save_match_result,
)


def test_job_save_and_duplicate():
    """Valida la inserción de empleos y el comportamiento del filtro de deduplicación."""
    job_url = "https://example.com/job/data-engineer-1"

    job = Job(
        title="Data Engineer",
        company="Tech Corp",
        location="Remote",
        description="Looking for a Python/SQL expert.",
        url=job_url,
        source="test",
    )

    # 1. Verificar que inicialmente no está duplicado
    assert not is_duplicate(job_url)

    # 2. Guardar el empleo por primera vez
    saved_job = save_job(job)
    assert saved_job.id is not None
    assert saved_job.hash_url is not None

    # 3. Comprobar que ahora sí es detectado como duplicado
    assert is_duplicate(job_url)

    # 4. Intentar guardar otra instancia con la misma URL
    duplicate_job = Job(
        title="Senior Data Engineer",  # Título diferente, misma URL
        company="Tech Corp",
        location="Remote",
        description="Other description",
        url=job_url,
        source="test",
    )
    saved_duplicate = save_job(duplicate_job)

    # Debe retornar el registro original sin duplicarlo en la base de datos
    assert saved_duplicate.id == saved_job.id
    assert saved_duplicate.title == "Data Engineer"  # No cambia


def test_get_pending_jobs():
    """Valida que get_pending_jobs solo retorna empleos no analizados por el LLM."""
    job1 = Job(
        title="Analytics Engineer",
        company="DataInc",
        location="Santiago",
        description="dbt, BigQuery expert.",
        url="https://datainc.com/jobs/1",
        source="test",
    )
    job2 = Job(
        title="Data Platform Engineer",
        company="Cloudy",
        location="Remote",
        description="Spark, Kubernetes.",
        url="https://cloudy.com/jobs/2",
        source="test",
    )

    saved_job1 = save_job(job1)
    saved_job2 = save_job(job2)

    # Al inicio, ambos están pendientes
    pending = get_pending_jobs()
    assert len(pending) == 2

    # Guardamos un resultado de match para el job1 (analizado)
    match = MatchResult(
        job_id=saved_job1.id,
        score=75.0,
        tier=2,
        rationale="Buen encaje con dbt.",
        missing_keywords="airflow",
    )
    save_match_result(match)

    # Ahora solo el job2 debe estar pendiente
    pending_after = get_pending_jobs()
    assert len(pending_after) == 1
    assert pending_after[0].id == saved_job2.id
