import json
import logging
import random
import re
import time
import urllib.parse
from datetime import UTC, datetime

from bs4 import BeautifulSoup
from curl_cffi import requests

from src.database.models import Job
from src.database.repository import is_duplicate
from src.scraper.base import BaseScraper

logger = logging.getLogger(__name__)


class IndeedScraper(BaseScraper):
    def __init__(self, rate_limit_config: dict | None = None):
        super().__init__(name="indeed")
        self.default_base_url = "https://www.indeed.com"
        self.rate_config = rate_limit_config or {
            "min_delay_seconds": 3.0,
            "max_delay_seconds": 6.0,
            "max_errors_before_circuit_break": 1,
        }

    def get_base_url_for_location(self, location: str) -> str:
        """Determina el subdominio regional de Indeed según la ubicación geográfica."""
        loc = location.lower()
        if any(
            term in loc
            for term in [
                "chile",
                "santiago",
                "vina",
                "viña",
                "valparaiso",
                "valparaíso",
                "concepcion",
                "concepción",
            ]
        ):
            return "https://cl.indeed.com"
        elif any(term in loc for term in ["mexico", "méxico", "cdmx", "guadalajara"]):
            return "https://mx.indeed.com"
        elif any(term in loc for term in ["spain", "españa", "madrid", "barcelona"]):
            return "https://es.indeed.com"
        elif any(term in loc for term in ["argentina", "buenos aires"]):
            return "https://ar.indeed.com"
        elif any(term in loc for term in ["colombia", "bogota", "bogotá", "medellin"]):
            return "https://co.indeed.com"
        return self.default_base_url

    def fetch_jobs(self, keywords: list[str], locations: list[str], limit: int = 20) -> list[Job]:
        """
        Extrae vacantes de Indeed emulando TLS de Chrome con curl_cffi y
        parseando el JSON embebido en la página, con circuit breaker de bloqueos.
        """
        jobs_found: list[Job] = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "es-CL,es;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }

        consecutive_blocks = 0
        max_blocks = self.rate_config.get("max_errors_before_circuit_break", 1)

        # Iteramos por keywords y localizaciones
        for keyword in keywords:
            for location in locations:
                if len(jobs_found) >= limit:
                    break

                # Si el circuit breaker se activó, abortamos todo el scraper
                if consecutive_blocks >= max_blocks:
                    logger.critical(
                        "Indeed: Circuit Breaker activado. Abortando búsqueda para evitar blacklist de IP."
                    )
                    return jobs_found

                base_url = self.get_base_url_for_location(location)
                query_params = {
                    "q": keyword,
                    "l": location,
                    "from": "searchOnHP",
                }

                query_string = urllib.parse.urlencode(query_params)
                search_url = f"{base_url}/jobs?{query_string}"

                try:
                    logger.info(f"Indeed: Buscando '{keyword}' en '{location}' ({base_url})...")

                    response = requests.get(
                        search_url, headers=headers, impersonate="chrome120", timeout=20
                    )

                    if response.status_code in [403, 429]:
                        consecutive_blocks += 1
                        logger.error(f"Indeed: Acceso bloqueado ({response.status_code}).")
                        continue
                    elif response.status_code != 200:
                        logger.error(f"Indeed: Error {response.status_code} al consultar búsqueda.")
                        continue

                    # Si la respuesta es exitosa, reiniciamos el contador de bloqueos
                    consecutive_blocks = 0

                    pattern = r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{.+?\});'
                    match = re.search(pattern, response.text, re.DOTALL)

                    raw_results = []
                    if match:
                        try:
                            data = json.loads(match.group(1))
                            raw_results = (
                                data.get("metaData", {})
                                .get("mosaicProviderJobCardsModel", {})
                                .get("results", [])
                            )
                        except Exception as je:
                            logger.error(f"Indeed: Error parseando JSON de Mosaic: {je}")

                    logger.info(f"Indeed: Encontrados {len(raw_results)} resultados en la página.")

                    for job_data in raw_results:
                        if len(jobs_found) >= limit:
                            break

                        if consecutive_blocks >= max_blocks:
                            logger.critical(
                                "Indeed: Circuit Breaker activado en medio de descarga de detalles. Abortando."
                            )
                            return jobs_found

                        # Indeed puede identificar el job por 'jobkey' o 'jk'
                        jobkey = (
                            job_data.get("jobkey") or job_data.get("jk") or job_data.get("jobKey")
                        )
                        if not jobkey:
                            continue

                        job_url = f"{base_url}/viewjob?jk={jobkey}"

                        # 1. DEDUPLICACIÓN PREVIA: Si ya está en BD, no gastamos peticiones de red
                        if is_duplicate(job_url):
                            logger.debug(f"Indeed: Omitiendo duplicado '{jobkey}'")
                            continue

                        # Throttling antes de descargar descripción detallada
                        delay = random.uniform(
                            self.rate_config.get("min_delay_seconds", 3.0),
                            self.rate_config.get("max_delay_seconds", 6.0),
                        )
                        logger.info(
                            f"Indeed: Esperando {delay:.2f} segundos antes de consultar descripción..."
                        )
                        time.sleep(delay)

                        # 2. Descargar la descripción completa del puesto
                        try:
                            desc_response = requests.get(
                                job_url, headers=headers, impersonate="chrome120", timeout=15
                            )

                            if desc_response.status_code in [403, 429]:
                                consecutive_blocks += 1
                                logger.error(
                                    f"Indeed: Bloqueo detectado al bajar descripción ({desc_response.status_code})."
                                )
                                continue

                            if desc_response.status_code != 200:
                                continue

                            soup = BeautifulSoup(desc_response.text, "html.parser")
                            desc_div = soup.find(id="jobDescriptionText")
                            job_desc = (
                                desc_div.get_text(separator="\n").strip() if desc_div else None
                            )

                        except Exception as de:
                            logger.error(f"Indeed: Error de conexión bajando descripción: {de}")
                            continue

                        if not job_desc:
                            continue

                        # Obtener fecha de publicación
                        posted_at = None
                        pub_date_ms = job_data.get("pubDate")
                        if pub_date_ms:
                            try:
                                posted_at = datetime.fromtimestamp(pub_date_ms / 1000.0, tz=UTC)
                            except Exception:
                                pass

                        # Parsear sueldo
                        salary_text = None
                        salary_info = job_data.get("salarySnippet") or job_data.get(
                            "estimatedSalary"
                        )
                        if salary_info:
                            salary_text = salary_info.get("text")

                        job = Job(
                            title=job_data.get("title", ""),
                            company=job_data.get("company", ""),
                            location=job_data.get("formattedLocation", location),
                            description=job_desc,
                            url=job_url,
                            source=self.name,
                            salary=salary_text,
                            job_type=job_data.get("jobCardRequirementsModel", {}).get("jobTypes")
                            or None,
                            posted_at=posted_at,
                        )
                        jobs_found.append(job)
                        logger.info(f"Indeed: Extraída vacante '{job.title}' @ '{job.company}'")

                        if len(jobs_found) >= limit:
                            break

                except Exception as e:
                    logger.exception(f"Indeed: Error durante scraping: {e}")

                # Esperar entre queries de búsqueda
                time.sleep(random.uniform(2.0, 4.0))

        return jobs_found
