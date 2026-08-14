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
    
    # 3. Inicializar Scrapers
    scrapers = []
    if active_sources.get("remotive", True):
        scrapers.append(RemotiveScraper())
    if active_sources.get("indeed", False):
        scrapers.append(IndeedScraper())
        
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

    # 5. Imprimir métricas de ejecución
    pending_jobs = get_pending_jobs()
    
    logger.info("=== METRICAS DE EJECUCIÓN ===")
    logger.info(f"Vacantes encontradas en la red: {total_found}")
    logger.info(f"Vacantes nuevas guardadas en la BD (excluyendo duplicados): {total_added}")
    logger.info(f"Total de vacantes pendientes de evaluación LLM en BD: {len(pending_jobs)}")
    logger.info("=============================")

if __name__ == "__main__":
    main()
