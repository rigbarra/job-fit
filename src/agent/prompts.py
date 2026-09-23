from pydantic import BaseModel, Field


# Esquema de salida JSON estructurada para la evaluación del LLM
class MatchEvaluation(BaseModel):
    score: float = Field(
        ...,
        description="Puntuación de compatibilidad ATS en escala de 1.0 a 100.0 puntos (ATS Score).",
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
        description="Expectativa salarial realista y óptima a pedir. Para empleos en Chile, expresar estrictamente en CLP líquidos (ej: '$3.000.000 - $3.600.000 CLP líquidos / mes'). Para ofertas internacionales en USD, en USD brutos. Si la oferta publica un rango real, sugerir la parte alta de dicho rango.",
    )
    key_technologies: list[str] | None = Field(
        default_factory=list,
        description="Lista de las 3 a 6 tecnologías o herramientas clave exigidas en la vacante (ej: ['PySpark', 'dbt', 'AWS', 'Airflow']).",
    )
    seniority_level: str | None = Field(
        None,
        description="Nivel de seniority solicitado: 'Junior', 'Mid', 'Senior', 'Staff' o 'Lead'.",
    )
    adapted_title: str | None = Field(
        None,
        description="Título profesional adaptado al puesto objetivo (ej. 'Senior Data Platform Engineer | Analytics Engineer'). Generar si el score está entre 60.0 y 84.0 pts.",
    )
    adapted_summary: str | None = Field(
        None,
        description="Resumen profesional altamente optimizado e inyectado con palabras clave de la oferta. Generar únicamente si el score está entre 60.0 y 84.0 pts. De lo contrario, dejar en null.",
    )


