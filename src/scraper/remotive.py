import logging
from datetime import datetime

from curl_cffi import requests

from src.database.models import Job

logger = logging.getLogger(__name__)


class RemotiveScraper:
    def __init__(self):
        self.name = "remotive"
        self.api_url = "https://remotive.com/api/remote-jobs"

    def fetch_jobs(self, keywords: list[str], locations: list[str], limit: int = 20) -> list[Job]:
        """
        Extrae vacantes desde la API REST pública de Remotive.
        Al ser una API pública y estable, es nuestra fuente principal sin costo.
        """
        jobs_found: list[Job] = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        # La API de Remotive recomienda no hacer peticiones demasiado rápido.
        # Hacemos una petición por cada keyword.
        for keyword in keywords:
            params = {"search": keyword, "limit": limit}
            try:
                logger.info(f"Remotive: Buscando '{keyword}'...")
                response = requests.get(
                    self.api_url,
                    params=params,
                    headers=headers,
                    impersonate="chrome120",
                    timeout=15,
                )

                if response.status_code != 200:
                    logger.error(
                        f"Remotive API retornó código {response.status_code} para keyword '{keyword}'"
                    )
                    continue

                payload = response.json()
                raw_jobs = payload.get("jobs", [])

                for rj in raw_jobs:
                    # Filtrado básico por ubicación geográfica requerida por la empresa
                    req_location = rj.get("candidate_required_location", "").lower()

                    # Si especificamos ubicaciones en la configuración (ej: "Chile"),
                    # filtramos las vacantes que no sean "Worldwide" o no coincidan.
                    location_matched = False
                    if not locations:
                        location_matched = True
                    else:
                        for loc in locations:
                            loc_lower = loc.lower()
                            if (
                                loc_lower in req_location
                                or any(
                                    term in req_location
                                    for term in [
                                        "worldwide",
                                        "latam",
                                        "latin america",
                                        "anywhere",
                                        "global",
                                        "remote",
                                        "contractor",
                                    ]
                                )
                                or req_location == ""
                            ):
                                location_matched = True
                                break

                    if not location_matched:
                        continue

                    # Filtrado básico por título para descartar puestos administrativos/ventas/redacción
                    job_title = rj.get("title", "")
                    job_title_lower = job_title.lower()
                    data_keywords = [
                        "data",
                        "analytics",
                        "bi",
                        "dbt",
                        "etl",
                        "pipeline",
                        "intelligence",
                        "datos",
                        "ia",
                        "ai",
                    ]
                    if not any(kw in job_title_lower for kw in data_keywords):
                        continue

                    # Convertir fecha de publicación
                    posted_at = None
                    pub_date_str = rj.get("publication_date")
                    if pub_date_str:
                        try:
                            # Remotive usa formato ISO: '2026-08-13T06:25:53'
                            posted_at = datetime.fromisoformat(pub_date_str)
                        except ValueError:
                            pass

                    # Crear modelo Job de SQLModel
                    job = Job(
                        title=rj.get("title", ""),
                        company=rj.get("company_name", ""),
                        location=rj.get("candidate_required_location", "Remote"),
                        description=rj.get("description", ""),
                        url=rj.get("url", ""),
                        source=self.name,
                        salary=rj.get("salary") or None,
                        job_type=rj.get("job_type") or None,
                        posted_at=posted_at,
                    )
                    jobs_found.append(job)

                    if len(jobs_found) >= limit:
                        break

            except Exception as e:
                logger.exception(f"Error consultando Remotive para keyword '{keyword}': {e}")

            if len(jobs_found) >= limit:
                break

        return jobs_found[:limit]
