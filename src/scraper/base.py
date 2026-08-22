import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from curl_cffi import requests

from src.database.models import Job
from src.database.repository import is_duplicate

logger = logging.getLogger(__name__)


@dataclass
class RawJobCard:
    """Resultado intermedio de una tarjeta de búsqueda antes de descargar la descripción."""

    title: str
    company: str
    location: str
    url: str
    salary: str | None = None
    job_type: str | None = None
    posted_at: datetime | None = None


class WebScraper(ABC):
    """
    Clase base para scrapers de sitios web con scraping HTML.
    Encapsula la lógica compartida de:
    - Loop de búsqueda por keywords × locations
    - Throttling con pausas aleatorias
    - Circuit breaker para protección de IP
    - Deduplicación previa a la descarga de detalles
    - Descarga de descripción completa del puesto

    Cada scraper concreto solo implementa 3 métodos de parsing específicos.
    """

    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }

    DEFAULT_RATE_CONFIG = {
        "min_delay_seconds": 3.0,
        "max_delay_seconds": 6.0,
        "max_errors_before_circuit_break": 1,
    }

    def __init__(self, name: str, rate_limit_config: dict | None = None):
        self.name = name
        self.rate_config = {**self.DEFAULT_RATE_CONFIG, **(rate_limit_config or {})}

    # ──────────────── Métodos abstractos (cada scraper implementa estos) ────────────────

    @abstractmethod
    def _build_search_url(self, keyword: str, location: str) -> str:
        """Construye la URL de búsqueda para un keyword + location dados."""

    @abstractmethod
    def _parse_search_results(self, response: requests.Response, location: str) -> list[RawJobCard]:
        """Parsea la respuesta HTML/JSON de búsqueda y extrae tarjetas de empleo."""

    @abstractmethod
    def _extract_description(self, url: str, response: requests.Response) -> str | None:
        """Extrae la descripción textual completa desde la página de detalle del puesto."""

    # ──────────────── Lógica compartida ────────────────

    def fetch_jobs(self, keywords: list[str], locations: list[str], limit: int = 20) -> list[Job]:
        """Orquesta la extracción completa: búsqueda → dedup → detalle → Job."""
        jobs_found: list[Job] = []
        consecutive_blocks = 0
        max_blocks = self.rate_config.get("max_errors_before_circuit_break", 1)

        for keyword in keywords:
            for location in locations:
                if len(jobs_found) >= limit:
                    break

                if consecutive_blocks >= max_blocks:
                    logger.critical(
                        f"{self.name}: Circuit Breaker activado. Abortando búsqueda para proteger IP."
                    )
                    return jobs_found

                search_url = self._build_search_url(keyword, location)

                try:
                    logger.info(f"{self.name}: Buscando '{keyword}' en '{location}'...")
                    response = requests.get(
                        search_url,
                        headers=self.DEFAULT_HEADERS,
                        impersonate="chrome120",
                        timeout=20,
                    )

                    if response.status_code in [403, 429]:
                        consecutive_blocks += 1
                        logger.error(
                            f"{self.name}: Bloqueo detectado ({response.status_code}) al consultar búsqueda."
                        )
                        continue
                    elif response.status_code != 200:
                        logger.error(
                            f"{self.name}: Error {response.status_code} al consultar búsqueda."
                        )
                        continue

                    # Búsqueda exitosa: reiniciar contador de bloqueos
                    consecutive_blocks = 0

                    cards = self._parse_search_results(response, location)
                    logger.info(f"{self.name}: Encontrados {len(cards)} resultados en la página.")

                    for card in cards:
                        if len(jobs_found) >= limit:
                            break

                        if consecutive_blocks >= max_blocks:
                            logger.critical(
                                f"{self.name}: Circuit Breaker activado durante descarga de detalles. Abortando."
                            )
                            return jobs_found

                        # 1. Deduplicación previa: Si ya está en BD, no gastamos peticiones de red
                        if is_duplicate(card.url):
                            logger.debug(f"{self.name}: Omitiendo duplicado '{card.url}'")
                            continue

                        # 2. Throttling antes de descargar descripción
                        delay = random.uniform(
                            self.rate_config.get("min_delay_seconds", 3.0),
                            self.rate_config.get("max_delay_seconds", 6.0),
                        )
                        logger.info(
                            f"{self.name}: Pausa de {delay:.2f}s antes de descargar detalle de '{card.title}'..."
                        )
                        time.sleep(delay)

                        # 3. Descargar descripción completa del puesto
                        job_desc = None
                        try:
                            detail_resp = requests.get(
                                card.url,
                                headers=self.DEFAULT_HEADERS,
                                impersonate="chrome120",
                                timeout=15,
                            )

                            if detail_resp.status_code in [403, 429]:
                                consecutive_blocks += 1
                                logger.error(
                                    f"{self.name}: Bloqueo detectado al bajar descripción ({detail_resp.status_code})."
                                )
                                continue

                            if detail_resp.status_code == 200:
                                job_desc = self._extract_description(card.url, detail_resp)

                        except Exception as de:
                            logger.error(
                                f"{self.name}: Error de red al consultar detalle de '{card.title}': {de}"
                            )
                            continue

                        if not job_desc:
                            logger.warning(
                                f"{self.name}: No se pudo extraer descripción para '{card.title}' @ '{card.company}'. Omitiendo."
                            )
                            continue

                        job = Job(
                            title=card.title,
                            company=card.company,
                            location=card.location,
                            description=job_desc,
                            url=card.url,
                            source=self.name,
                            salary=card.salary,
                            job_type=card.job_type,
                            posted_at=card.posted_at,
                        )
                        jobs_found.append(job)
                        logger.info(
                            f"{self.name}: Extraída vacante '{job.title}' @ '{job.company}'"
                        )

                except Exception as e:
                    logger.exception(f"{self.name}: Error durante scraping: {e}")

                # Pausa entre combinaciones de búsqueda
                time.sleep(random.uniform(2.0, 4.0))

        return jobs_found
