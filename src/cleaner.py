import logging
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlmodel import Session, select

from config.settings import settings
from src.database.models import CVSnapshot, Job, MatchResult
from src.database.repository import engine

logger = logging.getLogger(__name__)


def clean_old_files(directory: str | Path, days: int = 30) -> int:
    """
    Elimina archivos en un directorio (y sus subdirectorios) cuya fecha de modificación
    sea superior a 'days' días.
    """
    dir_path = Path(directory)
    if not dir_path.is_absolute():
        dir_path = Path(settings.project_root) / dir_path

    if not dir_path.exists():
        return 0

    cutoff_time = time.time() - (days * 86400)
    removed_count = 0

    for item in dir_path.rglob("*"):
        if item.is_file():
            try:
                if item.stat().st_mtime < cutoff_time:
                    item.unlink()
                    removed_count += 1
                    logger.debug(f"Archivo eliminado por antigüedad (> {days}d): {item}")
            except Exception as e:
                logger.warning(f"No se pudo eliminar {item}: {e}")

    return removed_count


def clean_old_db_records(days: int = 30) -> dict[str, int]:
    """
    Elimina registros obsoletos de la base de datos:
    - CVSnapshots cuya fecha de creación sea > 'days' días o cuyos archivos PDF/TeX ya no existan.
    - Vacantes clasificadas como Tier 3 (descartes algorítmicos o por LLM) con más de 'days' días.
    """
    cutoff_date = datetime.now(tz=UTC) - timedelta(days=days)
    stats = {"cv_snapshots": 0, "tier3_jobs": 0}

    with Session(engine) as session:
        # 1. Limpiar CVSnapshots antiguos o con archivos inexistentes
        snapshots = session.exec(select(CVSnapshot)).all()
        for snap in snapshots:
            snap_date = snap.created_at
            if snap_date.tzinfo is None:
                snap_date = snap_date.replace(tzinfo=UTC)

            file_exists = os.path.exists(snap.pdf_path) or os.path.exists(snap.tex_path)
            if snap_date < cutoff_date or not file_exists:
                session.delete(snap)
                stats["cv_snapshots"] += 1

        # 2. Limpiar vacantes Tier 3 (descartes) antiguas
        old_tier3_jobs = session.exec(
            select(Job)
            .join(MatchResult)
            .where(MatchResult.tier == 3, Job.created_at < cutoff_date)
        ).all()

        for job in old_tier3_jobs:
            session.delete(job)
            stats["tier3_jobs"] += 1

        session.commit()

    return stats


def clean_all_temporary_data(days: int = 30) -> dict[str, int]:
    """
    Ejecuta el pipeline completo de purga de artefactos temporales
    y mantenimientos en base de datos.
    """
    logger.info(f"Iniciando depuración de datos temporales (antigüedad > {days} días)...")

    cvs_removed = clean_old_files(os.path.join("data", "generated_cvs"), days=days)
    interviews_removed = clean_old_files(os.path.join("data", "interview_prep"), days=days)
    tmp_removed = clean_old_files("tmp", days=days)

    db_stats = clean_old_db_records(days=days)

    summary = {
        "cv_files_removed": cvs_removed,
        "interview_files_removed": interviews_removed,
        "tmp_files_removed": tmp_removed,
        "db_cv_snapshots_removed": db_stats["cv_snapshots"],
        "db_tier3_jobs_removed": db_stats["tier3_jobs"],
    }

    logger.info(
        f"Depuración completada: {cvs_removed} CVs/TeX, {interviews_removed} Guías, "
        f"{tmp_removed} temporales en tmp, {db_stats['cv_snapshots']} snapshots en BD "
        f"y {db_stats['tier3_jobs']} vacantes descartadas eliminadas."
    )
    return summary
