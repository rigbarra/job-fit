import logging
import sys

from config.settings import load_config, settings
from src.agent.evaluator import evaluate_job
from src.agent.filter import CHILE_TERMS, normalize_text, should_evaluate_job
from src.agent.quota import DailyQuotaExhaustedError, RateLimitError
from src.cv_engine.compiler import generate_cv_for_job
from src.database.models import Job, MatchResult
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
    """Retorna True si la ubicación corresponde a Chile local o vacantes Remotas accesibles desde Chile/LATAM."""
    # F-5: reutiliza normalize_text + CHILE_TERMS de filter.py (single source of truth)
    loc = normalize_text(location)
    if any(term in loc for term in CHILE_TERMS):
        return True
    return any(r in loc for r in ["remote", "remoto", "teletrabajo", "wfh", "worldwide", "global", "latin america", "latam"])


def _maybe_notify(
    job: Job,
    match_result: MatchResult,
    notification_rules: dict,
    search_filters: dict,
    min_score: float,
) -> None:
    """F-2: Lógica de notificación extraída para eliminar duplicación en main(). Genera CV y despacha Discord si aplica."""
    is_job_local = is_local_location(job.location)
    group_key = "national" if is_job_local else "international"
    tier_key = f"allow_tier_{match_result.tier}"
    should_notify = bool(notification_rules.get(group_key, {}).get(tier_key, False))

    if match_result.score < min_score:
        should_notify = False

    excluded_comps = search_filters.get("excluded_companies", [])
    if any(ex.lower() in (job.company or "").lower() for ex in excluded_comps):
        should_notify = False

    if not should_notify:
        return

    snapshot = None
    try:
        snapshot = generate_cv_for_job(job, match_result)
        logger.info(f"CV PDF generado exitosamente: {snapshot.pdf_path}")
    except Exception as ce:
        logger.error(f"Error generando CV en PDF para vacante {job.id}: {ce}")

    try:
        send_job_notification(job, match_result, snapshot)
    except Exception as de:
        logger.error(f"Error despachando notificación de Discord para vacante {job.id}: {de}")



def main():
    logger.info("Iniciando pipeline de Ingesta y Scraping (Fase 1)...")

    # 1. Inicializar Base de Datos SQLite (recrea las tablas vacías si fue borrada)
    logger.info("Inicializando base de datos local...")
    init_db()

    # Purga automática de datos temporales (> 30 días)
    try:
        from src.cleaner import clean_all_temporary_data
        clean_all_temporary_data(days=30)
    except Exception as cle:
        logger.warning(f"No se pudo completar la limpieza automática: {cle}")

    # 2. Cargar configuración y filtros (cacheado en memoria)
    config = load_config()
    search_filters = config.get("search_filters", {})
    keywords = search_filters.get("keywords", [])
    locations = search_filters.get("locations", [])
    limit = search_filters.get("limit_per_source", 20)
    max_job_age_days = search_filters.get("max_job_age_days", 1)
    experience_level = search_filters.get("experience_level", "4")
    # A-4: umbral de notificación desde config.yaml (único lugar para cambiarlo)
    min_notify_score = float(config.get("notification_rules", {}).get("min_score_to_notify", 80.0))

    active_sources = config.get("sources", {})
    rate_limiting = config.get("rate_limiting", {})
    notification_rules = config.get("notification_rules", {})

    intl_rules = notification_rules.get("international", {})
    intl_enabled = intl_rules.get("allow_tier_1", True) or intl_rules.get("allow_tier_2", True)

    # Separar ubicaciones en locales (Chile) e internacionales (LATAM / Worldwide)
    local_locs = [loc for loc in locations if is_local_location(loc)]
    intl_locs = search_filters.get("international_locations", ["Latin America", "Worldwide"])

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

        # Prioridad absoluta a Chile: El grupo internacional solo procede si hay cuota sobrante reservando para Chile
        if not is_local:
            from src.database.repository import get_match_results_count_today
            calls_today = get_match_results_count_today()
            reserve_for_chile = 30
            if calls_today >= (settings.llm_max_calls_per_day - reserve_for_chile):
                logger.info(
                    f"Saltando grupo internacional ({source_name.upper()}): Priorizando Chile. "
                    f"Cuota hoy: {calls_today}/{settings.llm_max_calls_per_day} (reserva mínima Chile: {reserve_for_chile})."
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
            group_limit = limit if is_local else min(limit, 30)
            jobs = scraper.fetch_jobs(keywords=keywords, locations=group_locs, limit=group_limit)
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
                            f"Vacante '{job.title}' @ '{job.company}': Evaluada con éxito vía LLM. Score: {match_result.score:.1f} / 100 pts -> Tier {match_result.tier}"
                        )
                        _maybe_notify(job, match_result, notification_rules, search_filters, min_notify_score)
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


    # 5. Pasada final para vacantes pendientes rezagadas (manuales o por fallas temporales de red previas)
    catchall_pending = get_pending_jobs()
    if catchall_pending and not quota_exhausted and api_key_configured:
        # Priorizar siempre vacantes de Chile sobre internacionales
        catchall_pending.sort(key=lambda j: not is_local_location(j.location))
        logger.info(f"=== PASADA FINAL: EVALUANDO {len(catchall_pending)} VACANTES PENDIENTES REZAGADAS/MANUALES ===")
        for job in catchall_pending:
            try:
                passed, reason = should_evaluate_job(job)
                if not passed:
                    auto_discard = MatchResult(
                        job_id=job.id, score=10.0, tier=3, rationale=reason, missing_keywords="[]"
                    )
                    save_match_result(auto_discard)
                    logger.info(f"Vacante '{job.title}' @ '{job.company}': DESCARTADA localmente (Algoritmo).")
                else:
                    match_result = evaluate_job(job)
                    save_match_result(match_result)
                    evaluated_count += 1
                    logger.info(
                        f"Vacante '{job.title}' @ '{job.company}': Evaluada con éxito vía LLM (pasada final). Score: {match_result.score:.1f} / 100 pts -> Tier {match_result.tier}"
                    )
                    _maybe_notify(job, match_result, notification_rules, search_filters, min_notify_score)
            except DailyQuotaExhaustedError as dqe:
                logger.warning(f"Evaluación LLM pausada: {dqe}.")
                quota_exhausted = True
                break
            except RateLimitError as rle:
                logger.warning(f"OpenRouter: Saturado temporalmente (429). {rle}")
                quota_exhausted = True
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

    # 7. Actualización automática del Estudio de Mercado Histórico Acumulativo
    try:
        from src.market_engine.analytics import generate_market_study_report
        canonical_path, _ = generate_market_study_report()
        logger.info(f"Estudio de Mercado Histórico actualizado en: {canonical_path}")
    except Exception as me:
        logger.error(f"Error actualizando estudio de mercado: {me}")

    # 8. Sincronización automática del Vault de Obsidian (Markdown + Kanban)
    try:
        from src.obsidian_exporter import sync_obsidian_vault
        res = sync_obsidian_vault()
        logger.info(f"Vault de Obsidian sincronizado automáticamente ({res['cards_created']} fichas).")
    except Exception as oe:
        logger.error(f"Error sincronizando Vault de Obsidian: {oe}")


if __name__ == "__main__":
    main()
