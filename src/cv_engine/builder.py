import json
import logging
import re
import copy
from pathlib import Path
from typing import Optional, Dict, Any, List
import yaml
import jinja2

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
    # 1. Backslash (si es texto plano)
    # 2. %, &, $, #, _
    # Usamos regex para no volver a escapar lo que ya tiene una barra inversa antes
    text = re.sub(r'(?<!\\)%', r'\%', text)
    text = re.sub(r'(?<!\\)&', r'\&', text)
    text = re.sub(r'(?<!\\)\$', r'\$', text)
    text = re.sub(r'(?<!\\)#', r'\#', text)
    text = re.sub(r'(?<!\\)_', r'\_', text)
    
    # Comillas tipográficas
    text = text.replace('“', "``").replace('”', "''").replace('"', "''")
    
    return text

def get_jinja_env(templates_dir: Optional[str] = None) -> jinja2.Environment:
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
        loader=jinja2.FileSystemLoader(str(templates_path))
    )
    env.filters["latex_escape"] = escape_latex
    return env

def load_profile(profile_path: Optional[str] = None) -> Dict[str, Any]:
    """Carga los datos del perfil profesional desde el archivo YAML."""
    if not profile_path:
        path = settings.project_root / "config" / "profile.yaml"
    else:
        path = Path(profile_path)
        
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"Error cargando perfil en {path}: {e}")
        raise FileNotFoundError(f"No se pudo cargar el perfil del candidato: {path}")

def build_cv_tex(
    match_result: Optional[MatchResult] = None, 
    language: str = "es", 
    profile_override: Optional[Dict[str, Any]] = None
) -> str:
    """
    Genera el código fuente LaTeX (.tex) completo inyectando los datos del perfil
    y las adaptaciones del LLM (si aplican para Tier 2).
    
    Args:
        match_result: Resultado de la evaluación LLM con textos adaptados (opcional).
        language: Idioma del CV a compilar ("es" o "en").
        profile_override: Datos de perfil para pruebas o personalizaciones específicas.
        
    Returns:
        str: Contenido del archivo LaTeX listo para compilar.
    """
    profile = copy.deepcopy(profile_override or load_profile())
    
    summary = profile.get("summary", "")
    experiences: List[Dict[str, Any]] = profile.get("experience", [])
    education: List[Dict[str, Any]] = profile.get("education", [])
    
    # 1. Si existe evaluación de Tier 2, inyectar el resumen adaptado
    if match_result and match_result.adapted_summary:
        logger.info("Inyectando resumen adaptado por el LLM en la plantilla LaTeX.")
        summary = escape_latex(match_result.adapted_summary)
    else:
        summary = escape_latex(summary)
        
    # 2. Si existen viñetas adaptadas, reemplazar en las experiencias correspondientes
    adapted_bullets_map: Dict[str, str] = {}
    if match_result and match_result.adapted_bullets:
        try:
            if isinstance(match_result.adapted_bullets, str):
                adapted_bullets_map = json.loads(match_result.adapted_bullets)
            elif isinstance(match_result.adapted_bullets, dict):
                adapted_bullets_map = match_result.adapted_bullets
        except Exception as e:
            logger.warning(f"No se pudo parsear adapted_bullets como JSON: {e}")

    # Procesar viñetas de experiencia
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
            # Verificar si esta viñeta fue adaptada por el LLM
            for original_key, adapted_val in adapted_bullets_map.items():
                if original_key.lower() in bullet.lower():
                    logger.info(f"Reemplazando viñeta adaptada en '{exp['company']}': {original_key[:30]}...")
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
    template_name = "cv_base_en.tex" if language.lower() in ("en", "english", "ingles") else "cv_base_es.tex"
    
    try:
        template = env.get_template(template_name)
    except Exception as e:
        logger.warning(f"Plantilla {template_name} no encontrada, usando cv_base.tex como fallback: {e}")
        template = env.get_template("cv_base.tex")

    context = {
        "name": profile.get("name", "Rigoberto Barra"),
        "title": profile.get("title", "Analytics Engineer / Data Engineer"),
        "summary": summary,
        "experiences": experiences,
        "education": education,
        "skills": profile.get("skills", {})
    }
    
    rendered_tex = template.render(**context)
    return rendered_tex
