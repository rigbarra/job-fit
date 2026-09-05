import json
import logging
from datetime import datetime
from pathlib import Path

import jinja2
import yaml

from config.settings import settings
from src.agent.providers import get_llm_provider
from src.agent.quota import call_with_retry
from src.cv_engine.builder import escape_latex, get_jinja_env, load_profile
from src.database.models import Job

logger = logging.getLogger(__name__)

COVER_LETTER_SYSTEM_PROMPT = """
Eres un especialista sénior en redacción de cartas de presentación (Cover Letters) para roles de Data Engineering, Analytics Engineering y Data Platform.

Tu tarea es redactar una Carta de Presentación persuasiva, profesional y personalizada de exactamente 1 página (aproximadamente 250 a 300 palabras).

### REGLAS DE REDACCIÓN:
1. **Preservar Veracidad Absoluta:** NUNCA inventes años de experiencia, empresas ni certificaciones que el candidato no posea.
2. **Estructura Impactante:**
   - **Saludo:** "Estimado Equipo de Reclutamiento de [Empresa]," o "Dear [Company] Hiring Team," según el idioma.
   - **Párrafo Inicial:** Nombrar el puesto, expresar entusiasmo y declarar la razón principal de encaje en 2-3 oraciones.
   - **Párrafo de Experiencia y Logros:** Presentar 3 viñetas concretas con métricas o tecnologías del stack del puesto (SQL, Python, PySpark, dbt, Cloud).
   - **Párrafo de Conexión:** Por qué esta empresa y este rol específico en datos generan interés genuino.
   - **Párrafo de Cierre:** Expresar disposición para una entrevista.
3. **Idioma:** Si la vacante está en español -> Redactar 100% en ESPAÑOL. Si está en inglés -> Redactar 100% en INGLÉS.

### FORMATO DE SALIDA (JSON ÚNICAMENTE):
```json
{
  "salutation": "Estimado Equipo de Reclutamiento de Accenture Chile,",
  "opening_paragraph": "Me dirijo a ustedes con gran entusiasmo para presentar mi candidatura al puesto de Senior Data Engineer...",
  "body_paragraph": "A lo largo de mi trayectoria en ingeniería de datos, he diseñado y optimizado pipelines de datos escalables...",
  "achievement_bullets": [
    "Diseño e implementación de pipelines ETL/ELT con Python, PySpark y dbt reduciendo tiempos de procesamiento.",
    "Modelamiento dimensional (Kimball) y optimización de consultas SQL en data warehouses en la nube.",
    "Implementación de buenas prácticas de calidad de datos, orquestación con Airflow y CI/CD para arquitecturas de datos."
  ],
  "connection_paragraph": "Accenture Chile destaca por su liderazgo en transformación digital y cultura de excelencia en analytics...",
  "closing_paragraph": "Agradezco de antemano su tiempo y consideración. Quedo a su entera disposición para conversar sobre cómo mi experiencia puede aportar valor al equipo.",
  "closing_valediction": "Atentamente,"
}
```
"""


def build_cover_letter_tex(
    job: Job,
    language: str = "es",
    profile_path: str | None = None,
) -> str:
    """
    Genera el código LaTeX compilable (.tex) para la carta de presentación adaptada a la vacante.
    """
    profile = load_profile(language=language, profile_path=profile_path)
    cand_name = profile.get("name", "Candidato")
    cand_loc = profile.get("location", "Chile")
    cand_email = profile.get("email", "")
    cand_phone = profile.get("phone", "")
    cand_linkedin = profile.get("linkedin", "")

    lang_display = "ESPAÑOL" if language == "es" else "ENGLISH"
    prompt_user = f"""
    ### IDIOMA DE SALIDA: **{lang_display}**
    Redacta la carta de presentación 100% en **{lang_display}**.

    ### CANDIDATO
    Nombre: {cand_name}
    Ubicación: {cand_loc}
    Email: {cand_email}
    LinkedIn: {cand_linkedin}
    Perfil técnico: {yaml.dump(profile.get('skills', {}), allow_unicode=True)}

    ### PUESTO OBJETIVO
    Título: {job.title}
    Empresa: {job.company}
    Descripción de la Oferta:
    {job.description[:2500]}
    """

    provider = get_llm_provider()

    def _call_llm():
        return provider.generate(COVER_LETTER_SYSTEM_PROMPT, prompt_user)

    logger.info(f"CoverEngine: Generando Carta de Presentación para '{job.title}' @ '{job.company}' [{lang_display}]...")
    raw_response = call_with_retry(_call_llm)

    # Limpiar JSON de la respuesta del LLM
    clean_json = raw_response.strip()
    if "```json" in clean_json:
        clean_json = clean_json.split("```json")[1].split("```")[0].strip()
    elif "```" in clean_json:
        clean_json = clean_json.split("```")[1].split("```")[0].strip()

    try:
        data = json.loads(clean_json)
    except Exception as e:
        logger.error(f"CoverEngine: Error parseando JSON de Cover Letter: {e}. Respuesta: {raw_response[:300]}")
        data = {
            "salutation": f"Dear Hiring Manager at {job.company}," if language == "en" else f"Estimado Equipo de {job.company},",
            "opening_paragraph": f"I am writing to express my strong interest in the {job.title} position.",
            "body_paragraph": "With extensive experience in Data Engineering, SQL, Python, and Cloud Data Platforms, I have built reliable pipelines.",
            "achievement_bullets": [
                "Built and maintained scalable ETL/ELT pipelines using SQL and Python.",
                "Implemented dimensional data models and optimized query performance.",
                "Collaborated across analytics and engineering teams to deliver high-quality data products."
            ],
            "connection_paragraph": f"I am especially drawn to {job.company}'s data engineering initiatives and growth.",
            "closing_paragraph": "I look forward to discussing how my technical background aligns with your team's goals.",
            "closing_valediction": "Kind regards," if language == "en" else "Atentamente,",
        }

    # Cargar plantilla Jinja2 de cover letter
    templates_dir = settings.project_root / "templates" / "cover"
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
        loader=jinja2.FileSystemLoader(str(templates_dir)),
    )

    template = env.get_template("cover_template.tex")

    context = {
        "candidate_name": escape_latex(cand_name),
        "candidate_location": escape_latex(cand_loc),
        "candidate_email": escape_latex(cand_email),
        "candidate_phone": escape_latex(cand_phone),
        "candidate_linkedin": escape_latex(cand_linkedin),
        "date_str": escape_latex(datetime.now().strftime("%d de %B de %Y" if language == "es" else "%B %d, %Y")),
        "company_name": escape_latex(job.company),
        "job_title": escape_latex(job.title),
        "salutation": escape_latex(data.get("salutation", "Estimado Equipo,")),
        "opening_paragraph": escape_latex(data.get("opening_paragraph", "")),
        "body_paragraph": escape_latex(data.get("body_paragraph", "")),
        "achievement_bullets": [escape_latex(b) for b in data.get("achievement_bullets", [])],
        "connection_paragraph": escape_latex(data.get("connection_paragraph", "")),
        "closing_paragraph": escape_latex(data.get("closing_paragraph", "")),
        "closing_valediction": escape_latex(data.get("closing_valediction", "Atentamente,")),
    }

    return template.render(**context)
