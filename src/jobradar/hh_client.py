from __future__ import annotations

import html
import json
import re
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .models import Vacancy


class HHClient:
    API_BASE_URL = "https://api.hh.ru"
    RSS_BASE_URL = "https://hh.ru/search/vacancy/rss"
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "Chrome/126 Safari/537.36 JobRadar/0.1"
    )

    def __init__(self, user_agent: str, oauth_token: str = "", timeout: int = 15) -> None:
        self.user_agent = user_agent
        self.oauth_token = oauth_token.strip()
        self.timeout = timeout

    @staticmethod
    def _plain_text(value: str) -> str:
        text = re.sub(r"<[^>]+>", " ", value or "")
        return re.sub(r"\s+", " ", html.unescape(text)).strip()

    @classmethod
    def _description_field(cls, raw_html: str, label: str) -> str:
        match = re.search(
            rf"<p>\s*{re.escape(label)}\s*(.*?)\s*</p>",
            raw_html or "",
            flags=re.IGNORECASE | re.DOTALL,
        )
        return cls._plain_text(match.group(1)) if match else ""

    @staticmethod
    def _salary_from_text(value: str) -> tuple[int | None, int | None, str | None]:
        normalized = html.unescape(value or "").replace("\u202f", " ").replace("\xa0", " ").strip()
        if not normalized or "не указан" in normalized.lower():
            return None, None, None

        raw_numbers = re.findall(r"\d[\d\s]*", normalized)
        numbers: list[int] = []
        for raw in raw_numbers:
            digits = re.sub(r"\D", "", raw)
            if digits:
                numbers.append(int(digits))
        if not numbers:
            return None, None, None

        lower = normalized.lower()
        currency = "RUR" if any(mark in lower for mark in ("₽", "руб", "rur", "rub")) else None
        if len(numbers) >= 2:
            return numbers[0], numbers[1], currency
        if lower.startswith("до "):
            return None, numbers[0], currency
        if lower.startswith("от "):
            return numbers[0], None, currency
        return numbers[0], numbers[0], currency

    @classmethod
    def _from_rss_item(cls, item: ET.Element) -> Vacancy | None:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published_at = (item.findtext("pubDate") or "").strip()
        raw_description = item.findtext("description") or ""
        id_match = re.search(r"/vacancy/(\d+)", link)
        if not id_match or not title:
            return None

        company = cls._description_field(raw_description, "Вакансия компании:")
        area = cls._description_field(raw_description, "Регион:")
        salary_text = cls._description_field(raw_description, "Предполагаемый уровень месячного дохода:")
        salary_from, salary_to, currency = cls._salary_from_text(salary_text)

        return Vacancy(
            source="hh",
            external_id=id_match.group(1),
            title=title,
            company=company,
            url=link,
            apply_url=link,
            published_at=published_at,
            area=area,
            salary_from=salary_from,
            salary_to=salary_to,
            salary_currency=currency,
            snippet=cls._plain_text(raw_description),
        )

    def _search_api(
        self,
        query: str,
        *,
        area: str,
        per_page: int,
        page: int,
    ) -> list[Vacancy]:
        params = urlencode(
            {
                "text": query,
                "area": area,
                "per_page": min(max(per_page, 1), 100),
                "page": max(page, 0),
                "order_by": "publication_time",
                "period": 7,
            }
        )
        headers = {
            "HH-User-Agent": self.user_agent,
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }
        if self.oauth_token:
            headers["Authorization"] = f"Bearer {self.oauth_token}"
        request = Request(f"{self.API_BASE_URL}/vacancies?{params}", headers=headers)
        with urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return [Vacancy.from_hh_item(item) for item in payload.get("items", [])]

    def _search_rss(self, query: str, *, area: str) -> list[Vacancy]:
        params = urlencode({"text": query, "area": area})
        request = Request(
            f"{self.RSS_BASE_URL}?{params}",
            headers={
                "User-Agent": self.BROWSER_USER_AGENT,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
            },
        )
        with urlopen(request, timeout=self.timeout) as response:
            root = ET.fromstring(response.read())
        vacancies: list[Vacancy] = []
        for item in root.findall(".//item"):
            vacancy = self._from_rss_item(item)
            if vacancy is not None:
                vacancies.append(vacancy)
        return vacancies

    def search(
        self,
        query: str,
        *,
        area: str = "113",
        per_page: int = 50,
        page: int = 0,
    ) -> list[Vacancy]:
        # HH currently challenges anonymous API vacancy searches. Use the official
        # API when OAuth exists; otherwise use HH's public RSS search feed.
        if not self.oauth_token:
            return self._search_rss(query, area=area)
        try:
            return self._search_api(query, area=area, per_page=per_page, page=page)
        except HTTPError as exc:
            if exc.code not in {401, 403}:
                raise
            return self._search_rss(query, area=area)

    def search_many(
        self,
        queries: tuple[str, ...],
        *,
        area: str = "113",
        per_page: int = 50,
    ) -> list[Vacancy]:
        dedup: dict[str, Vacancy] = {}
        for query in queries:
            for vacancy in self.search(query, area=area, per_page=per_page):
                dedup[vacancy.external_id] = vacancy
        return list(dedup.values())
