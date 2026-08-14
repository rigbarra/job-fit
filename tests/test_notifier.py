import json

from src.database.models import CVSnapshot, Job, MatchResult
from src.notifier.discord import DiscordNotifier


def test_is_configured(monkeypatch):
    """Valida la detección de webhook configurado vs omitido."""
    monkeypatch.setattr("config.settings.settings.discord_webhook_url", None)
    assert not DiscordNotifier.is_configured()

    monkeypatch.setattr(
        "config.settings.settings.discord_webhook_url", "https://discord.com/api/webhooks/XXXX/YYYY"
    )
    assert not DiscordNotifier.is_configured()

    monkeypatch.setattr(
        "config.settings.settings.discord_webhook_url",
        "https://discord.com/api/webhooks/123456/abcdef",
    )
    assert DiscordNotifier.is_configured()


def test_send_job_notification_tier_3_silenced(monkeypatch, mocker):
    """Valida que los descartes (Tier 3) sean silenciados y no generen peticiones de red."""
    monkeypatch.setattr(
        "config.settings.settings.discord_webhook_url",
        "https://discord.com/api/webhooks/123456/abcdef",
    )

    mock_post = mocker.patch("src.notifier.discord.requests.post")

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
        id=1, job_id=1, score=20.0, tier=3, rationale="Incompatible.", missing_keywords="[]"
    )

    sent = DiscordNotifier.send_job_notification(job, match_result)
    assert not sent
    mock_post.assert_not_called()


def test_send_job_notification_tier_1_success(monkeypatch, mocker):
    """Valida el envío de una alerta Tier 1 (Verde) con Embed estructurado."""
    monkeypatch.setattr(
        "config.settings.settings.discord_webhook_url",
        "https://discord.com/api/webhooks/123456/abcdef",
    )

    mock_response = mocker.Mock()
    mock_response.status_code = 204
    mock_post = mocker.patch("src.notifier.discord.requests.post", return_value=mock_response)

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

    sent = DiscordNotifier.send_job_notification(job, match_result)
    assert sent
    assert mock_post.call_count == 1

    call_kwargs = mock_post.call_args.kwargs
    assert "json" in call_kwargs
    payload = call_kwargs["json"]

    assert "SnowTech" in payload["embeds"][0]["title"]
    assert payload["embeds"][0]["color"] == 0x2ECC71  # Verde
    assert any("92.0%" in f["value"] for f in payload["embeds"][0]["fields"])


def test_send_job_notification_tier_2_with_pdf(tmp_path, monkeypatch, mocker):
    """Valida el envío de una alerta Tier 2 (Amarillo) con archivo PDF adjunto multipart."""
    monkeypatch.setattr(
        "config.settings.settings.discord_webhook_url",
        "https://discord.com/api/webhooks/123456/abcdef",
    )

    # Crear un PDF simulado en disco
    pdf_file = tmp_path / "CV_Rigoberto_Barra_FinTech_20_T2_es_20260814.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 mock pdf content")

    mock_response = mocker.Mock()
    mock_response.status_code = 200
    mock_post = mocker.patch("src.notifier.discord.requests.post", return_value=mock_response)

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
        id=5, job_id=20, pdf_path=str(pdf_file), tex_path=str(pdf_file).replace(".pdf", ".tex")
    )

    sent = DiscordNotifier.send_job_notification(job, match_result, snapshot)
    assert sent
    assert mock_post.call_count == 1

    call_kwargs = mock_post.call_args.kwargs
    assert "data" in call_kwargs
    assert "payload_json" in call_kwargs["data"]
    assert "files" in call_kwargs
    assert "files[0]" in call_kwargs["files"]

    payload = json.loads(call_kwargs["data"]["payload_json"])
    assert payload["embeds"][0]["color"] == 0xF1C40F  # Amarillo
    assert any("Airflow" in f["value"] for f in payload["embeds"][0]["fields"])
