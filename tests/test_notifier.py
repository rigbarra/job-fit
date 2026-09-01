import json

from src.database.models import CVSnapshot, Job, MatchResult
from src.notifier.discord import is_configured, send_job_notification


def test_is_configured(monkeypatch):
    """Valida la detección de webhook configurado vs omitido."""
    monkeypatch.setattr("config.settings.settings.discord_webhook_url", None)
    assert not is_configured()

    monkeypatch.setattr(
        "config.settings.settings.discord_webhook_url",
        "https://discord.com/api/webhooks/XXXX/YYYY",
    )
    assert not is_configured()

    monkeypatch.setattr(
        "config.settings.settings.discord_webhook_url",
        "https://discord.com/api/webhooks/123456/abcdef",
    )
    assert is_configured()

    monkeypatch.setattr(
        "config.settings.settings.discord_webhook_url",
        "https://discordapp.com/api/webhooks/123456/abcdef",
    )
    assert is_configured()


def test_send_job_notification_tier_3_silenced(monkeypatch, mocker):
    """Valida que los descartes (Tier 3) sean silenciados y no generen peticiones de red."""
    monkeypatch.setattr(
        "config.settings.settings.discord_webhook_url",
        "https://discord.com/api/webhooks/123456/abcdef",
    )

    mock_urlopen = mocker.patch("urllib.request.urlopen")

    job = Job(
        id=1,
        title="Admin Assistant",
        company="AdminCorp",
        location="Remote",
        description="Office assistant.",
        url="https://example.com/job/1",
        source="test",
    )
    match_result = MatchResult(
        id=1,
        job_id=1,
        score=20.0,
        tier=3,
        rationale="Incompatible.",
        missing_keywords="[]",
    )

    sent = send_job_notification(job, match_result)
    assert not sent
    mock_urlopen.assert_not_called()


def test_send_job_notification_excluded_company_silenced(monkeypatch, mocker):
    """Valida que empresas en la blacklist (ej. BairesDev) se silencien de Discord."""
    monkeypatch.setattr(
        "config.settings.settings.discord_webhook_url",
        "https://discord.com/api/webhooks/123456/abcdef",
    )
    mock_urlopen = mocker.patch("urllib.request.urlopen")

    job = Job(
        id=9,
        title="Senior Data Engineer",
        company="BairesDev",
        location="Remote",
        description="Airflow y Python.",
        url="https://example.com/job/9",
        source="indeed",
    )
    match_result = MatchResult(
        id=9, job_id=9, score=90.0, tier=1, rationale="Strong fit.", missing_keywords="[]"
    )

    sent = send_job_notification(job, match_result)
    assert not sent
    mock_urlopen.assert_not_called()


def test_send_job_notification_tier_1_success(monkeypatch, mocker):
    """Valida el envío de una alerta Tier 1 (Verde) con Embed estructurado."""
    monkeypatch.setattr(
        "config.settings.settings.discord_webhook_url",
        "https://discord.com/api/webhooks/123456/abcdef",
    )

    mock_resp = mocker.MagicMock()
    mock_resp.__enter__.return_value.status = 204
    mock_urlopen = mocker.patch("urllib.request.urlopen", return_value=mock_resp)

    job = Job(
        id=10,
        title="Senior Analytics Engineer",
        company="SnowTech",
        location="Remote (Chile)",
        salary="USD 5,000 - 6,500 / mes",
        description="dbt, SQL, Snowflake.",
        url="https://example.com/job/10",
        source="remotive",
    )
    match_result = MatchResult(
        id=1,
        job_id=10,
        score=92.0,
        tier=1,
        rationale="Match excelente con el perfil.",
        missing_keywords="[]",
    )

    sent = send_job_notification(job, match_result)
    assert sent
    assert mock_urlopen.call_count == 1

    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data.decode("utf-8"))

    assert "SnowTech" in payload["embeds"][0]["title"]
    assert payload["embeds"][0]["color"] == 0x2ECC71  # Verde
    assert any("92.0 / 100 pts" in f["value"] for f in payload["embeds"][0]["fields"])


def test_send_job_notification_tier_2_with_pdf(tmp_path, monkeypatch, mocker):
    """Valida el envío de una alerta Tier 2 (Amarillo) con archivo PDF adjunto multipart."""
    monkeypatch.setattr(
        "config.settings.settings.discord_webhook_url",
        "https://discord.com/api/webhooks/123456/abcdef",
    )

    # Crear un PDF simulado en disco
    pdf_file = tmp_path / "CV_Rigoberto_Barra_FinTech_20_T2_es_20260814.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 mock pdf content")

    mock_resp = mocker.MagicMock()
    mock_resp.__enter__.return_value.status = 200
    mock_urlopen = mocker.patch("urllib.request.urlopen", return_value=mock_resp)

    job = Job(
        id=20,
        title="Data Engineer",
        company="FinTech",
        location="Santiago, Chile",
        description="Airflow y Python.",
        url="https://example.com/job/20",
        source="indeed",
    )
    match_result = MatchResult(
        id=2,
        job_id=20,
        score=72.0,
        tier=2,
        rationale="Buen encaje con retoque.",
        missing_keywords='["Airflow"]',
        adapted_summary="Data Engineer con experiencia en Python y orquestación...",
    )
    snapshot = CVSnapshot(
        id=5,
        job_id=20,
        pdf_path=str(pdf_file),
        tex_path=str(pdf_file).replace(".pdf", ".tex"),
    )

    sent = send_job_notification(job, match_result, snapshot)
    assert sent
    assert mock_urlopen.call_count == 1

    req = mock_urlopen.call_args[0][0]
    assert "multipart/form-data" in req.headers["Content-type"]
    assert b"payload_json" in req.data
    assert b"files[0]" in req.data
    assert b"%PDF-1.4 mock pdf content" in req.data
