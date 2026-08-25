import copy
import json
import logging
import re
from pathlib import Path
from typing import Any

import jinja2
import yaml

from config.settings import settings
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
        path = settings.project_root / "config" / "profile.yaml"
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
            "name": raw_data.get("name", "Rigoberto Barra"),
            "phone": raw_data.get("phone", "+56-996974170"),
            "email": raw_data.get("email", "rigbarra@outlook.com"),
            "linkedin": raw_data.get("linkedin", "linkedin.com/in/rigbarra"),
            "skills": raw_data.get("skills", {}),
            "work_preferences": raw_data.get("work_preferences", {}),
            "title": lang_data.get("title", ""),
            "location": lang_data.get("location", ""),
            "summary": lang_data.get("summary", ""),
            "experience": lang_data.get("experience", []),
            "education": lang_data.get("education", []),
        }

    return raw_data


def build_cv_tex(
    match_result: MatchResult | None = None,
    language: str = "es",
    profile_override: dict[str, Any] | None = None,
) -> str:
    """
    Genera el código fuente LaTeX (.tex) completo inyectando los datos del perfil
    en el idioma correspondiente y aplicando las adaptaciones del LLM (si aplican para Tier 2).

    Args:
        match_result: Resultado de la evaluación LLM con textos adaptados (opcional).
        language: Idioma del CV a compilar ("es" o "en").
        profile_override: Datos de perfil para pruebas o personalizaciones específicas.

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

    # 3. Renderizar con Jinja2
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
        "name": profile.get("name", "Rigoberto Barra"),
        "phone": profile.get("phone", ""),
        "email": profile.get("email", ""),
        "linkedin": profile.get("linkedin", ""),
        "location": profile.get("location", ""),
        "title": title,
        "summary": summary,
        "experiences": experiences,
        "education": education,
        "skills": profile.get("skills", {}),
    }

    rendered_tex = template.render(**context)
    return rendered_tex
