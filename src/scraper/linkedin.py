import re
import urllib.parse
from datetime import UTC, datetime, timedelta

from bs4 import BeautifulSoup
from curl_cffi import requests

from src.agent.filter import CHILE_TERMS
from src.scraper.base import RawJobCard, WebScraper


class LinkedInScraper(WebScraper):
    """Scraper de LinkedIn Guest API que hereda throttling, circuit breaker y dedup de WebScraper."""

    SEARCH_API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

    def __init__(
        self,
        rate_limit_config: dict | None = None,
        max_job_age_days: int = 3,
        experience_level: str = "4",
    ):
        config = {"min_delay_seconds": 4.0, "max_delay_seconds": 8.0}
        config.update(rate_limit_config or {})
        super().__init__(name="linkedin", rate_limit_config=config)
        self.max_job_age_days = max_job_age_days
        self.experience_level = experience_level

    def _build_search_url(self, keyword: str, location: str) -> str:
        tpr_seconds = self.max_job_age_days * 86400
        loc_lower = location.lower()

        is_chile = any(term in loc_lower for term in CHILE_TERMS)

        # f_WT: 1 = On-site (Presencial), 2 = Remote (Remoto), 3 = Hybrid (Híbrido)
        # - Para internacional: Exigir f_WT=2 (100% Remoto exclusivamente)
        # - Para Chile: Exigir f_WT=2,3 (Remoto o Híbrido, descartando on-site puro de raíz)
        f_wt = "2,3" if is_chile else "2"

        params = {
            "keywords": keyword,
            "location": location,
            "start": 0,
            "f_TPR": f"r{tpr_seconds}",
            "f_WT": f_wt,
        }
        if self.experience_level:
            params["f_E"] = self.experience_level

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
            if time_el:
                time_text = time_el.get_text().strip().lower()
                m_hours = re.search(r"(\d+)\s*(hour|hora)", time_text)
                m_mins = re.search(r"(\d+)\s*(minute|minuto|min)", time_text)
                m_days = re.search(r"(\d+)\s*(day|dia|día)", time_text)

                now_utc = datetime.now(tz=UTC)
                if m_hours:
                    posted_at = now_utc - timedelta(hours=int(m_hours.group(1)))
                elif m_mins:
                    posted_at = now_utc - timedelta(minutes=int(m_mins.group(1)))
                elif m_days:
                    posted_at = now_utc - timedelta(days=int(m_days.group(1)))
                elif time_el.get("datetime"):
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
