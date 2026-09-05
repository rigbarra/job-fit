import copy
import json
import logging
import re
from pathlib import Path
from typing import Any

import jinja2
import yaml

from config.settings import settings, get_profile_path
from src.database.models import MatchResult

logger = logging.getLogger(__name__)


def escape_latex(text: str) -> str:
    """
    Escapa caracteres especiales de LaTeX en cadenas de texto dinámicas (LLM / usuario)
    para evitar errores de compilación con pdflatex.
    """
    if not text or not isinstance(text, str):
        return ""

    # Reemplazo de caracteres especiales que no estén ya escapados
    # %, &, $, #, _
    text = re.sub(r"(?<!\\)%", r"\%", text)
    text = re.sub(r"(?<!\\)&", r"\&", text)
    text = re.sub(r"(?<!\\)\$", r"\$", text)
    text = re.sub(r"(?<!\\)#", r"\#", text)
    text = re.sub(r"(?<!\\)_", r"\_", text)

    # Comillas tipográficas
    text = text.replace("“", "``").replace("”", "''").replace('"', "''")

    return text


def get_jinja_env(templates_dir: str | None = None) -> jinja2.Environment:
    """
    Configura el entorno Jinja2 con delimitadores personalizados compatibles con LaTeX
    para evitar colisiones con las llaves {} de TeX.
    """
    if not templates_dir:
        templates_path = settings.project_root / "templates" / "cv"
    else:
        templates_path = Path(templates_dir)

    env = jinja2.Environment(
        block_start_string=r"\BLOCK{",
        block_end_string=r"}",
        variable_start_string=r"\VAR{",
        variable_end_string=r"}",
        comment_start_string=r"\#{",
        comment_end_string=r"}",
        line_statement_prefix=r"%%",
        line_comment_prefix=r"%#",
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,
        loader=jinja2.FileSystemLoader(str(templates_path)),
    )
    env.filters["latex_escape"] = escape_latex
    return env


def load_profile(language: str = "es", profile_path: str | None = None) -> dict[str, Any]:
    """
    Carga los datos del perfil profesional desde el archivo YAML en el idioma especificado ('es' o 'en').
    """
    if not profile_path:
        path = get_profile_path()
    else:
        path = Path(profile_path)

    try:
        with open(path, encoding="utf-8") as f:
            raw_data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Error cargando perfil en {path}: {e}")
        raise FileNotFoundError(f"No se pudo cargar el perfil del candidato: {path}")

    lang_key = "en" if language.lower() in ("en", "english", "ingles") else "es"

    # Si el YAML contiene secciones bilingües 'es' y 'en'
    if lang_key in raw_data:
        lang_data = raw_data[lang_key]
        return {
            "name": raw_data.get("name", "Candidato"),
            "phone": raw_data.get("phone", ""),
            "email": raw_data.get("email", ""),
            "linkedin": raw_data.get("linkedin", ""),
            "skills": raw_data.get("skills", {}),
            "work_preferences": raw_data.get("work_preferences", {}),
            "title": lang_data.get("title", ""),
            "location": lang_data.get("location", ""),
            "summary": lang_data.get("summary", ""),
            "experience": lang_data.get("experience", []),
            "education": lang_data.get("education", []),
        }

    return raw_data


def prepare_skills_list(
    profile: dict[str, Any],
    language: str = "es",
    job: Any | None = None,
    match_result: MatchResult | None = None,
) -> list[dict[str, str]]:
    """
    Parsea las habilidades desde profile.yaml y las reordena/adapta dinámicamente
    según las tecnologías y requisitos clave de la vacante.
    """
    raw_skills = profile.get("skills", {})
    lang_key = "en" if language.lower() in ("en", "english", "ingles") else "es"
    is_es = lang_key == "es"

    formatted_skills = []
    if isinstance(raw_skills, dict):
        for cat_id, cat_data in raw_skills.items():
            if isinstance(cat_data, dict):
                name = cat_data.get("category_es" if is_es else "category_en", cat_id)
                details = cat_data.get("items", "")
            else:
                name = str(cat_id).replace("_", " ").title()
                details = ", ".join(cat_data) if isinstance(cat_data, list) else str(cat_data)

            formatted_skills.append({
                "id": str(cat_id),
                "name": escape_latex(name),
                "details": escape_latex(details),
                "priority": 10,
            })

    # Si hay información de la vacante o de la evaluación, adaptar el orden de prioridad
    if job or match_result:
        job_text = ""
        if job and getattr(job, "title", None) and getattr(job, "description", None):
            job_text += f"{job.title} {job.description}".lower()
        if match_result and getattr(match_result, "rationale", None):
            job_text += f" {match_result.rationale}".lower()

        for item in formatted_skills:
            cid = item["id"]
            if cid == "ai_engineering" and any(k in job_text for k in ["ai", "llm", "rag", "genai", "gpt", "inteligencia artificial", "agente"]):
                item["priority"] = 1
            elif cid == "bi_analytics" and any(k in job_text for k in ["bi", "power bi", "tableau", "looker", "visualization", "dashboard", "report"]):
                item["priority"] = 2
            elif cid == "data_engineering" and any(k in job_text for k in ["data engineer", "pipeline", "etl", "dbt", "sql", "pyspark"]):
                item["priority"] = 3
            elif cid == "cloud_bigdata" and any(k in job_text for k in ["aws", "gcp", "azure", "cloud", "emr", "bigquery", "redshift"]):
                item["priority"] = 4

        formatted_skills.sort(key=lambda x: x["priority"])

    return formatted_skills


