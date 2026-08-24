import logging
import sys

from config.loader import load_config
from config.settings import settings
from src.agent.evaluator import evaluate_job
from src.agent.filter import CHILE_TERMS, should_evaluate_job
from src.agent.quota import DailyQuotaExhaustedError, RateLimitError
from src.cv_engine.compiler import generate_cv_for_job
from src.database.models import MatchResult
from src.database.repository import get_pending_jobs, init_db, save_job, save_match_result
from src.notifier.discord import send_job_notification

# Configurar logging detallado
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("job-fit")


def is_local_location(location: str) -> bool:
    """Retorna True si la ubicación corresponde a Chile local."""
    loc_lower = (location or "").lower()
    return any(term in loc_lower for term in CHILE_TERMS)


def main():
    logger.info("Iniciando pipeline de Ingesta y Scraping (Fase 1)...")

    # 1. Inicializar Base de Datos SQLite (recrea las tablas vacías si fue borrada)
    logger.info("Inicializando base de datos local...")
    init_db()

    # 2. Cargar configuración y filtros (cacheado en memoria)
    config = load_config()
    search_filters = config.get("search_filters", {})
    keywords = search_filters.get("keywords", [])
    locations = search_filters.get("locations", [])
    limit = search_filters.get("limit_per_source", 20)
    max_job_age_days = search_filters.get("max_job_age_days", 1)
    experience_level = search_filters.get("experience_level", "4")

    active_sources = config.get("sources", {})
    rate_limiting = config.get("rate_limiting", {})
    notification_rules = config.get("notification_rules", {})

    intl_rules = notification_rules.get("international", {})
    intl_enabled = intl_rules.get("allow_tier_1", True) or intl_rules.get("allow_tier_2", True)

    # Separar ubicaciones en locales (Chile) e internacionales
    local_locs = [loc for loc in locations if is_local_location(loc)]
    intl_locs = [loc for loc in locations if loc not in local_locs]

    api_key_configured = bool(
        (settings.llm_api_key and settings.llm_api_key != "tu_api_key_aqui")
        or (settings.openrouter_api_key and settings.openrouter_api_key != "tu_api_key_de_openrouter_aqui")
    )

    # Definir el orden estricto de ejecución para priorizar cuotas
    # 1. GetOnBoard Chile (API), 2. LinkedIn Chile, 3. Indeed Chile, 4. Remotive (API Int.), 5. Indeed Int., 6. LinkedIn Int.
    execution_groups = [
        ("getonboard", True),
        ("linkedin", True),
        ("indeed", True),
        ("remotive", False),
        ("indeed", False),
        ("linkedin", False),
    ]

    total_added = 0
    total_found = 0
    evaluated_count = 0
    quota_exhausted = False

    logger.info(f"Ubicaciones locales (Chile): {local_locs}")
    logger.info(f"Reglas de notificación cargadas: {notification_rules}")

    search_scope = config.get("search_scope", "all").lower().strip()
    logger.info(f"Ámbito de búsqueda configurado: '{search_scope.upper()}'")

    # 4. Iniciar ejecución secuencial por grupos prioritarios
    for source_name, is_local in execution_groups:
        if quota_exhausted:
            logger.warning(
                f"Saltando grupo ({source_name.upper()}, Local={is_local}) porque la cuota diaria o límite de tasa fue alcanzado."
            )
            continue

        # Filtrar grupos según search_scope ('chile', 'international', 'all')
        if search_scope == "chile" and not is_local:
            logger.info(
                f"Saltando grupo internacional ({source_name.upper()}) porque search_scope='chile'."
            )
            continue
        elif search_scope == "international" and is_local:
            logger.info(
                f"Saltando grupo local ({source_name.upper()}) porque search_scope='international'."
            )
            continue

        # Si el grupo es internacional y todas sus reglas están deshabilitadas, saltar
        if not is_local and not intl_enabled:
            logger.info(
                f"Saltando grupo internacional ({source_name.upper()}) (desactivado en notification_rules)."
            )
            continue

        # Verificar si la fuente de datos está activa en config.yaml
        if not active_sources.get(source_name, False):
            logger.info(f"Saltando fuente '{source_name}' (desactivada en config.yaml).")
            continue

        # Remotive es siempre internacional (ignorar en paso local)
        if source_name == "remotive" and is_local:
            continue

        # Seleccionar ubicaciones para este grupo
        group_locs = local_locs if is_local else intl_locs
        if not group_locs and source_name not in ["remotive", "getonboard"]:
            continue

        logger.info(f"=== INICIANDO EJECUCIÓN GRUPO: {source_name.upper()} (Local={is_local}) ===")

        # A. Inicializar Scraper
        scraper = None
        if source_name == "getonboard":
            from src.scraper.getonboard import GetOnBoardScraper

            scraper = GetOnBoardScraper(rate_limit_config=rate_limiting)
        elif source_name == "remotive":
            from src.scraper.remotive import RemotiveScraper

            scraper = RemotiveScraper()
        elif source_name == "indeed":
            from src.scraper.indeed import IndeedScraper

            scraper = IndeedScraper(
                rate_limit_config=rate_limiting,
                max_job_age_days=max_job_age_days,
            )
        elif source_name == "linkedin":
            from src.scraper.linkedin import LinkedInScraper

            scraper = LinkedInScraper(
                rate_limit_config=rate_limiting,
                max_job_age_days=max_job_age_days,
                experience_level=experience_level,
            )

        if not scraper:
            continue

        # B. Scraping e ingesta para este grupo
        try:
            logger.info(
                f"Scrapeando {source_name} con keywords={keywords} y locs={group_locs if source_name != 'remotive' else 'API'}"
            )
            jobs = scraper.fetch_jobs(keywords=keywords, locations=group_locs, limit=limit)
            total_found += len(jobs)
            logger.info(f"Scraper '{source_name}' extrajo {len(jobs)} vacantes.")

            for job in jobs:
                _, is_new = save_job(job)
                if is_new:
                    total_added += 1
        except Exception as se:
            logger.error(f"Error en scraper {source_name} (Local={is_local}): {se}", exc_info=True)

        # C. Filtrar y evaluar inmediatamente las vacantes de este grupo
        pending_jobs = get_pending_jobs()
        group_pending_jobs = []
        for job in pending_jobs:
            if job.source != source_name:
                continue
            is_job_local = is_local_location(job.location)

            # Si estamos en scope chile y la vacante es internacional, descartar de inmediato
            if search_scope == "chile" and not is_job_local:
                auto_discard = MatchResult(
                    job_id=job.id,
                    score=0.0,
                    tier=3,
                    rationale="Descarte automático: Ubicación fuera de Chile.",
                    missing_keywords="[]",
                )
                save_match_result(auto_discard)
                continue

            if is_job_local == is_local:
                group_pending_jobs.append(job)

        if not group_pending_jobs:
            logger.info(f"Sin vacantes pendientes para {source_name.upper()} (Local={is_local}).")
            continue

        logger.info(f"Procesando {len(group_pending_jobs)} vacantes pendientes de este grupo...")

        filtered_pending_jobs = []
        for job in group_pending_jobs:
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

        # D. Evaluación con LLM para las vacantes pre-filtradas de este grupo
        if filtered_pending_jobs:
            if not api_key_configured:
                logger.warning(
                    f"OpenRouter: Hay {len(filtered_pending_jobs)} vacantes pre-filtradas con alto potencial, pero se salta la fase LLM porque OPENROUTER_API_KEY no está configurada."
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

                        # Verificar si califica para notificación según notification_rules en config.yaml
                        is_job_local = is_local_location(job.location)
                        group_key = "national" if is_job_local else "international"
                        tier_key = f"allow_tier_{match_result.tier}"
                        group_rules = notification_rules.get(group_key, {})
                        should_notify = bool(group_rules.get(tier_key, True))

                        if should_notify:
                            snapshot = None
                            try:
                                snapshot = generate_cv_for_job(job, match_result)
                                logger.info(f"CV PDF generado exitosamente: {snapshot.pdf_path}")
                            except Exception as ce:
                                logger.error(
                                    f"Error generando CV en PDF para vacante {job.id}: {ce}"
                                )

                            try:
                                send_job_notification(job, match_result, snapshot)
                            except Exception as de:
                                logger.error(
                                    f"Error despachando notificación de Discord para vacante {job.id}: {de}"
                                )
                    except DailyQuotaExhaustedError as dqe:
                        logger.warning(
                            f"Evaluación LLM pausada: {dqe}. Las vacantes pendientes se conservan para la próxima corrida."
                        )
                        quota_exhausted = True
                        break
                    except RateLimitError as rle:
                        logger.warning(
                            f"OpenRouter: El modelo está saturado temporalmente (429). Pausando evaluación para proteger cuota. {rle}"
                        )
                        quota_exhausted = True
                        break
                    except Exception as ee:
                        logger.error(f"Error evaluando vacante {job.id} ({job.title}): {ee}")

                    if quota_exhausted:
                        break

    # 6. Imprimir métricas finales de ejecución
    remaining_pending = get_pending_jobs()

    logger.info("=== METRICAS DE EJECUCIÓN ===")
    logger.info(f"Vacantes encontradas en la red: {total_found}")
    logger.info(f"Vacantes nuevas guardadas en la BD (excluyendo duplicados): {total_added}")
    logger.info(f"Vacantes evaluadas por LLM en esta corrida: {evaluated_count}")
    logger.info(f"Total de vacantes pendientes de evaluación LLM en BD: {len(remaining_pending)}")
    logger.info("=============================")

    # 7. Actualización automática del Estudio de Mercado Histórico Acumulativo
    try:
        from src.market_engine.analytics import generate_market_study_report
        canonical_path, _ = generate_market_study_report()
        logger.info(f"Estudio de Mercado Histórico actualizado en: {canonical_path}")
    except Exception as me:
        logger.error(f"Error actualizando estudio de mercado: {me}")


if __name__ == "__main__":
    main()
