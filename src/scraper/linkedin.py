"""
Cliente de scraping para LinkedIn Guest Mode (fuente opcional / best-effort).
"""

import logging

from src.database.models import Job
from src.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


class LinkedInScraper(BaseScraper):
    def __init__(self):
        super().__init__(name="linkedin")

    def fetch_jobs(self, keywords: list[str], locations: list[str], limit: int = 20) -> list[Job]:
        """
        Extrae vacantes desde endpoints públicos de LinkedIn.
        """
        logger.info(
            "LinkedIn: Scraper en modo best-effort (desactivado por defecto por seguridad de IP)."
        )
        return []
