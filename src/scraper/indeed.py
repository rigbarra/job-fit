import re
import json
import time
import random
import logging
from typing import List, Optional
from datetime import datetime
import urllib.parse
from bs4 import BeautifulSoup
from curl_cffi import requests

from src.scraper.base import BaseScraper
from src.database.models import Job
from src.database.repository import is_duplicate

logger = logging.getLogger(__name__)

class IndeedScraper(BaseScraper):
    def __init__(self):
        super().__init__(name="indeed")
        self.base_url = "https://www.indeed.com"

    def fetch_jobs(self, keywords: List[str], locations: List[str], limit: int = 20) -> List[Job]:
        """
        Extrae vacantes de Indeed emulando TLS de Chrome con curl_cffi y
        parseando el JSON embebido en la página.
        """
        jobs_found: List[Job] = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive"
        }

        # Iteramos por keywords y localizaciones para ampliar el espectro de búsqueda
        for keyword in keywords:
            for location in locations:
                if len(jobs_found) >= limit:
                    break

                query_params = {
                    "q": keyword,
                    "l": location,
                    "from": "searchOnHP",
                }
                
                # Codificar manualmente para evitar problemas de formato
                query_string = urllib.parse.urlencode(query_params)
                search_url = f"{self.base_url}/jobs?{query_string}"
                
                try:
                    logger.info(f"Indeed: Buscando '{keyword}' en '{location}'...")
                    
                    # Usamos curl_cffi con impersonación de Chrome 120
                    response = requests.get(
                        search_url,
                        headers=headers,
                        impersonate="chrome120",
                        timeout=20
                    )
                    
                    if response.status_code == 403:
                        logger.error("Indeed: Acceso bloqueado (403 Forbidden). Cloudflare ha bloqueado la petición.")
                        continue
                    elif response.status_code != 200:
                        logger.error(f"Indeed: Error {response.status_code} al consultar búsqueda.")
                        continue

                    # Extraer el modelo de datos Mosaic embebido en la página
                    # Contiene toda la información de los resultados en JSON
                    pattern = r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{.+?\});'
                    match = re.search(pattern, response.text, re.DOTALL)
                    
                    if not match:
                        logger.warning("Indeed: No se encontró el blob JSON 'mosaic-provider-jobcards'.")
                        # Intentar fallback alternativo de regex
                        alt_pattern = r'window\.mosaic\.providerData\s*=\s*(\{.+?\});'
                        alt_match = re.search(alt_pattern, response.text, re.DOTALL)
                        if alt_match:
                            try:
                                provider_data = json.loads(alt_match.group(1))
                                raw_results = (provider_data
                                               .get("mosaic-provider-jobcards", {})
                                               .get("metaData", {})
                                               .get("mosaicProviderJobCardsModel", {})
                                               .get("results", []))
                            except Exception:
                                raw_results = []
                        else:
                            raw_results = []
                    else:
                        try:
                            data = json.loads(match.group(1))
                            raw_results = (data
                                           .get("metaData", {})
                                           .get("mosaicProviderJobCardsModel", {})
                                           .get("results", []))
                        except Exception as je:
                            logger.error(f"Indeed: Error parseando JSON de Mosaic: {je}")
                            raw_results = []

                    logger.info(f"Indeed: Encontrados {len(raw_results)} resultados en la página.")

                    for job_data in raw_results:
                        if len(jobs_found) >= limit:
                            break

                        # Extraer Job Key (jk) único de Indeed
                        jk = job_data.get("jk")
                        if not jk:
                            continue

                        job_url = f"{self.base_url}/viewjob?jk={jk}"

                        # 1. DEDUPLICACIÓN PREVIA: Si ya está en BD, no gastamos peticiones de red
                        if is_duplicate(job_url):
                            logger.debug(f"Indeed: Omitiendo duplicado '{jk}'")
                            continue

                        # Throttling antes de descargar descripción detallada
                        time.sleep(random.uniform(2.5, 4.5))

                        # 2. Descargar la descripción completa del puesto
                        job_desc = self._fetch_job_description(job_url, headers)
                        if not job_desc:
                            logger.warning(f"Indeed: No se pudo obtener la descripción para '{jk}'")
                            continue

                        # Obtener fecha de publicación
                        posted_at = None
                        pub_date_ms = job_data.get("pubDate")  # epoch en ms
                        if pub_date_ms:
                            try:
                                posted_at = datetime.fromtimestamp(pub_date_ms / 1000.0)
                            except Exception:
                                pass

                        # Parsear sueldo
                        salary_text = None
                        salary_info = job_data.get("salarySnippet") or job_data.get("estimatedSalary")
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
                            job_type=job_data.get("jobCardRequirementsModel", {}).get("jobTypes") or None,
                            posted_at=posted_at
                        )
                        jobs_found.append(job)
                        logger.info(f"Indeed: Ingerida vacante '{job.title}' de '{job.company}'")

                except Exception as e:
                    logger.exception(f"Indeed: Error durante scraping: {e}")
                
                # Esperar entre queries de búsqueda
                time.sleep(random.uniform(4.0, 7.0))

        return jobs_found[:limit]

    def _fetch_job_description(self, url: str, headers: dict) -> Optional[str]:
        """Descarga una vacante y extrae el texto limpio de la descripción."""
        try:
            response = requests.get(
                url,
                headers=headers,
                impersonate="chrome120",
                timeout=15
            )
            if response.status_code != 200:
                return None

            soup = BeautifulSoup(response.text, "html.parser")
            
            # Selectores comunes en Indeed para la descripción del puesto
            desc_div = soup.find(id="jobDescriptionText")
            if desc_div:
                return desc_div.get_text(separator="\n").strip()
            
            # Fallback en caso de que cambie el ID
            fallback_div = soup.find(class_="jobsearch-JobComponent-description")
            if fallback_div:
                return fallback_div.get_text(separator="\n").strip()
                
            return None
        except Exception as e:
            logger.error(f"Indeed: Error descargando descripción {url}: {e}")
            return None
