import logging
import urllib.parse
from datetime import UTC, datetime

from bs4 import BeautifulSoup
from curl_cffi import requests

from src.scraper.base import RawJobCard, WebScraper

logger = logging.getLogger(__name__)


class LinkedInScraper(WebScraper):
    """Scraper de LinkedIn Guest API que hereda throttling, circuit breaker y dedup de WebScraper."""

    SEARCH_API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

    def __init__(self, rate_limit_config: dict | None = None):
        config = {"min_delay_seconds": 4.0, "max_delay_seconds": 8.0}
        config.update(rate_limit_config or {})
        super().__init__(name="linkedin", rate_limit_config=config)

    def _build_search_url(self, keyword: str, location: str) -> str:
        params = {"keywords": keyword, "location": location, "start": 0}
        return f"{self.SEARCH_API}?{urllib.parse.urlencode(params)}"

    def _parse_search_results(self, response: requests.Response, location: str) -> list[RawJobCard]:
        soup = BeautifulSoup(response.text, "html.parser")
        html_cards = soup.find_all(
            ["li", "div"], class_=lambda c: c and "base-card" in c
        ) or soup.find_all("li")

        cards = []
        for card in html_cards:
            title_el = card.find(class_="base-search-card__title")
            link_el = card.find("a", class_="base-card__full-link")

            if not title_el or not link_el or not link_el.get("href"):
                continue

            company_el = card.find(class_="base-search-card__subtitle")
            loc_el = card.find(class_="job-search-card__location")
            time_el = card.find("time")

            # Limpiar parámetros de tracking de la URL
            clean_url = link_el["href"].strip().split("?")[0]

            posted_at = None
            if time_el and time_el.get("datetime"):
                try:
                    posted_at = datetime.fromisoformat(time_el["datetime"]).replace(tzinfo=UTC)
                except Exception:
                    pass

            cards.append(
                RawJobCard(
                    title=title_el.get_text().strip(),
                    company=company_el.get_text().strip() if company_el else "Empresa Confidencial",
                    location=loc_el.get_text().strip() if loc_el else location,
                    url=clean_url,
                    posted_at=posted_at,
                )
            )
        return cards

    def _extract_description(self, url: str, response: requests.Response) -> str | None:
        soup = BeautifulSoup(response.text, "html.parser")
        desc_el = (
            soup.find(class_="show-more-less-html__markup")
            or soup.find(class_="description__text")
            or soup.find(id="job-details")
        )
        return desc_el.get_text(separator="\n").strip() if desc_el else None
