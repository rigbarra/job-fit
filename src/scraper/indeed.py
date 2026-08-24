import logging
from datetime import date, datetime
import pandas as pd
from jobspy import scrape_jobs
from src.database.models import Job

logger = logging.getLogger(__name__)


class IndeedScraper:
    """Scraper de Indeed que utiliza la librería JobSpy para mayor resiliencia contra bloqueos."""

    def __init__(self, rate_limit_config: dict | None = None):
        self.name = "indeed"

    def fetch_jobs(self, keywords: list[str], locations: list[str], limit: int = 20) -> list[Job]:
        jobs_found: list[Job] = []

        # Determinar el país para Indeed (Chile vs fallback)
        is_chile = any("chile" in loc.lower() or "santiago" in loc.lower() for loc in locations)
        country_param = "chile" if is_chile else "USA"

        for keyword in keywords:
            for location in locations:
                if len(jobs_found) >= limit:
                    break

                try:
                    logger.info(
                        f"Indeed (JobSpy): Buscando '{keyword}' en '{location}' (país: {country_param})..."
                    )
                    results = scrape_jobs(
                        site_name=["indeed"],
                        search_term=keyword,
                        location=location,
                        results_wanted=limit,
                        hours_old=168,  # Rango de 7 días
                        country_indeed=country_param,
                    )

                    if results is None or len(results) == 0:
                        logger.info(
                            f"Indeed (JobSpy): No se encontraron resultados para '{keyword}' en '{location}'"
                        )
                        continue

                    for _, row in results.iterrows():
                        title = str(row.get("title", "")).strip()
                        company = str(row.get("company", "")).strip()
                        url = str(row.get("job_url", "")).strip()
                        description = str(row.get("description", "")).strip()

                        if not title or not url:
                            continue

                        # Parsear fecha de publicación
                        posted_date = row.get("date_posted")
                        posted_at = None
                        if pd.notna(posted_date):
                            if isinstance(posted_date, (date, datetime)):
                                if isinstance(posted_date, date) and not isinstance(
                                    posted_date, datetime
                                ):
                                    posted_at = datetime.combine(posted_date, datetime.min.time())
                                else:
                                    posted_at = posted_date
                            else:
                                try:
                                    posted_at = datetime.strptime(str(posted_date), "%Y-%m-%d")
                                except Exception:
                                    pass

                        # Parsear rango salarial
                        salary = None
                        min_amt = row.get("min_amount")
                        max_amt = row.get("max_amount")
                        currency = row.get("currency")
                        if pd.notna(min_amt) or pd.notna(max_amt):
                            curr_str = str(currency) if pd.notna(currency) else "USD"
                            if pd.notna(min_amt) and pd.notna(max_amt):
                                salary = f"{curr_str} ${min_amt} - ${max_amt} / mes"
                            elif pd.notna(min_amt):
                                salary = f"Desde {curr_str} ${min_amt} / mes"
                            elif pd.notna(max_amt):
                                salary = f"Hasta {curr_str} ${max_amt} / mes"

                        # Construir objeto Job
                        job = Job(
                            title=title,
                            company=company or "Empresa Confidencial",
                            location=str(row.get("location", location)).strip(),
                            description=description or title,
                            url=url,
                            source=self.name,
                            salary=salary,
                            job_type=str(row.get("job_type", "Full-time"))
                            if pd.notna(row.get("job_type"))
                            else "Full-time",
                            posted_at=posted_at,
                        )
                        jobs_found.append(job)

                except Exception as e:
                    logger.error(f"Indeed (JobSpy): Error extrayendo vacantes: {e}")

        return jobs_found
