import logging
from pathlib import Path

import yaml

from config.settings import settings
from src.database.models import Job

logger = logging.getLogger(__name__)


def load_filter_config() -> dict:
    """Carga los filtros algorítmicos desde config.yaml."""
    config_path = Path(settings.project_root) / "config" / "config.yaml"
    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
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
    except Exception as e:
        logger.error(f"Error cargando config de filtros algorítmicos: {e}")
        return {
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
        }


def should_evaluate_job(job: Job) -> tuple[bool, str]:
    """
    Evalúa algorítmicamente si una vacante merece ser analizada por el LLM.
    Retorna (True, "") si pasa el filtro, o (False, Rationale) si es descartada.
    """
    filter_config = load_filter_config()

    if not filter_config.get("enabled", True):
        return True, ""

    title = job.title.lower()
    description = job.description.lower()

    # 1. Validar palabras clave en el título (cualquiera de la lista)
    title_keywords = filter_config.get("title_keywords_any", [])
    title_matched = False

    if not title_keywords:
        title_matched = True
    else:
        for kw in title_keywords:
            if kw.lower() in title:
                title_matched = True
                break

    if not title_matched:
        return (
            False,
            f"Descarte algorítmico: El título '{job.title}' no contiene palabras clave de datos requeridas.",
        )

    # 2. Validar palabras clave obligatorias en la descripción (todas las de la lista)
    desc_keywords = filter_config.get("description_keywords_all", [])
    for kw in desc_keywords:
        kw_lower = kw.lower()
        if kw_lower not in description:
            return (
                False,
                f"Descarte algorítmico: La descripción no contiene la palabra clave obligatoria '{kw}'.",
            )

    return True, ""
