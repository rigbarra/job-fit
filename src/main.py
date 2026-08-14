import logging
import sys
import yaml
from pathlib import Path

from config.settings import settings
from src.database.repository import init_db, save_job, get_pending_jobs
from src.scraper.remotive import RemotiveScraper
from src.scraper.indeed import IndeedScraper

# Configurar logging detallado
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("job-fit")

def load_config() -> dict:
    """Carga los parámetros y filtros del archivo yaml de configuración."""
    config_path = settings.project_root / "config" / "config.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"No se pudo cargar el archivo de configuración en {config_path}: {e}")
        # Retornar una estructura básica de fallback
        return {
            "search_filters": {
                "keywords": ["Analytics Engineer", "Data Engineer"],
                "locations": ["Remote"],
                "limit_per_source": 10
            },
            "sources": {
                "remotive": True,
                "indeed": False
            }
        }

def main():
    logger.info("Iniciando pipeline de Ingesta y Scraping (Fase 1)...")
    
    # 1. Inicializar Base de Datos SQLite
    logger.info("Inicializando base de datos local...")
    init_db()
    
    # 2. Cargar configuración y filtros
    config = load_config()
    search_filters = config.get("search_filters", {})
    keywords = search_filters.get("keywords", [])
    locations = search_filters.get("locations", [])
    limit = search_filters.get("limit_per_source", 20)
    
    active_sources = config.get("sources", {})
    rate_limiting = config.get("rate_limiting", {})
    
    # 3. Inicializar Scrapers
    scrapers = []
    if active_sources.get("remotive", True):
        scrapers.append(RemotiveScraper())
    if active_sources.get("indeed", False):
        scrapers.append(IndeedScraper(rate_limit_config=rate_limiting))
        
    logger.info(f"Scrapers activos: {[s.name for s in scrapers]}")
    logger.info(f"Parámetros de búsqueda: Keywords={keywords}, Locations={locations}, Límite={limit}")
    
    total_added = 0
    total_found = 0

    # 4. Orquestar Ejecución de Scraping
    for scraper in scrapers:
        try:
            logger.info(f"Ejecutando scraper: {scraper.name}...")
            jobs = scraper.fetch_jobs(keywords=keywords, locations=locations, limit=limit)
            total_found += len(jobs)
            
            logger.info(f"Scraper '{scraper.name}' extrajo {len(jobs)} vacantes.")
            
            # Guardar en BD (deduplicando automáticamente)
            for job in jobs:
                saved_job = save_job(job)
                # Si el ID fue asignado por la BD tras insertar, es nuevo
                if saved_job.id is not None and saved_job.created_at == job.created_at:
                    total_added += 1
                    
        except Exception as e:
            logger.error(f"Error ejecutando scraper {scraper.name}: {e}", exc_info=True)

    # 5. Evaluar vacantes pendientes con el LLM
    pending_jobs = get_pending_jobs()
    logger.info(f"Detectadas {len(pending_jobs)} vacantes pendientes de evaluación por el LLM.")
    
    api_key_configured = settings.openrouter_api_key and settings.openrouter_api_key != "tu_api_key_de_openrouter_aqui"
    
    evaluated_count = 0
    if pending_jobs:
        # Importar el filtro de forma tardía
        from src.agent.filter import should_evaluate_job
        from src.database.models import MatchResult
        from src.database.repository import save_match_result
        
        filtered_pending_jobs = []
        for job in pending_jobs:
            try:
                # 1. Pre-filtrado algorítmico local (Ahorro de tokens)
                passed, reason = should_evaluate_job(job)
                
                if not passed:
                    # Registrar descarte inmediato en BD sin coste de API
                    auto_discard = MatchResult(
                        job_id=job.id,
                        score=10.0,
                        tier=3,
                        rationale=reason,
                        missing_keywords="[]"
                    )
                    save_match_result(auto_discard)
                    logger.info(f"Vacante '{job.title}' @ '{job.company}': DESCARTADA localmente (Algoritmo).")
                else:
                    filtered_pending_jobs.append(job)
            except Exception as fe:
                logger.error(f"Error en pre-filtrado de vacante {job.id}: {fe}")
                
        # 2. Ejecutar evaluación mediante LLM solo para las vacantes que superaron el filtro
        if filtered_pending_jobs:
            if not api_key_configured:
                logger.warning(f"OpenRouter API: Hay {len(filtered_pending_jobs)} vacantes pre-filtradas con alto potencial, pero se salta la fase LLM porque OPENROUTER_API_KEY no está configurada.")
            else:
                from src.agent.evaluator import evaluate_job
                for job in filtered_pending_jobs:
                    try:
                        match_result = evaluate_job(job)
                        save_match_result(match_result)
                        evaluated_count += 1
                        logger.info(f"Vacante '{job.title}' @ '{job.company}': Evaluada con éxito vía LLM. Score: {match_result.score:.1f}% -> Tier {match_result.tier}")
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
