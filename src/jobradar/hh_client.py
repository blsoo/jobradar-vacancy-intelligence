from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import Vacancy


class HHClient:
    BASE_URL = "https://api.hh.ru"

    def __init__(self, user_agent: str, timeout: int = 15) -> None:
        self.user_agent = user_agent
        self.timeout = timeout

    def search(
        self,
        query: str,
        *,
        area: str = "113",
        per_page: int = 50,
        page: int = 0,
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
        request = Request(
            f"{self.BASE_URL}/vacancies?{params}",
            headers={
                "HH-User-Agent": self.user_agent,
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )
        with urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return [Vacancy.from_hh_item(item) for item in payload.get("items", [])]

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
