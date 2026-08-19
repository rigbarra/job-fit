import hashlib
import os
from datetime import UTC

from sqlmodel import Session, SQLModel, create_engine, select

from config.settings import settings
from src.database.models import CVSnapshot, Job, MatchResult

# Crear motor de base de datos SQLite
engine = create_engine(settings.database_url, echo=False)


def init_db():
    """Crea las tablas de la base de datos si no existen."""
    # Extraer la ruta de la base de datos de la URL de conexión
    db_path = settings.database_url.replace("sqlite:///", "")

    # Manejar rutas relativas en base a project_root
    if not os.path.isabs(db_path):
        db_path = os.path.join(settings.project_root, db_path)

    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    SQLModel.metadata.create_all(engine)


def get_hash(value: str) -> str:
    """Genera un hash SHA-256 único para una URL o valor de texto."""
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def is_duplicate(url: str) -> bool:
    """Verifica si la URL de la vacante ya existe en la base de datos."""
    hash_url = get_hash(url)
    with Session(engine) as session:
        statement = select(Job).where(Job.hash_url == hash_url)
        results = session.exec(statement)
        return results.first() is not None


def save_job(job: Job) -> tuple[Job, bool]:
    """
    Guarda una vacante si no existe previamente (deduplicación integrada).

    Returns:
        tuple[Job, bool]: (job guardado/existente, True si fue insertado como nuevo).
    """
    job.hash_url = get_hash(job.url)
    with Session(engine) as session:
        # Verificar duplicados por URL
        statement = select(Job).where(Job.hash_url == job.hash_url)
        existing = session.exec(statement).first()
        if existing:
            return existing, False

        session.add(job)
        session.commit()
        session.refresh(job)
        return job, True


def get_pending_jobs() -> list[Job]:
    """Obtiene vacantes ingresadas que aún no han sido evaluadas por el LLM."""
    with Session(engine) as session:
        # Obtener jobs cuyo ID no esté en la tabla de MatchResult
        statement = select(Job).where(Job.id.not_in(select(MatchResult.job_id)))
        return list(session.exec(statement).all())


def save_match_result(result: MatchResult) -> MatchResult:
    """Guarda el resultado del análisis del LLM para una vacante."""
    with Session(engine) as session:
        session.add(result)
        session.commit()
        session.refresh(result)
        return result


def save_cv_snapshot(snapshot: CVSnapshot) -> CVSnapshot:
    """Guarda un registro del PDF/TeX generado para un empleo específico."""
    with Session(engine) as session:
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
        return snapshot


def get_match_results_count_today() -> int:
    """Obtiene el número de evaluaciones (MatchResult) realizadas el día de hoy."""
    from datetime import datetime, time

    from sqlalchemy import func

    # Inicio del día de hoy en UTC
    today_start = datetime.combine(datetime.now(tz=UTC).date(), time.min)

    with Session(engine) as session:
        statement = (
            select(func.count())
            .select_from(MatchResult)
            .where(MatchResult.created_at >= today_start)
        )
        return session.exec(statement).one()
