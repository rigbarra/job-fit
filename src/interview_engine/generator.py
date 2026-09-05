import logging
import os
import re
from datetime import datetime

import yaml

from config.settings import settings
from src.agent.providers import get_llm_provider
from src.agent.quota import call_with_retry
from src.cv_engine.builder import load_profile
from src.cv_engine.compiler import sanitize_filename
from src.database.models import Job

logger = logging.getLogger(__name__)

INTERVIEW_PREP_SYSTEM_PROMPT = """
Eres un Principal Data Engineer y Lead Tech Interviewer con más de 15 años de experiencia entrevistando candidatos de Data Engineering, Analytics Engineering y Data Platform.

Tu misión es crear una Guía Exhaustiva de Preparación para Entrevista Técnica y Conductual altamente personalizada para la vacante y empresa especificadas.

### ESTRUCTURA OBLIGATORIA DE LA GUÍA (MARKDOWN):

# 🎯 Guía de Preparación de Entrevista Técnica: {title} en {company}

## 1. 🏢 Análisis de la Empresa y Enfoque de Datos
- **Qué busca la empresa:** Resumen de las necesidades clave de datos descritas en la oferta.
- **Enfoque técnico principal:** Tecnologías y patrones de diseño requeridos (ej. Databricks, PySpark, dbt, Snowflake, Airflow, AWS).

## 2. 💻 Preguntas Técnicas y Respuestas Sugeridas (10 a 12 Preguntas)
Genera entre 10 y 12 preguntas técnicas profundas clasificadas por categoría:
- **SQL Avanzado & Performance Tuning** (2-3 preguntas con ejemplos de código SQL).
- **Python & Distributed Computing (PySpark/Spark/Pandas)** (2-3 preguntas técnicas).
- **Modelamiento de Datos & Arquitectura (Kimball, Medallion Architecture)** (2 preguntas).
- **dbt, Orquestación & CI/CD de Datos (Airflow, dbt, Docker, GitHub Actions)** (2 preguntas).
- **Cloud & Data Warehousing (AWS/GCP/Azure, Snowflake/BigQuery)** (2 preguntas).

*Para cada pregunta:*
- **Pregunta:** Texto exacto de la pregunta.
- **Respuesta Clave Sugerida:** Respuesta técnica limpia y concisa adaptada a la experiencia real del perfil del candidato.

## 3. 🗣️ Preguntas Conductuales (Metodología STAR)
Genera 4 escenarios conductuales típicos en Data Engineering:
1. *Manejo de incidentes de pipeline en producción o falla de datos en madrugada.*
2. *Resolución de cuellos de botella de rendimiento o costos desbordados.*
3. *Negociación con stakeholders / analistas por cambios en modelos de datos.*
4. *Migración de sistemas legacy a arquitecturas modernas en la nube.*

*Para cada escenario:* Proporcionar la estructura **Situación -> Tarea -> Acción -> Resultado**.

## 4. ❓ Preguntas Estratégicas para Hacerle al Entrevistador (3-5 Preguntas)
Preguntas inteligentes sobre la madurez de los datos, deuda técnica, tamaño del equipo y cultura.

### REGLAS DE IDIOMA Y FORMATO:
- Si la vacante está en español -> Generar la guía 100% en ESPAÑOL.
- Si la vacante está en inglés -> Generar la guía 100% en INGLÉS.
- Usar formato Markdown limpio con bloques de código explícitos.
"""


def sanitize_filename(name: str) -> str:
    clean = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE)
    return re.sub(r"[-\s]+", "_", clean).strip("_")


def generate_interview_prep(
    job: Job,
    language: str = "es",
    profile_path: str | None = None,
    output_dir: str | None = None,
) -> str:
    """
    Genera una Guía de Preparación de Entrevista Técnica en Markdown para una vacante dada.

    Returns:
        str: Ruta absoluta del archivo Markdown generado (.md).
    """
    profile = load_profile(language=language, profile_path=profile_path)
    cand_name = profile.get("name", "Candidato")
    cand_title = profile.get("title", "Analytics Engineer / Data Engineer")

    lang_display = "ESPAÑOL" if language == "es" else "ENGLISH"
    prompt_user = f"""
    ### IDIOMA DE SALIDA: **{lang_display}**

    ### CANDIDATO
    Nombre: {cand_name}
    Perfil: {cand_title}
    Experiencia y Stack: {yaml.dump(profile.get('skills', {}), allow_unicode=True)}

    ### VACANTE A PREPARAR
    Puesto: {job.title}
    Empresa: {job.company}
    Ubicación: {job.location}
    Descripción Completa:
    {job.description}
    """

    provider = get_llm_provider()

    def _call_llm():
        return provider.generate(INTERVIEW_PREP_SYSTEM_PROMPT, prompt_user)

    logger.info(f"InterviewEngine: Generando Guía de Entrevista para '{job.title}' @ '{job.company}' [{lang_display}]...")
    markdown_content = call_with_retry(_call_llm)

    out_directory = output_dir or os.path.join(settings.project_root, "data", "interview_prep")
    os.makedirs(out_directory, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d")
    company_clean = sanitize_filename(job.company)
    title_clean = sanitize_filename(job.title)

    filename = f"InterviewPrep_{company_clean}_{title_clean}_{job.id or '1'}_{timestamp}.md"
    file_path = os.path.join(out_directory, filename)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)

    logger.info(f"InterviewEngine: Guía de entrevista guardada exitosamente en '{file_path}'")
    return file_path
