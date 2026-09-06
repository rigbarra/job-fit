import hashlib
import os
from datetime import UTC, datetime, time
from urllib.parse import urlparse, urlunparse

from sqlalchemy import func, text
from sqlmodel import Session, SQLModel, create_engine, select

from config.settings import settings
from src.database.models import CVSnapshot, Job, MatchResult, SyncState

# Crear motor de base de datos SQLite
engine = create_engine(settings.database_url, echo=False)


def init_db():
    """Crea las tablas de la base de datos si no existen y aplica migraciones ligeras."""
    # Extraer la ruta de la base de datos de la URL de conexión
    db_path = settings.database_url.replace("sqlite:///", "")

    # Manejar rutas relativas en base a project_root
    if not os.path.isabs(db_path):
        db_path = os.path.join(settings.project_root, db_path)

    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    SQLModel.metadata.create_all(engine)

    # Migración liviana: WAL mode + columnas legacy
    with engine.connect() as conn:
        try:
            conn.execute(text("PRAGMA journal_mode=WAL;"))
            conn.execute(text("ALTER TABLE job ADD COLUMN origin_type VARCHAR;"))
            conn.commit()
        except Exception:
            pass


def clean_url(url: str) -> str:
    """Elimina parámetros de rastreo (tracking, utm, ref) de las URLs antes de deduplicar."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        cleaned = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
        return cleaned.rstrip("/")
    except Exception:
        return url.strip().rstrip("/")


def get_hash(value: str) -> str:
    """Genera un hash SHA-256 único para una URL limpia o valor de texto."""
    return hashlib.sha256(clean_url(value).encode("utf-8")).hexdigest()


def is_duplicate(url: str) -> bool:
    """Verifica si la URL de la vacante ya existe en la base de datos."""
    with Session(engine) as session:
        return session.exec(select(Job).where(Job.hash_url == get_hash(url))).first() is not None


def save_job(job: Job) -> tuple[Job, bool]:
    """
    Guarda una vacante si no existe previamente (deduplicación integrada) y enriquece sus metadatos.

    Returns:
        tuple[Job, bool]: (job guardado/existente, True si fue insertado como nuevo).
    """
    from src.agent.filter import extract_modality_and_country, parse_salary_details

    job.hash_url = get_hash(job.url)

    # F-3: Sanitizar company "nan" (artefacto de pandas/JobSpy cuando el campo está vacío)
    if not job.company or str(job.company).strip().lower() in ("nan", "none", ""):
        job.company = "Empresa Confidencial"

    # Enriquecer modalidad, país y origin_type si no están definidos
    if not job.modality or not job.country or not job.origin_type:
        modality, country, origin_type = extract_modality_and_country(job.location, job.description, job.source)
        job.modality = job.modality or modality
        job.country = job.country or country
        job.origin_type = job.origin_type or origin_type

    # Enriquecer salarios numéricos normalizados si no están definidos
    if job.min_salary is None and job.salary:
        min_sal, max_sal, curr = parse_salary_details(job.salary)
        job.min_salary = min_sal
        job.max_salary = max_sal
        job.salary_currency = curr

    with Session(engine) as session:
        # Verificar duplicados por URL
        existing = session.exec(select(Job).where(Job.hash_url == job.hash_url)).first()
        if existing:
            return existing, False

        session.add(job)
        session.commit()
        session.refresh(job)
        return job, True


def get_job_by_id(job_id: int) -> Job | None:
    """Obtiene una vacante por su ID primario."""
    with Session(engine) as session:
        return session.get(Job, job_id)


def get_pending_jobs() -> list[Job]:
    """Obtiene vacantes ingresadas que aún no han sido evaluadas por el LLM."""
    with Session(engine) as session:
        # Obtener jobs cuyo ID no esté en la tabla de MatchResult
        statement = select(Job).where(Job.id.not_in(select(MatchResult.job_id)))
        return list(session.exec(statement).all())


def save_match_result(result: MatchResult) -> MatchResult:
    """
    Guarda (o actualiza) el resultado del análisis del LLM para una vacante.
    F-1 fix: upsert — si ya existe un MatchResult para este job_id, actualiza en vez de duplicar.
    """
    with Session(engine) as session:
        existing = session.exec(select(MatchResult).where(MatchResult.job_id == result.job_id)).first()
        if existing:
            for field in ("score", "tier", "rationale", "missing_keywords", "recommended_salary_ask",
                          "key_technologies", "seniority_level", "adapted_title", "adapted_summary", "adapted_bullets"):
                setattr(existing, field, getattr(result, field))
            session.commit()
            session.refresh(existing)
            return existing
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
    """
    Obtiene el número de evaluaciones reales realizadas por el LLM el día de hoy,
    excluyendo los descartes automáticos del filtro algorítmico local (que no gastan cuota de API).
    """
    # Inicio del día de hoy en UTC con tzinfo
    today_start = datetime.combine(datetime.now(tz=UTC).date(), time.min, tzinfo=UTC)

    with Session(engine) as session:
        statement = (
            select(func.count())
            .select_from(MatchResult)
            .where(
                MatchResult.created_at >= today_start,
                ~MatchResult.rationale.like("Descarte algorítmico%"),
            )
        )
        return session.exec(statement).one()


_OBSIDIAN_SYNC_KEY = "obsidian_last_sync"


def get_obsidian_last_sync() -> datetime | None:
    """Devuelve el timestamp de la última sincronización exitosa con Obsidian, o None si nunca se ha ejecutado."""
    with Session(engine) as session:
        row = session.get(SyncState, _OBSIDIAN_SYNC_KEY)
        return row.last_sync if row else None


def set_obsidian_last_sync(ts: datetime) -> None:
    """Persiste el timestamp de fin de sincronización con Obsidian."""
    with Session(engine) as session:
        row = session.get(SyncState, _OBSIDIAN_SYNC_KEY)
        if row:
            row.last_sync = ts
        else:
            row = SyncState(key=_OBSIDIAN_SYNC_KEY, last_sync=ts)
            session.add(row)
        session.commit()
