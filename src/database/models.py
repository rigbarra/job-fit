from typing import Optional, List
from datetime import datetime, timezone
from sqlmodel import SQLModel, Field, Relationship

class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(index=True)
    company: str = Field(index=True)
    location: str
    description: str
    url: str = Field(unique=True, index=True)
    source: str  # indeed, remotive, adzuna, linkedin
    salary: Optional[str] = None
    job_type: Optional[str] = None
    posted_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    hash_url: str = Field(unique=True, index=True)  # hash de la URL para deduplicación rápida
    
    # Relación uno-a-muchos con resultados de match
    match_results: List["MatchResult"] = Relationship(back_populates="job", cascade_delete=True)

class MatchResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    score: float  # Score entre 0.0 y 100.0
    tier: int     # 1 (match alto), 2 (match con retoque), 3 (descarte)
    rationale: str  # Explicación/justificación del LLM
    missing_keywords: str  # Palabras clave faltantes en formato JSON o separadas por comas
    adapted_summary: Optional[str] = None  # Resumen redactado por LLM si es Tier 2
    adapted_bullets: Optional[str] = None  # Viñetas redactadas en formato JSON
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    
    job: Job = Relationship(back_populates="match_results")

class CVSnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id", index=True)
    pdf_path: str  # Ruta del PDF generado
    tex_path: str  # Ruta del código fuente .tex generado
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
