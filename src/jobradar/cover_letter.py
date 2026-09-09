from __future__ import annotations

from .models import RankedVacancy


FOCUS_LABELS = {
    "system analysis": "системным анализом",
    "requirements": "сбором и формализацией требований",
    "integrations": "интеграциями между сервисами",
    "REST": "REST API",
    "API": "API-контрактами",
    "HTTP": "HTTP",
    "JSON": "JSON",
    "Swagger/OpenAPI": "OpenAPI/Swagger",
    "SQL": "SQL и работой с данными",
    "PostgreSQL": "PostgreSQL",
}

META_LABELS = {
    "internship",
    "junior",
    "core junior analyst match",
    "remote",
    "no experience required",
}


def _focus_text(item: RankedVacancy) -> str:
    focus: list[str] = []
    for label in item.score.matched:
        text = FOCUS_LABELS.get(label)
        if text and text not in focus:
            focus.append(text)
        if len(focus) >= 3:
            break
    if not focus:
        return "системным анализом, данными и интеграциями"
    if len(focus) == 1:
        return focus[0]
    return ", ".join(focus[:-1]) + " и " + focus[-1]


def _project_evidence(item: RankedVacancy) -> list[str]:
    matched = set(item.score.matched)
    evidence: list[str] = []

    if matched & {"system analysis", "requirements"}:
        evidence.append(
            "В BullSignal декомпозировал многокомпонентную систему, описывал требования, состояния, API-контракты и обработку отказов."
        )

    if matched & {"SQL", "PostgreSQL"}:
        evidence.append(
            "В DevWork прорабатывал SQL, REST/OpenAPI и PostgreSQL-ориентированную модель данных для проектов, этапов и задач."
        )

    if matched & {"integrations", "REST", "API", "HTTP", "JSON", "Swagger/OpenAPI"}:
        evidence.append(
            "В FlowBridge проектировал интеграционные сценарии: REST/OpenAPI, валидацию, idempotency, retries/timeouts и обработку ошибок."
        )

    if not evidence:
        evidence.append(
            "В BullSignal и DevWork могу показать требования, системные сценарии, модели данных, API-контракты и тестовые сценарии."
        )

    return evidence[:2]


def _english_sentence(item: RankedVacancy) -> str:
    text = item.vacancy.searchable_text
    if "англий" in text or "english" in text:
        return " Английский C1 подтверждён тестом HH; свободно читаю техническую документацию и API/docs."
    return ""


def build_cover_letter(item: RankedVacancy) -> str:
    vacancy = item.vacancy
    company = vacancy.company.strip()
    company_part = f" в {company}" if company else ""
    focus = _focus_text(item)
    evidence = " ".join(_project_evidence(item))
    matched = set(item.score.matched)
    junior_flow = bool(matched & META_LABELS)

    closing = (
        "Ищу junior/стажёрскую позицию, где смогу быстро включиться в реальные задачи и расти внутри команды."
        if junior_flow
        else "Буду рад обсудить вакансию и подробнее рассказать о проектах и подходе к работе."
    )

    return (
        "Здравствуйте!\n\n"
        f"Заинтересовала вакансия «{vacancy.title}»{company_part} — особенно задачи, связанные с {focus}.\n\n"
        "В собственных IT-проектах прохожу цепочку от требований и сценариев до моделей данных, API/интеграций и проверки результата. "
        f"{evidence}"
        f"{_english_sentence(item)}\n\n"
        f"{closing}\n"
        "GitHub: https://github.com/blsoo"
    )
