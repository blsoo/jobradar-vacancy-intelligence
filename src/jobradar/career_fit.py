from __future__ import annotations

from dataclasses import dataclass

from .models import RankedVacancy


@dataclass(frozen=True)
class CareerFit:
    interest_score: int
    interest_label: str
    screening_label: str
    work_with: tuple[str, ...]
    advantages: tuple[str, ...]
    checks: tuple[str, ...]


WORK_RULES: tuple[tuple[tuple[str, ...], str, int], ...] = (
    (("требован", "requirements", "business requirement"), "требования и постановка задач", 10),
    (("rest", "api", "swagger", "openapi", "http"), "REST API и интеграции", 12),
    (("sql", "postgres", "database", "база данных"), "SQL и данные", 10),
    (("интеграц", "integration", "webhook"), "интеграции между системами", 12),
    (("bpmn", "uml", "sequence", "диаграм"), "моделирование процессов и систем", 9),
    (("backend", "python", "php"), "backend-контекст", 6),
    (("тест", "qa", "test case", "чек-лист"), "проверка сценариев и тест-кейсы", 4),
    (("автоматиз", "automation", "workflow"), "автоматизация процессов", 10),
    (("1с", "erp", "crm"), "корпоративные системы", 6),
)

NEGATIVE_INTEREST: tuple[tuple[str, int, str], ...] = (
    ("продаж", -28, "много продаж"),
    ("холодн", -28, "холодные контакты"),
    ("call-центр", -30, "call-center"),
    ("оператор", -15, "операторская работа"),
    ("senior", -20, "роль выше текущего уровня"),
    ("ведущий", -20, "роль выше текущего уровня"),
)


def screening_label(score: int) -> str:
    if score >= 90:
        return "очень высокий"
    if score >= 80:
        return "высокий"
    if score >= 70:
        return "выше среднего"
    if score >= 60:
        return "средний"
    return "низкий"


def interest_label(score: int) -> str:
    if score >= 85:
        return "очень похоже на твоё"
    if score >= 72:
        return "скорее должно зайти"
    if score >= 58:
        return "может зайти"
    if score >= 42:
        return "50/50"
    return "скорее не твоё"


def evaluate_career_fit(item: RankedVacancy, target_salary_rub: int = 70_000) -> CareerFit:
    v = item.vacancy
    text = v.searchable_text
    score = 45
    work: list[str] = []
    advantages: list[str] = []
    checks: list[str] = []

    title = v.title.lower()
    if ("систем" in title and "аналит" in title) or "system analyst" in title:
        score += 22
        advantages.append("целевая роль системного аналитика")
    if any(x in title for x in ("junior", "стаж", "младш", "intern")):
        score += 12
        advantages.append("уровень junior / intern")

    for needles, label, weight in WORK_RULES:
        if any(needle in text for needle in needles):
            work.append(label)
            score += weight

    if any(x in (v.schedule or "").lower() for x in ("удален", "remote", "дистан")) or "удален" in text:
        score += 10
        advantages.append("удалённый формат")

    salary_values = [x for x in (v.salary_from, v.salary_to) if isinstance(x, int)]
    if salary_values and (v.salary_currency in {None, "RUR", "RUB"}):
        salary_max = max(salary_values)
        if salary_max >= target_salary_rub:
            score += 8
            advantages.append(f"зарплата достигает цели {target_salary_rub // 1000}k+")
        elif salary_max < int(target_salary_rub * 0.8):
            score -= 10
            checks.append("зарплата ниже целевого уровня")

    if "нет опыта" in (v.experience or "").lower():
        score += 10
        advantages.append("коммерческий опыт не обязателен")

    for marker, penalty, label in NEGATIVE_INTEREST:
        if marker in text:
            score += penalty
            checks.append(label)

    if not work:
        checks.append("из описания плохо видно ежедневные задачи")

    score = max(0, min(100, score))
    return CareerFit(
        interest_score=score,
        interest_label=interest_label(score),
        screening_label=screening_label(item.score.total),
        work_with=tuple(dict.fromkeys(work))[:5],
        advantages=tuple(dict.fromkeys(advantages))[:5],
        checks=tuple(dict.fromkeys(checks))[:3],
    )
