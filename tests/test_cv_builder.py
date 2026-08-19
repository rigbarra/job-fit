import os
import shutil

import pytest

from src.cv_engine.builder import build_cv_tex, escape_latex
from src.cv_engine.compiler import (
    compile_tex_to_pdf,
    detect_job_language,
    generate_cv_for_job,
)
from src.database.models import Job, MatchResult


def test_escape_latex():
    """Valida el escape correcto de caracteres reservados de LaTeX."""
    raw_text = (
        "Experienced in 100% cloud platforms (AWS & GCP) with $100k budgets for data_pipeline #1."
    )
    escaped = escape_latex(raw_text)

    assert r"100\%" in escaped
    assert r"AWS \& GCP" in escaped
    assert r"\$100k" in escaped
    assert r"data\_pipeline" in escaped
    assert r"\#1" in escaped

    # Comprobar que no se doble-escapa si ya está escapado
    already_escaped = r"Valid \& escaped \% text"
    assert escape_latex(already_escaped) == already_escaped


def test_detect_job_language():
    """Valida la detección heurística del idioma de la vacante."""
    job_en = Job(
        title="Senior Analytics Engineer",
        company="GlobalTech",
        location="Remote",
        description="Looking for an experienced engineer with 5+ years of requirements and skills in SQL.",
        url="https://example.com/en",
        source="test",
    )
    assert detect_job_language(job_en) == "en"

    job_es = Job(
        title="Ingeniero de Datos",
        company="BancoChile",
        location="Santiago, Chile",
        description="Buscamos un profesional con experiencia y habilidades en modelado de datos y desarrollo.",
        url="https://example.com/es",
        source="test",
    )
    assert detect_job_language(job_es) == "es"


def test_build_cv_tex_base():
    """Valida la generación de código LaTeX para CV base bilingüe (Tier 1)."""
    tex_es = build_cv_tex(match_result=None, language="es")
    assert r"\documentclass" in tex_es
    assert "Rigoberto Barra" in tex_es
    assert "Resumen Profesional" in tex_es
    assert "Ingeniero Civil Industrial" in tex_es
    assert "Banco Estado" in tex_es

    tex_en = build_cv_tex(match_result=None, language="en")
    assert r"\documentclass" in tex_en
    assert "Rigoberto Barra" in tex_en
    assert "Professional Summary" in tex_en
    assert "Industrial Engineer" in tex_en
    assert "Banco Estado" in tex_en


def test_build_cv_tex_tier_2_adapted():
    """Valida la inyección de resumen y viñetas adaptadas para Tier 2 en español e inglés."""
    # Test en Español
    adapted_summary_es = "Ingeniero especializado con alto dominio de Airflow, Snowflake y dbt..."
    adapted_bullet_orig_es = "Lideré la migración y remodelación de flujos de datos"
    adapted_bullet_repl_es = "Lideré la migración de pipelines hacia Redshift usando Airflow"

    match_result_es = MatchResult(
        job_id=1,
        score=75.0,
        tier=2,
        rationale="Match moderado con retoque.",
        missing_keywords='["Airflow"]',
        adapted_summary=adapted_summary_es,
        adapted_bullets=f'{{"{adapted_bullet_orig_es}": "{adapted_bullet_repl_es}"}}',
    )

    tex_es = build_cv_tex(match_result=match_result_es, language="es")
    assert adapted_summary_es in tex_es
    assert adapted_bullet_repl_es in tex_es

    # Test en Inglés
    adapted_summary_en = "Senior Analytics Engineer with deep expertise in Airflow and dbt..."
    adapted_bullet_orig_en = "Spearheaded the migration and remodeling of legacy data flows"
    adapted_bullet_repl_en = "Spearheaded data migration towards Redshift orchestrated with Airflow"

    match_result_en = MatchResult(
        job_id=2,
        score=78.0,
        tier=2,
        rationale="Moderate match.",
        missing_keywords='["Airflow"]',
        adapted_summary=adapted_summary_en,
        adapted_bullets=f'{{"{adapted_bullet_orig_en}": "{adapted_bullet_repl_en}"}}',
    )

    tex_en = build_cv_tex(match_result=match_result_en, language="en")
    assert adapted_summary_en in tex_en
    assert adapted_bullet_repl_en in tex_en


@pytest.mark.skipif(not shutil.which("pdflatex"), reason="pdflatex no está instalado en el sistema")
def test_compile_tex_to_pdf_real(tmp_path):
    """Valida la compilación física de un PDF con pdflatex si está disponible."""
    tex_content = build_cv_tex(match_result=None, language="es")
    output_pdf = tmp_path / "test_cv.pdf"

    pdf_path = compile_tex_to_pdf(tex_content, str(output_pdf))

    assert os.path.exists(pdf_path)
    assert os.path.getsize(pdf_path) > 1000  # Archivo PDF válido (> 1KB)


def test_generate_cv_for_job(tmp_path, monkeypatch):
    """Valida el pipeline completo de generación de CV y registro de CVSnapshot."""
    monkeypatch.setattr("config.settings.settings.output_pdf_dir", str(tmp_path))

    job = Job(
        id=42,
        title="Analytics Engineer",
        company="FintechCorp",
        location="Remote",
        description="Requerimos experiencia en dbt y SQL.",
        url="https://example.com/job/42",
        source="test",
    )

    match_result = MatchResult(
        id=1, job_id=42, score=88.0, tier=1, rationale="Match excelente.", missing_keywords="[]"
    )

    if shutil.which("pdflatex"):
        snapshot = generate_cv_for_job(job, match_result, language="es")
        assert snapshot.job_id == 42
        assert os.path.exists(snapshot.pdf_path)
        assert os.path.exists(snapshot.tex_path)
        assert snapshot.pdf_path.endswith(".pdf")
        assert snapshot.tex_path.endswith(".tex")