def build_cv_tex(
    match_result: MatchResult | None = None,
    language: str = "es",
    profile_override: dict[str, Any] | None = None,
    job: Any | None = None,
) -> str:
    """
    Genera el código fuente LaTeX (.tex) completo inyectando los datos del perfil
    en el idioma correspondiente y aplicando las adaptaciones del LLM (si aplican para Tier 2).

    Args:
        match_result: Resultado de la evaluación LLM con textos adaptados (opcional).
        language: Idioma del CV a compilar ("es" o "en").
        profile_override: Datos de perfil para pruebas o personalizaciones específicas.
        job: Vacante objetivo para adaptar el orden de las habilidades técnicas (opcional).

    Returns:
        str: Contenido del archivo LaTeX listo para compilar.
    """
    lang_key = "en" if language.lower() in ("en", "english", "ingles") else "es"
    profile = copy.deepcopy(profile_override or load_profile(language=lang_key))

    title = profile.get("title", "")
    summary = profile.get("summary", "")
    experiences: list[dict[str, Any]] = profile.get("experience", [])
    education: list[dict[str, Any]] = profile.get("education", [])

    # 1. Adaptar título principal si existe en match_result (Tier 2)
    if match_result and getattr(match_result, "adapted_title", None):
        logger.info("Inyectando título profesional adaptado por el LLM en la plantilla LaTeX.")
        title = escape_latex(match_result.adapted_title)
    else:
        title = escape_latex(title)

    # 2. Adaptar resumen profesional si existe en match_result (Tier 2)
    if match_result and match_result.adapted_summary:
        logger.info("Inyectando resumen adaptado por el LLM en la plantilla LaTeX.")
        summary = escape_latex(match_result.adapted_summary)
    else:
        summary = escape_latex(summary)

    # 3. Conservar viñetas de experiencia 100% originales (respetando métricas y tono original)
    for exp in experiences:
        exp["role"] = escape_latex(exp.get("role", ""))
        exp["company"] = escape_latex(exp.get("company", ""))
        exp["period"] = escape_latex(exp.get("period", ""))
        exp["location"] = escape_latex(exp.get("location", ""))
        if "company_description" in exp:
            exp["company_description"] = escape_latex(exp.get("company_description", ""))

        exp["bullets"] = [escape_latex(b) for b in exp.get("bullets", [])]

    # Procesar educación
    for edu in education:
        edu["degree"] = escape_latex(edu.get("degree", ""))
        edu["school"] = escape_latex(edu.get("school", ""))
        edu["period"] = escape_latex(edu.get("period", ""))
        edu["location"] = escape_latex(edu.get("location", ""))

    # 4. Preparar lista dinámica de habilidades técnicas ordenadas por relevancia
    skills_list = prepare_skills_list(profile, language=lang_key, job=job, match_result=match_result)

    # 5. Renderizar con Jinja2
    env = get_jinja_env()
    template_name = "cv_base_en.tex" if lang_key == "en" else "cv_base_es.tex"

    try:
        template = env.get_template(template_name)
    except Exception as e:
        logger.warning(
            f"Plantilla {template_name} no encontrada, usando cv_base.tex como fallback: {e}"
        )
        template = env.get_template("cv_base.tex")

    context = {
        "name": escape_latex(profile.get("name", "Candidato")),
        "phone": escape_latex(profile.get("phone", "")),
        "email": escape_latex(profile.get("email", "")),
        "linkedin": escape_latex(profile.get("linkedin", "")),
        "location": escape_latex(profile.get("location", "")),
        "title": title,
        "summary": summary,
        "experiences": experiences,
        "education": education,
        "skills_list": skills_list,
    }

    rendered_tex = template.render(**context)
    return rendered_tex