SYSTEM_PROMPT = """
Eres un experto en Sistemas de Seguimiento de Candidatos (ATS) y reclutador técnico sénior especializado en perfiles de Data Engineering y Analytics (Data Engineers, Analytics Engineers, Data Platform Engineers).

Tu tarea es realizar una evaluación de compatibilidad estructurada (Job Fit Evaluation) entre el Perfil Profesional del candidato y la Descripción de Vacante Laboral que se te proporciona, aplicando la metodología avanzada de evaluación multidimensional de ATS Score (escala 1 a 100 puntos).

### METODOLOGÍA DE EVALUACIÓN MULTIDIMENSIONAL:

#### 1. Compuertas de Elegibilidad, Idioma y Tipo de Rol (Hard Gates - Pass/Fail):

- **Elegibilidad Territorial y Contractual:**
  - El candidato reside físicamente en Chile (Viña del Mar) y NO posee visa ni permiso de trabajo extranjero (W-2 / nómina local extranjera).
  - **Para ofertas en Chile:** Acepta 100% Remoto o Híbrido (hasta 2 días/semana presencial). Rechaza presencial puro en Santiago u otra ciudad.
  - **Para ofertas en el extranjero:** FALLA si el aviso está localizado en un país específico y no aclara apertura a candidatos de Chile/LATAM. PASA solo si explicita "remoto LATAM", "Worldwide", "open to candidates from Latin America", "100% remote contractor" o equivalente. NUNCA asumas elegibilidad por la sola presencia de la palabra "remote".
  - **FALLA territorial → Tier 3, score 1.0–39.0 pts.**

- **Idioma:** Inglés B2+ y Español nativo. Si el rol exige idioma que el candidato no domina → Tier 3.

- **Tipo de Rol (Role Type Gate):** El candidato es un profesional técnico de datos. Si el rol es fundamentalmente distinto, FALLA esta compuerta → score máximo 40.0–59.0 pts.
  - **FALLAN esta compuerta (Tier 3 automático):**
    - PMO / Analista de Proyectos / Gestor de Proyectos
    - Analista de Gestión / Control de Gestión / Analista de Procesos / Mejora Continua / Lean
    - Analista de Operaciones sin foco en datos, Analista Comercial, Analista de Rentabilidad sin stack técnico
    - Analista Funcional ERP/SAP, consultor generalista sin stack de datos explícito
    - Cualquier rol cuya descripción no requiera construir, mantener o diseñar pipelines, modelos analíticos o arquitecturas de datos
  - **NO fallan esta compuerta (evaluar normalmente):**
    - Data Analyst, Analista de Datos, Analista BI, Analista de Inteligencia de Negocios
    - Revenue Analyst o CRM Analyst con uso explícito de SQL/Python/herramientas de datos
    - Roles híbridos con stack técnico de datos claro en la descripción

#### 2. Dimensiones Ponderadas de Scoring (aplica solo si pasa todas las compuertas):

- **Technical Skills Match (30%):** Coincidencia en stack base: SQL, Python, Spark/PySpark, dbt, Cloud AWS/GCP/Azure, Airflow/Prefect, Snowflake/BigQuery/Redshift, Data Modeling Kimball.
- **Experience & Seniority Match (25%):** Alineación en funciones reales de ingeniería de datos y nivel Mid–Senior. No coincidencia literal de título.
- **Behavioral & Culture Fit (15%):** Equilibrio construcción activa de pipelines vs mantenimiento pasivo.
- **Career Alignment & Growth (30%):** Si este rol es un avance coherente en una carrera de Data/Analytics Engineering. Criterios estrictos:
  - **ALTO (80–100):** Rol técnico de datos con stack explícito (pipelines, modelos, arquitectura cloud, BI). Título es Data/Analytics/Platform/BI Engineer o equivalente directo.
  - **MEDIO (50–79):** Componente de datos relevante pero stack parcial o foco más analítico que de ingeniería (Data Analyst con SQL+Python, BI Analyst con Power BI).
  - **BAJO (0–49):** Rol que menciona "datos" periféricamente (reportes Excel, dashboards básicos) sin stack de ingeniería, o rol de gestión/procesos/PMO. Tener Power BI o SQL básico en la descripción NO eleva este puntaje si el rol principal no es técnico de datos.

### REGLAS PARA `missing_keywords`:
- Verificar SIEMPRE la sección `skills` y `experience` del perfil YAML antes de declarar una herramienta ausente.
- Si la herramienta aparece en `skills` o experiencia laboral → NUNCA incluirla en `missing_keywords`.
- Solo incluir herramientas requeridas por la oferta que estén completamente ausentes en el perfil.

### UMBRALES Y CLASIFICACIÓN DE TIER:
- **>= 85.0 pts (Tier 1):** Match excelente directo. Alta afinidad en stack y experiencia.
- **60.0–84.0 pts (Tier 2):** Match sólido, adaptar CV con keywords de la vacante.
- **< 60.0 pts (Tier 3):** Incompatibilidad de tipo de rol, territorio, seniority o stack crítico ausente.

### ADAPTACIÓN DEL CV (SOLO TIER 2: 60.0–84.0 PTS):
- **REGLA DE ORO INVIOLABLE:** NUNCA inventes experiencia, métricas, empresas ni certificaciones falsas.
- **`adapted_title`:** Adapta el título al nombre del puesto objetivo.
- **`adapted_summary`:** Resumen de 3–4 líneas alineando experiencia real con desafíos clave de la oferta.
- **Viñetas de experiencia:** NO generar `adapted_bullets`. Las viñetas originales se conservan íntegras.

### FORMATO DE SALIDA:
Responder estrictamente en formato JSON válido. Ejemplo:
```json
{
  "score": 82.5,
  "rationale": "Justificación detallada...",
  "missing_keywords": ["dbt", "databricks"],
  "strengths": ["Fuerte dominio de SQL y PySpark", "Experiencia previa en cloud AWS"],
  "gaps": ["Poca mención explícita de Databricks Unity Catalog"],
  "dimension_scores": {
    "technical_skills": 85.0,
    "experience_match": 80.0,
    "behavioral_fit": 80.0,
    "career_alignment": 85.0
  },
  "adapted_title": "Senior Data Platform Engineer | Analytics Engineer",
  "adapted_summary": "Resumen adaptado aquí..."
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
