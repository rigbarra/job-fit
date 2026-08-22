import logging
import re
from datetime import UTC, datetime

from config.loader import load_config
from src.database.models import Job

logger = logging.getLogger(__name__)

# Constante compartida: términos que identifican ubicaciones en Chile.
# Importar desde aquí en lugar de duplicar en cada módulo.
CHILE_TERMS = [
    "chile",
    "santiago",
    "vina",
    "viña",
    "valparaiso",
    "valparaíso",
    "concepcion",
    "concepción",
    "las condes",
    "providencia",
]


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
    location_lower = (job.location or "").lower()
    text_combined = f"{title} {location_lower} {description}".lower()

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

    # 4. Validar modalidad Híbrida / Presencial / Remota
    is_chile_location = any(term in location_lower for term in CHILE_TERMS)

    # A) Oferta Internacional (fuera de Chile): DEBE ser 100% remota y abierta a talento global
    if not is_chile_location:
        # Descartar si menciona requerimientos presenciales / híbridos en el extranjero
        hybrid_onsite_terms = [
            "hybrid",
            "híbrido",
            "hibrido",
            "on-site",
            "onsite",
            "on site",
            "in-office",
            "in office",
            "presencial",
            "in-person",
            "in person",
            "office-based",
            "relocation required",
            "must relocate",
        ]
        if any(term in text_combined for term in hybrid_onsite_terms):
            return (
                False,
                f"Descarte algorítmico: Oferta internacional en '{job.location}' requiere presencia híbrida/física (debe ser 100% remota).",
            )

        # Descartar si exige residencia obligatoria local o restricciones de visa doméstica en EE.UU./UK/etc.
        domestic_restriction_terms = [
            "must reside in the us",
            "must reside in the united states",
            "us only",
            "u.s. only",
            "must be a us citizen",
            "must be a u.s. citizen",
            "green card holder",
            "no visa sponsorship",
            "without visa sponsorship",
            "not eligible for visa sponsorship",
            "authorized to work in the us without",
            "authorized to work in the u.s. without",
            "authorized to work in the uk without",
            "security clearance",
        ]
        if any(term in text_combined for term in domestic_restriction_terms):
            return (
                False,
                f"Descarte algorítmico: Oferta en '{job.location}' exige autorización de trabajo local exclusiva (sin sponsorship/visa).",
            )

    # B) Oferta Local (Chile):
    # - Permitir 100% remota o híbrida general (o con 1 o 2 días presenciales).
    # - Descartar si es 100% presencial o si exige 3 o más días presenciales a la semana.
    else:
        # Descarte si es 100% presencial
        pure_onsite_terms = [
            "100% presencial",
            "100% presencialidad",
            "100% on-site",
            "100% onsite",
            "100% en oficina",
            "modalidad presencial",
        ]
        if any(term in text_combined for term in pure_onsite_terms):
            return (
                False,
                "Descarte algorítmico: Vacante en Chile descartada por ser 100% presencial.",
            )

        # Descarte si menciona 3, 4 o 5 días presenciales / en oficina
        pattern_3plus_days = r"\b([345]|tres|cuatro|cinco)\s*(días|dias|days)\s*(presenciales|de\s+presencialidad|en\s+oficina|on-site|onsite|in-office)\b"
        if re.search(pattern_3plus_days, text_combined):
            return (
                False,
                "Descarte algorítmico: Vacante en Chile exige 3 o más días presenciales por semana.",
            )

    return True, ""
