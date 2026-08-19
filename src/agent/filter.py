import logging
from datetime import UTC, datetime

from config.loader import load_config
from src.database.models import Job

logger = logging.getLogger(__name__)


def _get_filter_config() -> dict:
    """Obtiene los filtros algorítmicos desde la configuración cacheada."""
    config = load_config()
    return config.get(
        "algorithmic_filter",
        {
            "enabled": True,
            "title_keywords_any": [
                "data",
                "analytics",
                "bi",
                "dbt",
                "etl",
                "pipeline",
                "intelligence",
                "datos",
                "analista",
                "ingeniero",
            ],
            "description_keywords_all": ["sql"],
        },
    )


def should_evaluate_job(job: Job) -> tuple[bool, str]:
    """
    Evalúa algorítmicamente si una vacante merece ser analizada por el LLM.
    Retorna (True, "") si pasa el filtro, o (False, Rationale) si es descartada.
    """
    filter_config = _get_filter_config()
    config = load_config()

    if not filter_config.get("enabled", True):
        return True, ""

    title = job.title.lower()
    description = job.description.lower()

    # 1. Validar palabras clave en el título (cualquiera de la lista)
    title_keywords = filter_config.get("title_keywords_any", [])

    if title_keywords and not any(kw.lower() in title for kw in title_keywords):
        return (
            False,
            f"Descarte algorítmico: El título '{job.title}' no contiene palabras clave de datos requeridas.",
        )

    # 2. Validar palabras clave obligatorias en la descripción (todas las de la lista)
    for kw in filter_config.get("description_keywords_all", []):
        if kw.lower() not in description:
            return (
                False,
                f"Descarte algorítmico: La descripción no contiene la palabra clave obligatoria '{kw}'.",
            )

    # 3. Validar antigüedad máxima de la oferta en días
    max_age_days = config.get("search_filters", {}).get("max_job_age_days", 3)
    if job.posted_at:
        now = datetime.now(tz=UTC)
        posted_at = job.posted_at
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=UTC)

        age_days = (now - posted_at).total_seconds() / 86400.0
        if age_days > max_age_days:
            return (
                False,
                f"Descarte algorítmico: La oferta fue publicada hace {int(age_days)} días (máximo permitido: {max_age_days} días).",
            )

    return True, ""
