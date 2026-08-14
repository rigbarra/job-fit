import json
import logging
import os
from datetime import UTC, datetime

from curl_cffi import requests

from config.settings import settings
from src.database.models import CVSnapshot, Job, MatchResult

logger = logging.getLogger(__name__)


class DiscordNotifier:
    """
    Gestor de notificaciones a Discord mediante Webhooks oficiales.
    Construye Embeds enriquecidos y adjunta los PDFs de CV adaptados.
    """

    @staticmethod
    def is_configured() -> bool:
        """Verifica si la URL del webhook de Discord está debidamente configurada."""
        return (
            bool(settings.discord_webhook_url)
            and "XXXX" not in settings.discord_webhook_url
            and settings.discord_webhook_url.startswith("https://discord.com/api/webhooks/")
        )

    @classmethod
    def send_job_notification(
        cls, job: Job, match_result: MatchResult, cv_snapshot: CVSnapshot | None = None
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
        if not cls.is_configured():
            logger.warning(
                "Discord Notifier: DISCORD_WEBHOOK_URL no está configurado con una URL válida. Omitiendo notificación."
            )
            return False

        # Solo notificar Tier 1 (Match Alto) y Tier 2 (Match con Retoque)
        if match_result.tier not in (1, 2):
            logger.debug(
                f"Discord Notifier: Omitiendo notificación para vacante {job.id} clasificada como Tier {match_result.tier}."
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

        # 2. Formatear palabras clave faltantes
        missing_kw_str = "Ninguna detectada"
        if match_result.missing_keywords:
            try:
                parsed_kw = json.loads(match_result.missing_keywords)
                if isinstance(parsed_kw, list) and parsed_kw:
                    missing_kw_str = ", ".join(f"`{k}`" for k in parsed_kw)
            except Exception:
                missing_kw_str = match_result.missing_keywords

        # 3. Construir campos del Embed
        fields = [
            {
                "name": "📊 Match Score",
                "value": f"**{match_result.score:.1f}%** ({tier_icon} {tier_title})",
                "inline": True,
            },
            {"name": "📍 Ubicación", "value": job.location or "No especificada", "inline": True},
            {"name": "🌐 Portal / Fuente", "value": job.source.capitalize(), "inline": True},
        ]

        if job.salary:
            fields.append({"name": "💰 Salario", "value": job.salary, "inline": True})

        fields.append(
            {"name": "🔍 Keywords Faltantes", "value": missing_kw_str[:1024], "inline": False}
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
            "title": f"🎯 {job.title} @ {job.company}",
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
            "content": f"🚨 **Nueva vacante recomendada encontrada:** [{job.title}]({job.url})",
            "embeds": [embed],
        }

        # 4. Preparar el despacho (con o sin archivo adjunto)
        try:
            pdf_attached = False
            files = {}
            pdf_file_handle = None

            if cv_snapshot and cv_snapshot.pdf_path and os.path.exists(cv_snapshot.pdf_path):
                pdf_filename = os.path.basename(cv_snapshot.pdf_path)
                pdf_file_handle = open(cv_snapshot.pdf_path, "rb")
                files = {"files[0]": (pdf_filename, pdf_file_handle, "application/pdf")}
                pdf_attached = True

            try:
                if pdf_attached and pdf_file_handle:
                    response = requests.post(
                        settings.discord_webhook_url,
                        data={"payload_json": json.dumps(payload)},
                        files=files,
                        timeout=30,
                    )
                else:
                    response = requests.post(
                        settings.discord_webhook_url,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                        timeout=30,
                    )

                if response.status_code in (200, 204):
                    logger.info(
                        f"Discord: Notificación de '{job.title}' @ '{job.company}' enviada exitosamente (PDF adjunto: {pdf_attached})."
                    )
                    return True
                else:
                    logger.error(
                        f"Discord: Error {response.status_code} despachando webhook: {response.text}"
                    )
                    return False

            finally:
                if pdf_file_handle:
                    pdf_file_handle.close()

        except Exception as e:
            logger.error(
                f"Discord: Excepción al despachar notificación para vacante {job.id}: {e}",
                exc_info=True,
            )
            return False
