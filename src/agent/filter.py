import logging
import re
import unicodedata
from datetime import UTC, datetime

from config.settings import load_config
from src.agent.embedding import compute_semantic_similarity
from src.database.models import Job

logger = logging.getLogger(__name__)


def normalize_text(text: str) -> str:
    """Convierte texto a minúsculas y elimina tildes/diacríticos (estándar único de limpieza)."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


CHILE_TERMS = [
    "chile",
    "santiago",
    "vina",
    "valparaiso",
    "concepcion",
    "las condes",
    "providencia",
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
    ", region metropolitana",
    ", v region",
    ", viii region",
    ", ii region",
]


def parse_salary_details(salary_str: str) -> tuple[float | None, float | None, str | None]:
    """Extrae min, max y moneda (CLP o USD) desde un string de salario con validación estricta de contexto monetario."""
    if not salary_str:
        return None, None, None

    s_lower = salary_str.lower()

    # Rechazar explícitamente si el número corresponde a métricas operacionales (clientes, usuarios, etc.)
    non_monetary_metrics = [
        "cliente",
        "usuario",
        "transacci",
        "registro",
        "solicitud",
        "llamada",
        "habitante",
        "consulta",
        "hora",
        "dia",
        "semana",
        "visita",
        "descarga",
    ]
    if any(m in s_lower for m in non_monetary_metrics):
        return None, None, None

    # Normalizar centavos estadounidenses (.00 o ,00)
    s = re.sub(r"\.00\b", "", salary_str)
    s = re.sub(r",00\b", "", s)

    clean_str = s.replace(".", "").replace(",", "")
    nums = [float(n) for n in re.findall(r"\d+", clean_str)]
    if not nums:
        return None, None, None

    is_usd = "usd" in s_lower or max(nums) < 100000
    currency = "USD" if is_usd else "CLP"

    if len(nums) >= 2:
        return min(nums), max(nums), currency
    else:
        return nums[0], nums[0], currency


def extract_modality_and_country(location: str, description: str, source: str = "") -> tuple[str, str, str]:
    """Extrae la modalidad (Remoto 100%, Híbrido 2x3, Presencial, etc.), país normalizado y tipo de origen."""
    loc_lower = normalize_text(location)
    text = normalize_text(f"{location} {description}")

    # País y Origen
    is_chile_loc = any(term in loc_lower for term in CHILE_TERMS) or "remote_local" in loc_lower
    is_foreign_remote = any(r in loc_lower for r in ["remote_global", "remote - latin america", "latin america", "worldwide", "global"]) or source.lower() == "remotive"

    if is_chile_loc or (source.lower() == "getonboard" and not is_foreign_remote):
        country = "Chile"
        origin_type = "Chile (Empresa Local)"
    else:
        country = "Chile" if is_chile_loc else "Internacional / Remote"
        origin_type = "Internacional / LATAM (Remoto)"

    # Modalidad (text ya está normalizado sin tildes por normalize_text)
    if any(k in text for k in ["100% remoto", "100% remota", "remote", "remoto", "remota", "teletrabajo", "work from home", "wfh"]):
        if any(h in text for h in ["hibrido", "hibrida", "hybrid"]):
            modality = "Híbrido"
        else:
            modality = "Remoto 100%"
    elif any(k in text for k in ["hibrido", "hibrida", "hybrid"]):
        modality = "Híbrido"
        m = re.search(r"\b([1-4])\s*(x|por)\s*([1-4])\b", text)
        if m:
            modality = f"Híbrido ({m.group(1)}x{m.group(3)})"
    elif any(k in text for k in ["presencial", "on-site", "onsite", "en oficina"]):
        modality = "Presencial"
    else:
        # Sin mención explícita de modalidad → presencial por defecto.
        # ponytail: no asumir remoto si la oferta no lo dice.
        modality = "Presencial"

    return modality, country, origin_type


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

    title = normalize_text(job.title)
    description = normalize_text(job.description)
    location_lower = normalize_text(job.location)
    text_combined = f"{title} {location_lower} {description}"

    # 1. Lista Negra de Títulos Excluidos (Control de Gestión tradicional, Ciencia de Datos pura, Jr, etc.)
    title_blacklist = [
        "control de gestion",
        "control de gestión",
        "controller",
        "analista de procesos",
        "analista de operaciones",
        "analista de calidad",
        "analista contable",
        "analista de gestion",
        "analista de gestión",
        "gestion y procesos",
        "gestión y procesos",
        "pmo",
        "rrhh",
        "recursos humanos",
        "seleccion",
        "selección",
        "abastecimiento",
        "adquisiciones",
        "cientifico de datos",
        "cientista de datos",
        "data science",
        "data scientist",
        "machine learning",
        "ml engineer",
        "arquitecto datos",
        "junior",
        "jr",
    ]
    for term in title_blacklist:
        if term in title:
            return False, f"Descarte algorítmico: El título contiene término excluido '{term}'."

    # 1.5. Exclusión Estricta de Cargos de Liderazgo / Gerencia (Manager, Lead, Jefe, Director, Head of)
    leadership_terms = [
        "manager",
        "lead",
        "lider",
        "líder",
        "jefe",
        "jefa",
        "director",
        "directora",
        "head of",
    ]
    for term in leadership_terms:
        if term in title:
            return (
                False,
                f"Descarte algorítmico: Título contiene rol de liderazgo excluido '{term}'.",
            )

    # 2. Validar palabras clave en el título (cualquiera de la lista permitida)
    title_keywords = filter_config.get("title_keywords_any", [])
    if title_keywords:
        matches_any = False
        clean_title = re.sub(r"[/()_-]", " ", title)
        title_variants = [title, clean_title]
        for kw in title_keywords:
            kw_clean = kw.lower()
            if len(kw_clean) <= 2:
                if any(re.search(r"\b" + re.escape(kw_clean) + r"\b", t) for t in title_variants):
                    matches_any = True
                    break
            elif any(kw_clean in t for t in title_variants):
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

    # 6. Validar antigüedad máxima de la oferta en días (ventana móvil)
    max_age_days = float(config.get("search_filters", {}).get("max_job_age_days", 1))
    if job.posted_at:
        now = datetime.now(tz=UTC)
        posted_at = job.posted_at
        if isinstance(posted_at, str):
            try:
                posted_at = datetime.fromisoformat(posted_at)
            except Exception:
                posted_at = None
        if posted_at:
            if posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=UTC)

            # Si posted_at no incluye hora (medianoche 00:00:00 de LinkedIn/Indeed),
            # calculamos días calendario para evitar que la hora del día convierta 1 día en 1.8 días.
            if posted_at.hour == 0 and posted_at.minute == 0 and posted_at.second == 0:
                age_days = float((now.date() - posted_at.date()).days)
            else:
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
            "us based",
            "u.s. based",
            "must be located in the us",
            "must be based in the us",
            "must be based in the u.s.",
            "us remote",
            "u.s. remote",
            "remote in the us",
            "remote (us)",
            "remote us",
            "remote - us",
            "remote - usa",
            "w2 only",
            "w-2 only",
            "no c2c",
            "no corp-to-corp",
            "no 1099",
            "no contractors",
            "must be a us citizen",
            "must be a u.s. citizen",
            "green card",
            "no visa sponsorship",
            "without visa sponsorship",
            "not eligible for visa sponsorship",
            "authorized to work in the us",
            "authorized to work in the u.s.",
            "authorized to work in the uk",
            "security clearance",
            "reside in spain",
            "reside en espana",
            "permiso de trabajo",
            "eu only",
            "emea only",
        ]
        if any(term in text_combined for term in domestic_restriction_terms):
            return (
                False,
                f"Descarte algorítmico: Oferta en '{job.location}' exige residencia/permiso de trabajo local en el extranjero.",
            )

        loc_lower_clean = normalize_text(job.location)
        
        # 1. ¿Es una vacante con ubicación puramente remota global o LATAM?
        generic_remote_loc = loc_lower_clean in ["remote", "remoto", "100% remote", "100% remoto", "worldwide", "global", "latin america", "latam", "anywhere"]
        open_geo_in_loc = any(r in loc_lower_clean for r in ["worldwide", "latin america", "latam", "global", "anywhere", "chile"])
        open_geo_in_desc = any(
            r in text_combined for r in [
                "remote - latam",
                "remote - latin america",
                "remote - worldwide",
                "remote - global",
                "remote (latam)",
                "remote (worldwide)",
                "remote (global)",
                "hiring in latam",
                "candidates in latam",
                "residentes en latam",
                "latin america remote",
                "remoto latam",
                "remoto latinoamerica",
                "remoto en latam",
                "hispanoamerica",
                "latinoamerica",
                "remoto hispanoamerica",
                "remoto en cualquier parte",
                "cualquier parte de latam",
            ]
        )

        open_geo_match = generic_remote_loc or open_geo_in_loc or open_geo_in_desc

        # 2. ¿Especifica modalidad Contractor / B2B explícitamente?
        contractor_match = any(
            c in text_combined for c in [
                "contractor",
                "independent contractor",
                "b2b",
                "1099",
                "c2c",
                "corp-to-corp",
                "corp to corp",
                "deel",
                "ontop",
                "remote.com",
                "rippling",
                "contract position",
                "contract role",
                "prestacion de servicios",
                "honorarios",
            ]
        )

        if not (open_geo_match or contractor_match):
            return (
                False,
                f"Descarte algorítmico: Oferta internacional en '{job.location}' no especifica contratación B2B/Contractor ni apertura explícita para LATAM/Chile/Worldwide.",
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

        # Regla Innegociable para Santiago / Región Metropolitana:
        # El candidato reside en Viña del Mar y rechaza traslados presenciales diarios a Santiago.
        # Por ende, una vacante localizada en Santiago/RM DEBE ser Remota o Híbrida.
        is_santiago = any(stgo in location_lower for stgo in [
            "santiago", "region metropolitana", "región metropolitana", "las condes", 
            "providencia", "huechuraba", "quilicura", "pudahuel", "san bernardo", 
            "ciudad empresarial", "vitacura", "lo barnechea"
        ])
        
        has_remote_or_hybrid = any(term in text_combined for term in [
            "100% remoto", "100% remota", "remoto", "remota", "teletrabajo", 
            "home office", "wfh", "work from home", "hibrido", "hibrida", "hybrid"
        ])

        if is_santiago and not has_remote_or_hybrid:
            return (
                False,
                "Descarte algorítmico: Vacante en Santiago/RM descartada por no especificar modalidad Remota o Híbrida (presencial inviable desde Viña del Mar).",
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
