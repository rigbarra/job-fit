from datetime import UTC, datetime
from sqlmodel import Field, Relationship, SQLModel


class Job(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    company: str = Field(index=True)
    location: str
    description: str
    url: str = Field(unique=True, index=True)
    source: str  # indeed, remotive, getonboard, linkedin, manual

    # Campos enriquecidos de compensación y mercado
    salary: str | None = None
    min_salary: float | None = None
    max_salary: float | None = None
    salary_currency: str | None = None  # CLP, USD, EUR

    # Modalidad y localización
    job_type: str | None = None
    modality: str | None = None  # Remoto 100%, Híbrido (1x4, 2x3, etc.), Presencial
    country: str | None = None

    # Contactos y notas
    recruiter_contact: str | None = None
    recruiter_phone: str | None = None
    notes: str | None = None

    posted_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    hash_url: str = Field(unique=True, index=True)  # hash de la URL para deduplicación rápida

    # Relación uno-a-muchos con resultados de match
    match_results: list["MatchResult"] = Relationship(back_populates="job", cascade_delete=True)


class MatchResult(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    score: float  # Score entre 0.0 y 100.0
    tier: int  # 1 (match alto), 2 (match con retoque), 3 (descarte)
    rationale: str  # Explicación/justificación del LLM
    missing_keywords: str  # Palabras clave faltantes en formato JSON

    # Recomendaciones salariales y de mercado por vacante
    recommended_salary_ask: str | None = None  # Estimación salarial óptima a pedir
    key_technologies: str | None = None  # Lista JSON de tecnologías requeridas (ej: ["dbt", "Snowflake", "Airflow"])
    seniority_level: str | None = None  # Junior, Mid, Senior, Lead

    adapted_title: str | None = None  # Título profesional adaptado por LLM si es Tier 2
    adapted_summary: str | None = None  # Resumen redactado por LLM si es Tier 2
    adapted_bullets: str | None = None  # Viñetas redactadas en formato JSON
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    job: Job = Relationship(back_populates="match_results")


class CVSnapshot(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    pdf_path: str  # Ruta del PDF generado
    tex_path: str  # Ruta del código fuente .tex generado
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
