import json
import logging
import re
import urllib.parse
from datetime import UTC, datetime

from bs4 import BeautifulSoup
from curl_cffi import requests

from src.scraper.base import RawJobCard, WebScraper

logger = logging.getLogger(__name__)


class IndeedScraper(WebScraper):
    """Scraper de Indeed que hereda throttling, circuit breaker y dedup de WebScraper."""

    # Mapa de subdominios regionales de Indeed
    REGIONAL_DOMAINS = {
        (
            "chile",
            "santiago",
            "vina",
            "viña",
            "valparaiso",
            "valparaíso",
            "concepcion",
            "concepción",
        ): "https://cl.indeed.com",
        ("mexico", "méxico", "cdmx", "guadalajara"): "https://mx.indeed.com",
        ("spain", "españa", "madrid", "barcelona"): "https://es.indeed.com",
        ("argentina", "buenos aires"): "https://ar.indeed.com",
        ("colombia", "bogota", "bogotá", "medellin"): "https://co.indeed.com",
    }

    def __init__(self, rate_limit_config: dict | None = None):
        super().__init__(name="indeed", rate_limit_config=rate_limit_config)

    def _get_base_url(self, location: str) -> str:
        """Determina el subdominio regional de Indeed según la ubicación."""
        loc = location.lower()
        for terms, domain in self.REGIONAL_DOMAINS.items():
            if any(term in loc for term in terms):
                return domain
        return "https://www.indeed.com"

    def _build_search_url(self, keyword: str, location: str) -> str:
        base_url = self._get_base_url(location)
        params = {"q": keyword, "l": location, "from": "searchOnHP"}
        return f"{base_url}/jobs?{urllib.parse.urlencode(params)}"

    def _parse_search_results(self, response: requests.Response, location: str) -> list[RawJobCard]:
        base_url = self._get_base_url(location)
        pattern = r'window\.mosaic\.providerData\["mosaic-provider-jobcards"\]\s*=\s*(\{.+?\});'
        match = re.search(pattern, response.text, re.DOTALL)

        if not match:
            return []

        try:
            data = json.loads(match.group(1))
            raw_results = (
                data.get("metaData", {}).get("mosaicProviderJobCardsModel", {}).get("results", [])
            )
        except Exception as e:
            logger.error(f"Indeed: Error parseando JSON de Mosaic: {e}")
            return []

        cards = []
        for r in raw_results:
            jobkey = r.get("jobkey") or r.get("jk") or r.get("jobKey")
            if not jobkey:
                continue

            posted_at = None
            pub_date_ms = r.get("pubDate")
            if pub_date_ms:
                try:
                    posted_at = datetime.fromtimestamp(pub_date_ms / 1000.0, tz=UTC)
                except Exception:
                    pass

            salary_info = r.get("salarySnippet") or r.get("estimatedSalary")
            salary = salary_info.get("text") if salary_info else None

            cards.append(
                RawJobCard(
                    title=r.get("title", ""),
                    company=r.get("company", ""),
                    location=r.get("formattedLocation", location),
                    url=f"{base_url}/viewjob?jk={jobkey}",
                    salary=salary,
                    job_type=r.get("jobCardRequirementsModel", {}).get("jobTypes") or None,
                    posted_at=posted_at,
                )
            )
        return cards

    def _extract_description(self, url: str, response: requests.Response) -> str | None:
        soup = BeautifulSoup(response.text, "html.parser")
        desc_div = soup.find(id="jobDescriptionText")
        return desc_div.get_text(separator="\n").strip() if desc_div else None
