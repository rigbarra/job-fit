import logging
import re
from datetime import UTC, datetime

from config.settings import load_config
from src.agent.embedding import compute_semantic_similarity
from src.database.models import Job

logger = logging.getLogger(__name__)

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
    "ñuñoa",
    "nunoa",
    "recoleta",
    "quilicura",
    "buin",
    "antofagasta",
    "rancagua",
    "calama",
    "iquique",
    "valdivia",
    "puerto montt",
    "la serena",
    "coquimbo",
    "temuco",
    ", cl",
    "cl,",
    " rm",
    ", vs",
    ", bi",
    ", an",
]


def parse_salary_details(salary_str: str | None) -> tuple[float | None, float | None, str | None]:
    """Extrae valores numéricos min_salary, max_salary y moneda de una cadena de texto."""
    if not salary_str:
        return None, None, None

    clean_str = salary_str.replace(".", "").replace(",", "")
    nums = [float(n) for n in re.findall(r"\d+", clean_str)]
    if not nums:
        return None, None, None

    is_usd = "usd" in salary_str.lower() or max(nums) < 100000
    currency = "USD" if is_usd else "CLP"

    if len(nums) >= 2:
        return min(nums), max(nums), currency
    else:
        return nums[0], nums[0], currency


def extract_modality_and_country(location: str, description: str) -> tuple[str, str]:
    """Extrae la modalidad (Remoto 100%, Híbrido 2x3, Presencial, etc.) y país normalizado."""
    text = f"{location} {description}".lower()

    # País
    country = "Chile" if any(term in text for term in CHILE_TERMS) else "Internacional / Remote"

    # Modalidad
    if any(k in text for k in ["100% remoto", "remote", "remoto", "teletrabajo", "work from home", "wfh"]):
        if any(h in text for h in ["híbrido", "hibrido", "hybrid"]):
            modality = "Híbrido"
        else:
            modality = "Remoto 100%"
    elif any(k in text for k in ["híbrido", "hibrido", "hybrid"]):
        modality = "Híbrido"
        m = re.search(r"\b([1-4])\s*(x|por)\s*([1-4])\b", text)
        if m:
            modality = f"Híbrido ({m.group(1)}x{m.group(3)})"
    elif any(k in text for k in ["presencial", "on-site", "onsite", "en oficina"]):
        modality = "Presencial"
    else:
        modality = "Híbrido / Remoto"

    return modality, country


def is_salary_too_low(salary_str: str) -> tuple[bool, str]:
    """Determina si un rango de salario expresado en texto está por debajo de los mínimos ($2.5M CLP o $2500 USD)."""
    if not salary_str:
        return False, ""

    # Extraer todos los números eliminando separadores de miles
    clean_str = salary_str.replace(".", "").replace(",", "")
    nums = [int(n) for n in re.findall(r"\d+", clean_str)]
    if not nums:
        return False, ""

    max_val = max(nums)
    # Si tiene la palabra usd o el valor es muy bajo, asumimos USD. De lo contrario CLP.
    is_usd = "usd" in salary_str.lower() or max_val < 100000

    if is_usd:
        if max_val < 2500:
            return True, f"Salario máximo de {max_val} USD es menor a 2500 USD."
    else:
        if max_val < 2500000:
            return True, f"Salario máximo de {max_val} CLP es menor a 2.500.000 CLP."

    return False, ""


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
            "description_keywords_all": ["sql", "python"],
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

    # 1. Lista Negra de Títulos (Roles excluidos y cargos de gerencia/liderazgo de equipos)
    title_blacklist = [
        "cientifico de datos",
        "cientista de datos",
        "data science",
        "data scientist",
        "machine learning",
        "ml engineer",
        "arquitecto datos",
        "control de gestion",
        "junior",
        "jr",
    ]
    for term in title_blacklist:
        if term in title:
            return False, f"Descarte algorítmico: El título contiene término excluido '{term}'."

    # 2. Validar palabras clave en el título (cualquiera de la lista permitida)
    title_keywords = filter_config.get("title_keywords_any", [])
    if title_keywords:
        matches_any = False
        for kw in title_keywords:
            kw_clean = kw.lower()
            if len(kw_clean) <= 2:
                if re.search(r"\b" + re.escape(kw_clean) + r"\b", title):
                    matches_any = True
                    break
            elif kw_clean in title:
                matches_any = True
                break

        if not matches_any:
            return (
                False,
                f"Descarte algorítmico: El título '{job.title}' no contiene palabras clave de datos requeridas.",
            )

    # 3. Validar palabras clave en la descripción (al menos una — OR logic)
    # Se usa OR en vez de AND para no descartar avisos con descripciones cortas o truncadas de Indeed.
    desc_keywords = filter_config.get(
        "description_keywords_any",
        filter_config.get("description_keywords_all", ["sql", "python", "data", "datos"]),
    )
    if not any(kw.lower() in description for kw in desc_keywords):
        return (
            False,
            "Descarte algorítmico: La descripción no contiene ninguna palabra clave de datos.",
        )

    # 4. Descarte de experiencia junior/recién egresado (0 a 2 años de experiencia)
    junior_regexes = [
        r"\b0\s*(a|-|to)\s*2\s*(años|anios|years)\b",
        r"\b(recién|recien)\s+(egresado|titulado)\b",
        r"\bsin\s+experiencia\b",
        r"\bentry\s*level\b",
        r"\bno\s+experience\s+required\b",
    ]
    for pattern in junior_regexes:
        if re.search(pattern, description, re.IGNORECASE):
            return False, f"Descarte algorítmico: Oferta dirigida a perfiles junior (menciona '{pattern}')."

    # 5. Filtrar por Salario mínimo
    too_low, salary_reason = is_salary_too_low(job.salary)
    if too_low:
        return False, f"Descarte algorítmico: {salary_reason}"

    # 6. Validar antigüedad máxima de la oferta en días
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
                f"Descarte algorítmico: La oferta fue publicada hace {int(age_days)} días (máximo: {max_age_days} días).",
            )

    # 7. Validar modalidad Híbrida / Presencial / Remota
    is_chile_location = any(term in location_lower for term in CHILE_TERMS)

    # A) Oferta Internacional: DEBE ser 100% remota y abierta a talento global
    if not is_chile_location:
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
                f"Descarte algorítmico: Oferta internacional en '{job.location}' requiere presencia híbrida/física.",
            )

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
                f"Descarte algorítmico: Oferta en '{job.location}' exige residencia local obligatoria.",
            )

    # B) Oferta Local (Chile)
    else:
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

        pattern_3plus_days = r"\b([345]|tres|cuatro|cinco)\s*(días|dias|days)\s*(presenciales|de\s+presencialidad|en\s+oficina|on-site|onsite|in-office)\b"
        if re.search(pattern_3plus_days, text_combined):
            return (
                False,
                "Descarte algorítmico: Vacante en Chile exige 3 o más días presenciales por semana.",
            )

    # 8. Validar Similitud Semántica Vectorial Local (0 Tokens LLM)
    min_semantic_score = filter_config.get("min_semantic_score", 55.0)
    if min_semantic_score and min_semantic_score > 0:
        job_full_text = f"{job.title}. {job.description}"
        sim_pts = compute_semantic_similarity(job_full_text)
        if sim_pts is not None and sim_pts < min_semantic_score:
            return (
                False,
                f"Descarte algorítmico (Vector Semántico Local): Similitud semántica de {sim_pts:.1f} pts es menor al umbral de {min_semantic_score:.1f} pts.",
            )

    return True, ""
