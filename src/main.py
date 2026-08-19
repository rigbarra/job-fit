import logging
import sys

from config.loader import load_config
from config.settings import settings
from src.agent.evaluator import evaluate_job
from src.agent.filter import should_evaluate_job
from src.agent.quota import DailyQuotaExhaustedError, RateLimitError
from src.cv_engine.compiler import generate_cv_for_job
from src.database.models import MatchResult
from src.database.repository import get_pending_jobs, init_db, save_job, save_match_result
from src.notifier.discord import DiscordNotifier
from src.scraper.remotive import RemotiveScraper

# Configurar logging detallado
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("job-fit")


def main():
    logger.info("Iniciando pipeline de Ingesta y Scraping (Fase 1)...")

    # 1. Inicializar Base de Datos SQLite
    logger.info("Inicializando base de datos local...")
    init_db()

    # 2. Cargar configuración y filtros (cacheado en memoria)
    config = load_config()
    search_filters = config.get("search_filters", {})
    keywords = search_filters.get("keywords", [])
    locations = search_filters.get("locations", [])
    limit = search_filters.get("limit_per_source", 20)

    active_sources = config.get("sources", {})
    rate_limiting = config.get("rate_limiting", {})

    # 3. Inicializar Scrapers
    scrapers = []
    max_job_age_days = search_filters.get("max_job_age_days", 3)
    experience_level = search_filters.get("experience_level", "4")
    if active_sources.get("remotive", True):
        scrapers.append(RemotiveScraper())
    if active_sources.get("indeed", False):
        from src.scraper.indeed import IndeedScraper

        scrapers.append(IndeedScraper(rate_limit_config=rate_limiting))
    if active_sources.get("linkedin", False):
        from src.scraper.linkedin import LinkedInScraper

        scrapers.append(
            LinkedInScraper(
                rate_limit_config=rate_limiting,
                max_job_age_days=max_job_age_days,
                experience_level=experience_level,
            )
        )

    logger.info(f"Scrapers activos: {[s.name for s in scrapers]}")
    logger.info(
        f"Parámetros de búsqueda: Keywords={keywords}, Locations={locations}, Límite={limit}"
    )

    total_added = 0
    total_found = 0

    # 4. Orquestar Ejecución de Scraping
    for scraper in scrapers:
        try:
            logger.info(f"Ejecutando scraper: {scraper.name}...")
            jobs = scraper.fetch_jobs(keywords=keywords, locations=locations, limit=limit)
            total_found += len(jobs)

            logger.info(f"Scraper '{scraper.name}' extrajo {len(jobs)} vacantes.")

            for job in jobs:
                _, is_new = save_job(job)
                if is_new:
                    total_added += 1

        except Exception as e:
            logger.error(f"Error ejecutando scraper {scraper.name}: {e}", exc_info=True)

    # 5. Evaluar vacantes pendientes con el LLM
    pending_jobs = get_pending_jobs()
    logger.info(f"Detectadas {len(pending_jobs)} vacantes pendientes de evaluación por el LLM.")

    api_key_configured = (
        settings.openrouter_api_key
        and settings.openrouter_api_key != "tu_api_key_de_openrouter_aqui"
    )

    evaluated_count = 0
    if pending_jobs:
        filtered_pending_jobs = []
        for job in pending_jobs:
            try:
                passed, reason = should_evaluate_job(job)

                if not passed:
                    auto_discard = MatchResult(
                        job_id=job.id, score=10.0, tier=3, rationale=reason, missing_keywords="[]"
                    )
                    save_match_result(auto_discard)
                    logger.info(
                        f"Vacante '{job.title}' @ '{job.company}': DESCARTADA localmente (Algoritmo)."
                    )
                else:
                    filtered_pending_jobs.append(job)
            except Exception as fe:
                logger.error(f"Error en pre-filtrado de vacante {job.id}: {fe}")

        if filtered_pending_jobs:
            if not api_key_configured:
                logger.warning(
                    f"OpenRouter API: Hay {len(filtered_pending_jobs)} vacantes pre-filtradas con alto potencial, pero se salta la fase LLM porque OPENROUTER_API_KEY no está configurada."
                )
            else:
                for job in filtered_pending_jobs:
                    try:
                        match_result = evaluate_job(job)
                        save_match_result(match_result)
                        evaluated_count += 1
                        logger.info(
                            f"Vacante '{job.title}' @ '{job.company}': Evaluada con éxito vía LLM. Score: {match_result.score:.1f}% -> Tier {match_result.tier}"
                        )

                        # Si es Tier 1 o Tier 2, compilar PDF y notificar
                        if match_result.tier in (1, 2):
                            snapshot = None
                            try:
                                snapshot = generate_cv_for_job(job, match_result)
                                logger.info(f"📄 CV PDF generado exitosamente: {snapshot.pdf_path}")
                            except Exception as ce:
                                logger.error(
                                    f"Error generando CV en PDF para vacante {job.id}: {ce}"
                                )

                            try:
                                DiscordNotifier.send_job_notification(job, match_result, snapshot)
                            except Exception as de:
                                logger.error(
                                    f"Error despachando notificación de Discord para vacante {job.id}: {de}"
                                )
                    except DailyQuotaExhaustedError as dqe:
                        logger.warning(
                            f"Evaluación LLM pausada: {dqe}. Las vacantes pendientes se conservan para la próxima corrida."
                        )
                        break
                    except RateLimitError as rle:
                        logger.warning(
                            f"OpenRouter: El modelo está saturado temporalmente (429). Pausando evaluación para proteger cuota. {rle}"
                        )
                        break
                    except Exception as ee:
                        logger.error(f"Error evaluando vacante {job.id} ({job.title}): {ee}")

    # 6. Imprimir métricas finales de ejecución
    remaining_pending = get_pending_jobs()

    logger.info("=== METRICAS DE EJECUCIÓN ===")
    logger.info(f"Vacantes encontradas en la red: {total_found}")
    logger.info(f"Vacantes nuevas guardadas en la BD (excluyendo duplicados): {total_added}")
    logger.info(f"Vacantes evaluadas por LLM en esta corrida: {evaluated_count}")
    logger.info(f"Total de vacantes pendientes de evaluación LLM en BD: {len(remaining_pending)}")
    logger.info("=============================")


if __name__ == "__main__":
    main()
