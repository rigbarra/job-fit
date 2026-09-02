import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlmodel import Session

from src.cleaner import clean_all_temporary_data, clean_old_db_records, clean_old_files
from src.database.models import CVSnapshot, Job, MatchResult
from src.database.repository import engine


def test_clean_old_files(tmp_path):
    # Crear archivo nuevo (0 días)
    new_file = tmp_path / "new_cv.pdf"
    new_file.write_text("dummy pdf")

    # Crear archivo antiguo (modificado hace 35 días)
    old_file = tmp_path / "old_cv.pdf"
    old_file.write_text("old pdf")
    old_mtime = time.time() - (35 * 86400)
    os.utime(old_file, (old_mtime, old_mtime))

    removed = clean_old_files(tmp_path, days=30)
    assert removed == 1
    assert new_file.exists()
    assert not old_file.exists()


def test_clean_old_db_records():
    cutoff = datetime.now(tz=UTC) - timedelta(days=35)

    with Session(engine) as session:
        # Job antiguo descartado (Tier 3)
        old_job = Job(
            title="Old Discarded Job",
            company="OldCorp",
            location="Chile",
            description="Desc",
            url="https://example.com/old_discarded",
            source="test",
            hash_url="old_hash_123",
            created_at=cutoff,
        )
        session.add(old_job)
        session.commit()
        session.refresh(old_job)

        match = MatchResult(
            job_id=old_job.id,
            score=10.0,
            tier=3,
            rationale="Descarte antiguo",
            missing_keywords="[]",
            created_at=cutoff,
        )
        session.add(match)

        # Snapshot con archivo inexistente
        snap = CVSnapshot(
            job_id=old_job.id,
            pdf_path="/non/existent/path/cv.pdf",
            tex_path="/non/existent/path/cv.tex",
            created_at=cutoff,
        )
        session.add(snap)
        session.commit()

    stats = clean_old_db_records(days=30)
    assert stats["cv_snapshots"] >= 1
    assert stats["tier3_jobs"] >= 1


def test_clean_all_temporary_data():
    summary = clean_all_temporary_data(days=30)
    assert isinstance(summary, dict)
    assert "cv_files_removed" in summary
    assert "interview_files_removed" in summary
    assert "tmp_files_removed" in summary
    assert "db_cv_snapshots_removed" in summary
    assert "db_tier3_jobs_removed" in summary
