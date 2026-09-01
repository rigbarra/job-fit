import json
import logging
import os
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime

from config.settings import load_config, settings
from src.agent.filter import CHILE_TERMS
from src.database.models import CVSnapshot, Job, MatchResult

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    """Verifica si la URL del webhook de Discord está debidamente configurada."""
    if not settings.discord_webhook_url or "XXXX" in settings.discord_webhook_url:
        return False
    url = settings.discord_webhook_url.lower().strip()
    return url.startswith("https://discord.com/api/webhooks/") or url.startswith(
        "https://discordapp.com/api/webhooks/"
    )


def send_job_notification(
    job: Job,
    match_result: MatchResult,
    cv_snapshot: CVSnapshot | None = None,
) -> bool:
    """
    Envía una alerta de vacante a Discord con un Embed detallado y el PDF del CV adjunto.

    Args:
        job: Objeto Job con los datos de la vacante.
        match_result: Resultado del análisis ATS del LLM.
        cv_snapshot: Snapshot del PDF generado (si aplica).

    Returns:
        bool: True si la notificación fue despachada exitosamente, False en caso contrario.
    """
    if not is_configured():
        logger.warning(
            "Discord Notifier: DISCORD_WEBHOOK_URL no está configurado con una URL válida. Omitiendo notificación."
        )
        return False

    # Solo notificar vacantes con ATS Score >= 80.0 pts (Tier 1 o Tier 2 de alto impacto)
    if match_result.score < 80.0:
        logger.debug(
            f"Discord Notifier: Omitiendo notificación para vacante {job.id} con ATS score {match_result.score:.1f} pts (< 80.0 pts)."
        )
        return False

    # Omitir empresas excluidas exclusivamente de notificaciones de Discord (ej: BairesDev)
    config = load_config()
    excluded_companies = config.get("search_filters", {}).get("excluded_companies", [])
    company_lower = (job.company or "").lower()
    for ex_comp in excluded_companies:
        if ex_comp.lower() in company_lower:
            logger.info(
                f"Discord Notifier: Omitiendo notificación a Discord para vacante {job.id} de empresa excluida '{job.company}'."
            )
            return False

    # 1. Definir color e icono según el Tier
    if match_result.tier == 1:
        color = 0x2ECC71  # Verde Esmeralda (Match Alto)
        tier_icon = "🟢"
        tier_title = "MATCH DIRECTO (Tier 1)"
    else:
        color = 0xF1C40F  # Amarillo / Dorado (Match con Retoque)
        tier_icon = "🟡"
        tier_title = "MATCH CON RETOQUE (Tier 2)"

    # 2. Determinar etiqueta de ubicación (Chile vs Internacional)
    loc_lower = (job.location or "").lower()
    is_local = any(term in loc_lower for term in CHILE_TERMS)
    loc_tag = "[CHILE]" if is_local else "[INTL]"

    # 3. Formatear palabras clave faltantes
    missing_kw_str = "Ninguna detectada"
    if match_result.missing_keywords:
        try:
            parsed_kw = json.loads(match_result.missing_keywords)
            if isinstance(parsed_kw, list) and parsed_kw:
                missing_kw_str = ", ".join(f"`{k}`" for k in parsed_kw)
        except Exception:
            missing_kw_str = match_result.missing_keywords

    # 4. Construir campos del Embed
    fields = [
        {
            "name": "ATS Score",
            "value": f"**{match_result.score:.1f} / 100 pts** ({tier_icon} {tier_title})",
            "inline": True,
        },
        {
            "name": "📍 Ubicación",
            "value": job.location or "No especificada",
            "inline": True,
        },
        {
            "name": "Portal / Fuente",
            "value": job.source.capitalize(),
            "inline": True,
        },
    ]

    if job.salary:
        fields.append(
            {
                "name": "💰 Salario",
                "value": job.salary,
                "inline": True,
            }
        )

    if match_result.recommended_salary_ask:
        fields.append(
            {
                "name": "Renta Sugerida a Pedir",
                "value": f"**{match_result.recommended_salary_ask}**",
                "inline": True,
            }
        )

    fields.append(
        {
            "name": "🔗 Enlace de Postulación",
            "value": f"[👉 **Haz clic aquí para ver la oferta en {job.source.capitalize()}**]({job.url})",
            "inline": False,
        }
    )

    fields.append(
        {
            "name": "Keywords Faltantes",
            "value": missing_kw_str[:1024],
            "inline": False,
        }
    )

    if match_result.rationale:
        fields.append(
            {
                "name": "🧠 Análisis del Reclutador (ATS)",
                "value": match_result.rationale[:1024],
                "inline": False,
            }
        )

    if match_result.tier == 2 and match_result.adapted_summary:
        fields.append(
            {
                "name": "✍️ Resumen Adaptado Inyectado",
                "value": f"*{match_result.adapted_summary[:1020]}*",
                "inline": False,
            }
        )

    embed = {
        "title": f"{loc_tag} {job.title} @ {job.company}",
        "url": job.url,
        "color": color,
        "fields": fields,
        "footer": {
            "text": "job-fit • Pipeline Autónomo de Empleos",
            "icon_url": "https://cdn-icons-png.flaticon.com/512/3850/3850285.png",
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }

    payload = {
        "content": f"{loc_tag} **Nueva vacante recomendada:** [{job.title}]({job.url})",
        "embeds": [embed],
    }

    # 4. Despacho HTTP (con o sin archivo adjunto PDF vía multipart)
    try:
        has_pdf = bool(
            cv_snapshot and cv_snapshot.pdf_path and os.path.exists(cv_snapshot.pdf_path)
        )

        if has_pdf and cv_snapshot:
            pdf_filename = os.path.basename(cv_snapshot.pdf_path)
            with open(cv_snapshot.pdf_path, "rb") as f:
                pdf_bytes = f.read()

            boundary = f"----JobFitBoundary{uuid.uuid4().hex}"
            body = []

            # Parte 1: JSON payload
            body.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="payload_json"\r\n'
                f"Content-Type: application/json\r\n\r\n"
                f"{json.dumps(payload)}".encode()
            )

            # Parte 2: PDF adjunto
            body.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="files[0]"; filename="{pdf_filename}"\r\n'
                f"Content-Type: application/pdf\r\n\r\n".encode() + pdf_bytes
            )

            body.append(f"--{boundary}--\r\n".encode())
            post_data = b"\r\n".join(body)
            content_type = f"multipart/form-data; boundary={boundary}"
        else:
            post_data = json.dumps(payload).encode("utf-8")
            content_type = "application/json"

        # Normalizar URL: discordapp.com es legacy y no acepta POST redirects
        webhook_url = settings.discord_webhook_url.replace(
            "https://discordapp.com/api/webhooks/",
            "https://discord.com/api/webhooks/",
        )

        req = urllib.request.Request(
            webhook_url,
            data=post_data,
            headers={
                "Content-Type": content_type,
                "User-Agent": "Mozilla/5.0",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status in (200, 204):
                logger.info(
                    f"Discord: Notificación de '{job.title}' @ '{job.company}' enviada exitosamente (PDF adjunto: {has_pdf})."
                )
                return True
            else:
                logger.error(f"Discord: Error {resp.status} despachando webhook.")
                return False

    except urllib.error.HTTPError as he:
        err_body = he.read().decode("utf-8", errors="ignore")
        logger.error(f"Discord HTTPError {he.code} para vacante {job.id}: {err_body}")
        return False
    except Exception as e:
        logger.error(
            f"Discord: Excepción al despachar notificación para vacante {job.id}: {e}",
            exc_info=True,
        )
        return False
