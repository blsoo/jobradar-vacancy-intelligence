from __future__ import annotations

from .models import ScoreResult, Vacancy


POSITIVE_RULES: tuple[tuple[str, int, str], ...] = (
    ("системн", 18, "system analysis"),
    ("system analyst", 18, "system analysis"),
    ("стаж", 14, "internship"),
    ("junior", 14, "junior"),
    ("sql", 10, "SQL"),
    ("rest", 9, "REST"),
    ("http", 6, "HTTP"),
    ("json", 6, "JSON"),
    ("api", 7, "API"),
    ("swagger", 5, "Swagger/OpenAPI"),
    ("openapi", 5, "Swagger/OpenAPI"),
    ("требован", 6, "requirements"),
    ("requirements", 6, "requirements"),
    ("uml", 5, "UML"),
    ("bpmn", 5, "BPMN"),
    ("интеграц", 7, "integrations"),
    ("integration", 7, "integrations"),
    ("postgres", 5, "PostgreSQL"),
    ("git", 3, "Git"),
)

NEGATIVE_RULES: tuple[tuple[str, int, str], ...] = (
    ("senior", -35, "senior role"),
    ("ведущий", -30, "lead role"),
    ("lead ", -30, "lead role"),
    ("principal", -35, "principal role"),
    ("middle", -18, "middle role"),
    ("3–6 лет", -18, "3+ years experience"),
    ("3-6 лет", -18, "3+ years experience"),
    ("более 6 лет", -35, "6+ years experience"),
)


def _salary_rub(vacancy: Vacancy) -> int | None:
    if vacancy.salary_currency not in {None, "RUR", "RUB"}:
        return None
    values = [v for v in (vacancy.salary_from, vacancy.salary_to) if isinstance(v, int)]
    return max(values) if values else None


def score_vacancy(
    vacancy: Vacancy,
    *,
    target_salary_rub: int = 70_000,
    remote_preferred: bool = True,
) -> ScoreResult:
    text = vacancy.searchable_text
    score = 10
    matched: list[str] = []
    risks: list[str] = []
    reasons: list[str] = []
    seen_labels: set[str] = set()

    for needle, weight, label in POSITIVE_RULES:
        if needle in text and label not in seen_labels:
            score += weight
            matched.append(label)
            reasons.append(f"+{weight} {label}")
            seen_labels.add(label)

    for needle, weight, label in NEGATIVE_RULES:
        if needle in text:
            score += weight
            risks.append(label)
            reasons.append(f"{weight} {label}")

    schedule = vacancy.schedule.lower()
    if remote_preferred:
        if "удален" in schedule or "remote" in schedule or "дистан" in text:
            score += 10
            matched.append("remote")
            reasons.append("+10 remote")
        elif "офис" in text and "удален" not in text:
            score -= 4
            risks.append("office-only may be required")
            reasons.append("-4 office signal")

    salary = _salary_rub(vacancy)
    if salary is not None:
        if salary >= target_salary_rub:
            score += 8
            matched.append(f"salary ≥ {target_salary_rub:,} RUB".replace(",", " "))
            reasons.append("+8 target salary")
        elif salary < int(target_salary_rub * 0.75):
            score -= 8
            risks.append("salary below target")
            reasons.append("-8 low salary")

    if "нет опыта" in vacancy.experience.lower():
        score += 12
        matched.append("no experience required")
        reasons.append("+12 no experience")
    elif "1–3" in vacancy.experience or "1-3" in vacancy.experience:
        score += 2
        reasons.append("+2 1–3 years bucket")

    score = max(0, min(100, score))
    return ScoreResult(
        total=score,
        matched=tuple(dict.fromkeys(matched)),
        risks=tuple(dict.fromkeys(risks)),
        reasons=tuple(reasons),
    )
