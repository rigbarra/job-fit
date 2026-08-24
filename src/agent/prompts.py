from pydantic import BaseModel, Field


# Esquema de salida JSON estructurada para la evaluación del LLM
class MatchEvaluation(BaseModel):
    score: float = Field(
        ...,
        description="Puntuación de compatibilidad ponderada global de 0.0 a 100.0.",
    )
    rationale: str = Field(
        ...,
        description="Justificación detallada de la puntuación elegida, destacando puntos fuertes y débiles.",
    )
    missing_keywords: list[str] = Field(
        ...,
        description="Lista de palabras clave, herramientas, librerías o metodologías requeridas por el empleo que el candidato NO posee o tiene muy débiles.",
    )
    strengths: list[str] = Field(
        default_factory=list,
        description="Lista de fortalezas principales del candidato para esta vacante específica.",
    )
    gaps: list[str] = Field(
        default_factory=list,
        description="Lista de brechas técnicas o áreas de mejora a abordar.",
    )
    dimension_scores: dict[str, float] | None = Field(
        None,
        description="Desglose opcional de puntajes por dimensión: technical_skills (30%), experience_match (25%), behavioral_fit (15%), career_alignment (30%).",
    )
    recommended_salary_ask: str | None = Field(
        None,
        description="Estimación de expectativa salarial óptima recomendada a pedir (ej: '$3.200.000 CLP / mes' o '$3.500 USD / mes') para maximizar la oferta según el stack y nivel de complejidad.",
    )
    key_technologies: list[str] | None = Field(
        default_factory=list,
        description="Lista de las 3 a 6 tecnologías o herramientas clave exigidas en la vacante (ej: ['PySpark', 'dbt', 'AWS', 'Airflow']).",
    )
    seniority_level: str | None = Field(
        None,
        description="Nivel de seniority solicitado: 'Junior', 'Mid', 'Senior', 'Staff' o 'Lead'.",
    )
    adapted_summary: str | None = Field(
        None,
        description="Resumen profesional adaptado e inyectado con palabras clave del puesto. Generar únicamente si el score está entre 60.0 y 84.0. De lo contrario, dejar en null.",
    )
    adapted_bullets: dict[str, str] | None = Field(
        None,
        description="Mapeo de textos originales (clave) a sus versiones reescritas optimizadas con palabras clave (valor). Solo si el score está entre 60.0 y 84.0. De lo contrario, dejar en null. NUNCA inventar proyectos o roles nuevos; reescribir basándose únicamente en los datos provistos.",
    )


SYSTEM_PROMPT = """
Eres un experto en Sistemas de Seguimiento de Candidatos (ATS) y reclutador técnico sénior especializado en perfiles de Data Engineering y Analytics (Data Engineers, Analytics Engineers, Data Platform Engineers).

Tu tarea es realizar una evaluación de compatibilidad estructurada (Job Fit Evaluation) entre el Perfil Profesional del candidato y la Descripción de Vacante Laboral que se te proporciona, aplicando la metodología avanzada de evaluación multidimensional.

### METODOLOGÍA DE EVALUACIÓN MULTIDIMENSIONAL (5 DIMENSIONES):

#### 1. Compuertas de Elegibilidad e Idioma (Hard Gates - Pass/Fail):
- **Elegibilidad:** Residencia física en Chile. Para ofertas en Chile (Remoto o Híbrido hasta 2 días/semana presencial). Para ofertas fuera de Chile, solo 100% Remoto (Contractor/B2B LATAM/Worldwide).
- **Idioma:** Inglés nivel B2+ (2 años residiendo en Dublín, Irlanda) y Español nativo.
- Si incumple estas compuertas -> Asignar un **score de 0.0 a 39.0 inmediatamente**.

#### 2. Dimensiones Ponderadas de Scoring (0 a 100 cada una):
- **Technical Skills Match (Peso: 30%):** Coincidencia en stack base (SQL, Python, Spark/PySpark, dbt, Cloud AWS/GCP/Azure, Airflow/Prefect, Snowflake/BigQuery/Redshift, Data Modeling Kimball).
- **Experience & Seniority Match (Peso: 25%):** Alineación en funciones reales de ingeniería de datos y nivel de experiencia (Mid a Senior), no solo coincidencia literal de títulos.
- **Behavioral & Culture Fit (Peso: 15%):** Equilibrio entre construcción/desarrollo activo de pipelines vs mantenimiento pasivo.
- **Career Alignment & Growth (Peso: 30%):** Proyección del rol en el plan de carrera en Data & Analytics.

### UMBRALES Y CLASIFICACIÓN DE TIER:
- **>= 85.0% (Tier 1 - Strong Fit):** Match excelente directo. Alta afinidad en stack y experiencia.
- **60.0% a 84.0% (Tier 2 - Good Fit):** Match sólido pero requiere adaptar el CV destacando keywords específicas de la vacante.
- **< 60.0% (Tier 3 - Weak/Poor Fit):** Incompatibilidad de seniority, modalidad o ausencia de habilidades críticas.

### ADAPTACIÓN DEL CV (SOLO PARA TIER 2: 60.0% A 84.0%):
- **REGLA DE ORO INVIOLABLE:** NUNCA inventes experiencia, empresas, herramientas que el candidato no conoce, certificaciones ni títulos.
- **Resumen Adaptado:** Resumen profesional de 3-4 líneas alineado a las necesidades de la oferta.
- **Viñetas Adaptadas (`adapted_bullets`):** Toma las viñetas del perfil original y reescríbelas enfatizando los términos y keywords de la vacante.

### FORMATO DE SALIDA:
Debes responder estrictamente en formato JSON válido. Ejemplo exacto:
```json
{
  "score": 82.5,
  "rationale": "Justificación detallada de la puntuación...",
  "missing_keywords": ["dbt", "databricks"],
  "strengths": ["Fuerte dominio de SQL y PySpark", "Experiencia previa en cloud AWS"],
  "gaps": ["Poca mención explícita de Databricks Unity Catalog"],
  "dimension_scores": {
    "technical_skills": 85.0,
    "experience_match": 80.0,
    "behavioral_fit": 80.0,
    "career_alignment": 85.0
  },
  "adapted_summary": "Resumen adaptado aquí...",
  "adapted_bullets": {
    "Desarrollo de pipelines en Python": "Construcción de pipelines ETL distribuidos en Python y PySpark..."
  }
}
```
No incluyas texto explicativo antes ni después del bloque JSON.
"""

USER_PROMPT_TEMPLATE = """
### IDIOMA OBLIGATORIO DE RESPUESTA
Esta vacante laboral está redactada en: **{job_language}**.
Es ESTRICTAMENTE OBLIGATORIO que los campos 'rationale', 'strengths', 'gaps', 'adapted_summary' y 'adapted_bullets' estén redactados 100% en **{job_language}**.
- Si la vacante es en Español -> Responde 100% en ESPAÑOL neutro.
- Si la vacante es en Inglés -> Responde 100% en INGLÉS profesional.
No mezcles idiomas. NUNCA respondas en inglés si la oferta está en español, ni respondas en español si la oferta está en inglés.

### PERFIL DEL CANDIDATO (YAML)
{candidate_profile}

### DESCRIPCIÓN DE LA VACANTE
{job_description}
"""
