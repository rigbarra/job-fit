import logging
from datetime import datetime, timezone
from curl_cffi import requests

from src.database.models import Job

logger = logging.getLogger(__name__)


class GetOnBoardScraper:
    """
    Scraper para Get on Board (getonbrd.com), la plataforma líder en Chile
    y Latinoamérica para empleos de TI, Data Engineering y Analytics.
    Utiliza la API REST v0 oficial para una ingesta rápida, estructurada y resiliente.
    """

    def __init__(self, rate_limit_config: dict | None = None):
        self.name = "getonboard"
        self.api_url = "https://www.getonbrd.com/api/v0/search/jobs"
        self.category_url = "https://www.getonbrd.com/api/v0/categories/data-science-analytics/jobs"
        self.company_url_fmt = "https://www.getonbrd.com/api/v0/companies/{}"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        self._company_cache: dict[int, str] = {}

    def _get_company_name(self, company_id: int | None) -> str:
        """Obtiene el nombre de la empresa por ID desde la API v0 con caché en memoria."""
        if not company_id:
            return "Empresa Confidencial / Get on Board"
        if company_id in self._company_cache:
            return self._company_cache[company_id]

        try:
            url = self.company_url_fmt.format(company_id)
            resp = requests.get(url, headers=self.headers, impersonate="chrome120", timeout=5)
            if resp.status_code == 200:
                name = resp.json().get("data", {}).get("attributes", {}).get("name")
                if name:
                    self._company_cache[company_id] = name
                    return name
        except Exception:
            pass

        fallback = f"Empresa (ID: {company_id})"
        self._company_cache[company_id] = fallback
        return fallback

    def _parse_job_item(self, item: dict) -> Job | None:
        """Parsea un objeto de trabajo de la API de Get on Board."""
        attr = item.get("attributes", {})
        links = item.get("links", {})
        public_url = links.get("public_url")
        if not public_url or not attr.get("title"):
            return None

        title = attr.get("title", "").strip()

        # Extraer ID de la empresa
        company_id = None
        company_data = attr.get("company", {}).get("data", {})
        if isinstance(company_data, dict):
            company_id = company_data.get("id")

        company_name = self._get_company_name(company_id)

        # Construir descripción legible en Markdown combinando campos relevantes
        desc_parts = []
        if attr.get("description"):
            desc_parts.append(f"### Descripción del Puesto:\n{attr['description']}")
        if attr.get("projects"):
            desc_parts.append(f"### Proyectos:\n{attr['projects']}")
        if attr.get("functions"):
            desc_parts.append(f"### Funciones Principales:\n{attr['functions']}")
        if attr.get("desirable"):
            desc_parts.append(f"### Requisitos Deseables:\n{attr['desirable']}")

        full_description = "\n\n".join(desc_parts) if desc_parts else title

        # Formatear rango salarial
        salary = None
        min_sal = attr.get("min_salary")
        max_sal = attr.get("max_salary")
        if min_sal or max_sal:
            if min_sal and max_sal:
                salary = f"USD ${min_sal} - ${max_sal} / mes"
            elif min_sal:
                salary = f"Desde USD ${min_sal} / mes"
            elif max_sal:
                salary = f"Hasta USD ${max_sal} / mes"

        # Ubicación y modalidad
        countries = attr.get("countries", [])
        remote_modality = attr.get("remote_modality", "")
        location_parts = []
        if countries:
            location_parts.extend(countries)
        if remote_modality:
            location_parts.append(f"({remote_modality})")
        location = " ".join(location_parts) if location_parts else "Chile / Remote"

        # Fecha de publicación
        posted_at = None
        pub_ts = attr.get("published_at")
        if pub_ts:
            try:
                posted_at = datetime.fromtimestamp(pub_ts, tz=timezone.utc).replace(tzinfo=None)
            except Exception:
                posted_at = datetime.now()

        # Modalidad de empleo
        job_type = "Full-time"
        if attr.get("remote"):
            job_type += " / Remote"

        return Job(
            title=title,
            company=company_name,
            location=location,
            description=full_description,
            url=public_url,
            source=self.name,
            salary=salary,
            job_type=job_type,
            posted_at=posted_at,
        )

    def fetch_jobs(self, keywords: list[str], locations: list[str], limit: int = 20) -> list[Job]:
        """Extrae vacantes desde Get on Board por categoría y keywords."""
        jobs_found: list[Job] = []
        seen_urls: set[str] = set()

        # 1. Ingesta por categoría principal Data Science / Analytics
        try:
            logger.info("GetOnBoard: Ingestando categoría Data Science / Analytics...")
            resp = requests.get(
                self.category_url,
                params={"per_page": limit},
                headers=self.headers,
                impersonate="chrome120",
                timeout=15,
            )
            if resp.status_code == 200:
                cat_data = resp.json().get("data", [])
                for item in cat_data:
                    job = self._parse_job_item(item)
                    if job and job.url not in seen_urls:
                        seen_urls.add(job.url)
                        jobs_found.append(job)
        except Exception as e:
            logger.warning(f"GetOnBoard: Error al consultar categoría: {e}")

        # 2. Ingesta por Búsqueda de Keywords
        for kw in keywords:
            if len(jobs_found) >= limit:
                break
            try:
                logger.info(f"GetOnBoard: Buscando keyword '{kw}'...")
                resp = requests.get(
                    self.api_url,
                    params={"query": kw, "per_page": limit},
                    headers=self.headers,
                    impersonate="chrome120",
                    timeout=15,
                )
                if resp.status_code == 200:
                    search_data = resp.json().get("data", [])
                    for item in search_data:
                        job = self._parse_job_item(item)
                        if job and job.url not in seen_urls:
                            seen_urls.add(job.url)
                            jobs_found.append(job)
                        if len(jobs_found) >= limit:
                            break
            except Exception as e:
                logger.warning(f"GetOnBoard: Error al buscar keyword '{kw}': {e}")

        logger.info(f"GetOnBoard: Ingesta finalizada con {len(jobs_found)} vacantes.")
        return jobs_found[:limit]
