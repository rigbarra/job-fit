import logging
import random
import time
import urllib.parse
from datetime import UTC, datetime

from bs4 import BeautifulSoup
from curl_cffi import requests

from src.database.models import Job
from src.database.repository import is_duplicate
from src.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


class LinkedInScraper(BaseScraper):
    """
    Scraper para vacantes públicas de LinkedIn utilizando los endpoints guest sin autenticación,
    emulando TLS con curl_cffi y respetando tiempos de espera para protección de IP.
    """

    def __init__(self, rate_limit_config: dict | None = None):
        super().__init__(name="linkedin")
        self.search_api_url = (
            "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
        )
        self.rate_config = rate_limit_config or {
            "min_delay_seconds": 4.0,
            "max_delay_seconds": 8.0,
            "max_errors_before_circuit_break": 1,
        }

    def fetch_jobs(self, keywords: list[str], locations: list[str], limit: int = 20) -> list[Job]:
        """
        Extrae vacantes de LinkedIn Guest API con deduplicación y throttling.

        Args:
            keywords: Lista de cargos a buscar (ej: ["Analytics Engineer"]).
            locations: Lista de ubicaciones (ej: ["Chile", "Remote"]).
            limit: Número máximo de vacantes a retornar.

        Returns:
            list[Job]: Lista de modelos Job con descripciones completas.
        """
        jobs_found: list[Job] = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }

        consecutive_blocks = 0
        max_blocks = self.rate_config.get("max_errors_before_circuit_break", 1)

        for keyword in keywords:
            for location in locations:
                if len(jobs_found) >= limit:
                    break

                if consecutive_blocks >= max_blocks:
                    logger.critical(
                        "LinkedIn: Circuit Breaker activado. Abortando búsqueda para proteger IP."
                    )
                    return jobs_found

                params = {
                    "keywords": keyword,
                    "location": location,
                    "start": 0,
                }
                search_url = f"{self.search_api_url}?{urllib.parse.urlencode(params)}"

                try:
                    logger.info(f"LinkedIn: Buscando '{keyword}' en '{location}'...")
                    response = requests.get(
                        search_url, headers=headers, impersonate="chrome120", timeout=20
                    )

                    if response.status_code in [403, 429]:
                        consecutive_blocks += 1
                        logger.error(
                            f"LinkedIn: Bloqueo detectado ({response.status_code}) al consultar búsqueda."
                        )
                        continue
                    elif response.status_code != 200:
                        logger.error(
                            f"LinkedIn: Error {response.status_code} al consultar búsqueda."
                        )
                        continue

                    # Búsqueda exitosa: reiniciar contador de bloqueos
                    consecutive_blocks = 0

                    soup = BeautifulSoup(response.text, "html.parser")
                    cards = soup.find_all(
                        ["li", "div"], class_=lambda c: c and "base-card" in c
                    ) or soup.find_all("li")

                    logger.info(
                        f"LinkedIn: Encontradas {len(cards)} tarjetas de empleo en la página."
                    )

                    for card in cards:
                        if len(jobs_found) >= limit:
                            break

                        if consecutive_blocks >= max_blocks:
                            logger.critical(
                                "LinkedIn: Circuit Breaker activado durante descarga de detalles. Abortando."
                            )
                            return jobs_found

                        # 1. Extraer elementos de la tarjeta
                        title_el = card.find(class_="base-search-card__title")
                        company_el = card.find(class_="base-search-card__subtitle")
                        link_el = card.find("a", class_="base-card__full-link")
                        loc_el = card.find(class_="job-search-card__location")
                        time_el = card.find("time")

                        if not title_el or not link_el or not link_el.get("href"):
                            continue

                        raw_url = link_el["href"].strip()
                        # Limpiar parámetros de tracking (ej. ?position=1&pageNum=0)
                        clean_url = raw_url.split("?")[0]

                        # 2. Deduplicación previa: Si ya existe en BD, omitir descarga
                        if is_duplicate(clean_url):
                            logger.debug(f"LinkedIn: Omitiendo duplicado '{clean_url}'")
                            continue

                        title = title_el.get_text().strip()
                        company = (
                            company_el.get_text().strip() if company_el else "Empresa Confidencial"
                        )
                        job_location = loc_el.get_text().strip() if loc_el else location

                        # Fecha de publicación
                        posted_at = None
                        if time_el and time_el.get("datetime"):
                            try:
                                posted_at = datetime.fromisoformat(time_el["datetime"]).replace(
                                    tzinfo=UTC
                                )
                            except Exception:
                                pass

                        # 3. Throttling previo a la descarga de la descripción completa
                        delay = random.uniform(
                            self.rate_config.get("min_delay_seconds", 4.0),
                            self.rate_config.get("max_delay_seconds", 8.0),
                        )
                        logger.info(
                            f"LinkedIn: Pausa de seguridad de {delay:.2f}s antes de descargar detalle de '{title}'..."
                        )
                        time.sleep(delay)

                        # 4. Descargar descripción completa del puesto
                        job_desc = None
                        try:
                            detail_resp = requests.get(
                                clean_url, headers=headers, impersonate="chrome120", timeout=15
                            )

                            if detail_resp.status_code in [403, 429]:
                                consecutive_blocks += 1
                                logger.error(
                                    f"LinkedIn: Bloqueo detectado al bajar descripción ({detail_resp.status_code})."
                                )
                                continue

                            if detail_resp.status_code == 200:
                                detail_soup = BeautifulSoup(detail_resp.text, "html.parser")
                                desc_el = (
                                    detail_soup.find(class_="show-more-less-html__markup")
                                    or detail_soup.find(class_="description__text")
                                    or detail_soup.find(id="job-details")
                                )
                                if desc_el:
                                    job_desc = desc_el.get_text(separator="\n").strip()

                        except Exception as de:
                            logger.error(
                                f"LinkedIn: Error de red al consultar detalle de '{title}': {de}"
                            )
                            continue

                        if not job_desc:
                            logger.warning(
                                f"LinkedIn: No se pudo extraer descripción para '{title}' @ '{company}'. Omitiendo."
                            )
                            continue

                        job = Job(
                            title=title,
                            company=company,
                            location=job_location,
                            description=job_desc,
                            url=clean_url,
                            source=self.name,
                            salary=None,
                            job_type=None,
                            posted_at=posted_at,
                        )
                        jobs_found.append(job)
                        logger.info(f"LinkedIn: Extraída vacante '{job.title}' @ '{job.company}'")

                        if len(jobs_found) >= limit:
                            break

                except Exception as e:
                    logger.exception(f"LinkedIn: Error durante scraping: {e}")

                # Pausa entre combinaciones de búsqueda
                time.sleep(random.uniform(2.0, 4.0))

        return jobs_found
