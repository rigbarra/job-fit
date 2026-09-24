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
        description="Título profesional adaptado al puesto objetivo. Generar si el score está entre 60.0 y 84.0 pts.",
    )
    adapted_summary: str | None = Field(
        None,
        description="Resumen profesional optimizado con palabras clave de la oferta. Generar únicamente si el score está entre 60.0 y 84.0 pts. De lo contrario, dejar en null.",
    )


def build_system_prompt(cfg: dict) -> str:
    """
    Construye el system prompt del evaluador LLM inyectando los parámetros
    de dominio desde config.yaml (sección 'evaluation').
    Así el prompt es genérico y no necesita editarse para cambiar de dominio.
    """
    ev = cfg.get("evaluation", {})

    domain              = ev.get("domain", "Profesional")
    core_stack          = ev.get("core_stack", "según perfil del candidato")
    location            = ev.get("candidate_location", "Chile")
    languages           = ev.get("candidate_languages", "Español nativo")
    modality_chile      = ev.get("accepted_modality_chile", "Remoto o Híbrido")
    role_fail           = ev.get("role_gate_fail", [])
    role_exceptions     = ev.get("role_gate_pass_exceptions", [])
    ca_high             = ev.get("career_alignment_high", "Rol directamente alineado al perfil.")
    ca_mid              = ev.get("career_alignment_mid", "Rol parcialmente alineado.")
    ca_low              = ev.get("career_alignment_low", "Rol no alineado al perfil.")

    role_fail_str       = "\n    - ".join(role_fail) if role_fail else "(ninguno definido)"
    role_exceptions_str = "\n    - ".join(role_exceptions) if role_exceptions else "(ninguna)"

    return f"""
Eres un experto en Sistemas de Seguimiento de Candidatos (ATS) y reclutador técnico sénior especializado en perfiles de {domain}.

Tu tarea es realizar una evaluación de compatibilidad estructurada (Job Fit Evaluation) entre el Perfil Profesional del candidato y la Descripción de Vacante Laboral que se te proporciona, aplicando la metodología avanzada de evaluación multidimensional de ATS Score (escala 1 a 100 puntos).

### METODOLOGÍA DE EVALUACIÓN MULTIDIMENSIONAL:

#### 1. Compuertas de Elegibilidad, Idioma y Tipo de Rol (Hard Gates - Pass/Fail):

- **Elegibilidad Territorial y Contractual:**
  - El candidato reside físicamente en {location} y NO posee visa ni permiso de trabajo extranjero.
  - **Para ofertas en Chile:** Acepta {modality_chile}. Rechaza presencial puro.
  - **Para ofertas en el extranjero:** FALLA si el aviso está localizado en un país específico y no aclara apertura a candidatos de Chile/LATAM. PASA solo si explicita "remoto LATAM", "Worldwide", "open to candidates from Latin America", "100% remote contractor" o equivalente. NUNCA asumas elegibilidad por la sola presencia de la palabra "remote".
  - **FALLA territorial → Tier 3, score 1.0–39.0 pts.**

- **Idioma:** {languages}. Si el rol exige idioma que el candidato no domina → Tier 3.

- **Tipo de Rol (Role Type Gate):** El candidato es un profesional de {domain}. Si el rol es fundamentalmente distinto, FALLA esta compuerta → score máximo 40.0–59.0 pts.
  - **FALLAN esta compuerta (Tier 3 automático):**
    - {role_fail_str}
  - **NO fallan esta compuerta (evaluar normalmente):**
    - {role_exceptions_str}

#### 2. Dimensiones Ponderadas de Scoring (aplica solo si pasa todas las compuertas):

- **Technical Skills Match (30%):** Coincidencia en stack base: {core_stack}.
- **Experience & Seniority Match (25%):** Alineación en funciones reales y nivel Mid–Senior. No coincidencia literal de título.
- **Behavioral & Culture Fit (15%):** Equilibrio construcción activa vs mantenimiento pasivo.
- **Career Alignment & Growth (30%):** Si este rol es un avance coherente en la carrera del candidato. Criterios estrictos:
  - **ALTO (80–100):** {ca_high}
  - **MEDIO (50–79):** {ca_mid}
  - **BAJO (0–49):** {ca_low}

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
{{
  "score": 82.5,
  "rationale": "Justificación detallada...",
  "missing_keywords": ["herramienta_a", "herramienta_b"],
  "strengths": ["Fortaleza 1", "Fortaleza 2"],
  "gaps": ["Brecha 1"],
  "dimension_scores": {{
    "technical_skills": 85.0,
    "experience_match": 80.0,
    "behavioral_fit": 80.0,
    "career_alignment": 85.0
  }},
  "adapted_title": "Título adaptado",
  "adapted_summary": "Resumen adaptado aquí..."
}}
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

### METADATOS DEL PUESTO
- Cargo: {job_title}
- Empresa: {job_company}
- Ubicación registrada: {job_location}
- Modalidad detectada: {job_modality}

### PERFIL DEL CANDIDATO (YAML)
{candidate_profile}

### DESCRIPCIÓN DETALLADA DE LA VACANTE
{job_description}
"""


# Retrocompatibilidad: SYSTEM_PROMPT como constante usando config por defecto.
# Reemplazado por build_system_prompt(cfg) en evaluate_job para inyección dinámica.
def _default_system_prompt() -> str:
    try:
        from config.settings import load_config
        return build_system_prompt(load_config())
    except Exception:
        return build_system_prompt({})


SYSTEM_PROMPT = _default_system_prompt()
