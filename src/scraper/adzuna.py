"""
Cliente de scraping para la API REST de Adzuna (fuente opcional).
Requiere ADZUNA_APP_ID y ADZUNA_API_KEY en .env.
"""

import logging

from src.database.models import Job
from src.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


class AdzunaScraper(BaseScraper):
    def __init__(self):
        super().__init__(name="adzuna")

    def fetch_jobs(self, keywords: list[str], locations: list[str], limit: int = 20) -> list[Job]:
        """
        Extrae vacantes desde la API REST de Adzuna (free tier).
        """
        logger.info("Adzuna: Scraper listo para activación cuando se configuren credenciales.")
        return []
