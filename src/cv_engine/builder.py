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

    summary = profile.get("summary", "")
    experiences: list[dict[str, Any]] = profile.get("experience", [])
    education: list[dict[str, Any]] = profile.get("education", [])

    # 1. Si existe evaluación de Tier 2, inyectar el resumen adaptado
    if match_result and match_result.adapted_summary:
        logger.info("Inyectando resumen adaptado por el LLM en la plantilla LaTeX.")
        summary = escape_latex(match_result.adapted_summary)
    else:
        summary = escape_latex(summary)

    # 2. Si existen viñetas adaptadas, reemplazar en las experiencias correspondientes
    adapted_bullets_map: dict[str, str] = {}
    if match_result and match_result.adapted_bullets:
        try:
            if isinstance(match_result.adapted_bullets, str):
                adapted_bullets_map = json.loads(match_result.adapted_bullets)
            elif isinstance(match_result.adapted_bullets, dict):
                adapted_bullets_map = match_result.adapted_bullets
        except Exception as e:
            logger.warning(f"No se pudo parsear adapted_bullets como JSON: {e}")

    # Procesar viñetas de experiencia con matching robusto
    for exp in experiences:
        exp["role"] = escape_latex(exp.get("role", ""))
        exp["company"] = escape_latex(exp.get("company", ""))
        exp["period"] = escape_latex(exp.get("period", ""))
        exp["location"] = escape_latex(exp.get("location", ""))
        if "company_description" in exp:
            exp["company_description"] = escape_latex(exp.get("company_description", ""))

        processed_bullets = []
        for bullet in exp.get("bullets", []):
            final_bullet = bullet
            bullet_clean = bullet.lower().strip()

            # Verificar si esta viñeta fue adaptada por el LLM
            for original_key, adapted_val in adapted_bullets_map.items():
                orig_clean = original_key.lower().strip()
                # Coincidencia flexible: substring, inclusión inversa o prefijo de 25 caracteres
                if (
                    orig_clean in bullet_clean
                    or bullet_clean in orig_clean
                    or (len(orig_clean) >= 20 and orig_clean[:25] in bullet_clean)
                    or (len(bullet_clean) >= 20 and bullet_clean[:25] in orig_clean)
                ):
                    logger.info(
                        f"Reemplazando viñeta adaptada en '{exp['company']}': {original_key[:35]}..."
                    )
                    final_bullet = adapted_val
                    break
            processed_bullets.append(escape_latex(final_bullet))
        exp["bullets"] = processed_bullets

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
        "title": profile.get("title", "Analytics Engineer / Data Engineer"),
        "summary": summary,
        "experiences": experiences,
        "education": education,
        "skills": profile.get("skills", {}),
    }

    rendered_tex = template.render(**context)
    return rendered_tex
