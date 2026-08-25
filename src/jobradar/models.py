from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Vacancy:
    source: str
    external_id: str
    title: str
    company: str
    url: str
    published_at: str
    area: str = ""
    schedule: str = ""
    experience: str = ""
    salary_from: int | None = None
    salary_to: int | None = None
    salary_currency: str | None = None
    snippet: str = ""
    professional_roles: tuple[str, ...] = ()

    @classmethod
    def from_hh_item(cls, item: dict[str, Any]) -> "Vacancy":
        salary = item.get("salary") or {}
        snippet = item.get("snippet") or {}
        text = " ".join(
            part for part in [snippet.get("requirement") or "", snippet.get("responsibility") or ""] if part
        )
        roles = tuple(
            role.get("name", "")
            for role in (item.get("professional_roles") or [])
            if role.get("name")
        )
        return cls(
            source="hh",
            external_id=str(item["id"]),
            title=item.get("name") or "",
            company=(item.get("employer") or {}).get("name") or "",
            url=item.get("alternate_url") or "",
            published_at=item.get("published_at") or "",
            area=(item.get("area") or {}).get("name") or "",
            schedule=(item.get("schedule") or {}).get("name") or "",
            experience=(item.get("experience") or {}).get("name") or "",
            salary_from=salary.get("from"),
            salary_to=salary.get("to"),
            salary_currency=salary.get("currency"),
            snippet=text,
            professional_roles=roles,
        )

    @property
    def searchable_text(self) -> str:
        return " ".join(
            [self.title, self.company, self.snippet, self.schedule, self.experience, *self.professional_roles]
        ).lower()


@dataclass(frozen=True)
class ScoreResult:
    total: int
    matched: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class RankedVacancy:
    vacancy: Vacancy
    score: ScoreResult
    local_id: int | None = field(default=None, compare=False)
