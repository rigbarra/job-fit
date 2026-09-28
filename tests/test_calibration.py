import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from src.agent.calibration import detect_filter_phase, extract_tech_preview
from src.database.models import CalibrationFeedback
from src.database.repository import (
    get_reviewed_calibration_job_ids,
    save_calibration_feedback,
)


def test_detect_filter_phase():
    phase, _ = detect_filter_phase("Descarte algorítmico: La oferta fue publicada hace 4 días", 10.0)
    assert phase == "AGE"

    phase, _ = detect_filter_phase("Descarte algorítmico: El título no contiene palabras clave de datos", 10.0)
    assert phase == "TITLE_KEYWORDS"

    phase, _ = detect_filter_phase("Descarte algorítmico: Contiene término excluido", 10.0)
    assert phase == "TITLE_BLACKLIST"

    phase, _ = detect_filter_phase("Score bajo en evaluación técnica", 45.0)
    assert phase == "LLM_SCORE"


def test_extract_tech_preview():
    text = "We need a Senior Data Engineer skilled in Python, SQL, dbt and Databricks with Docker."
    techs = extract_tech_preview(text)
    assert "python" in techs
    assert "sql" in techs
    assert "dbt" in techs
    assert "databricks" in techs
    assert "docker" in techs
    assert "snowflake" not in techs


def test_calibration_feedback_repository(monkeypatch):
    test_engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(test_engine)

    import src.database.repository as repo
    monkeypatch.setattr(repo, "engine", test_engine)

    save_calibration_feedback(
        job_id=999,
        decision="INTERESTED",
        filter_phase="AGE",
        user_comment="Excelente oportunidad en Santiago",
    )

    reviewed = get_reviewed_calibration_job_ids()
    assert 999 in reviewed

    with Session(test_engine) as session:
        feedback = session.exec(
            select(CalibrationFeedback).where(CalibrationFeedback.job_id == 999)
        ).first()
        assert feedback is not None
        assert feedback.decision == "INTERESTED"
        assert feedback.filter_phase == "AGE"
        assert feedback.user_comment == "Excelente oportunidad en Santiago"
