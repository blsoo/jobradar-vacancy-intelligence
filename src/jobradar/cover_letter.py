from __future__ import annotations

from .models import RankedVacancy


PORTFOLIO_MAP = {
    "SQL": "SQL/PostgreSQL Casebook",
    "REST": "DevWork / FlowBridge",
    "HTTP": "DevWork / FlowBridge",
    "JSON": "DevWork / FlowBridge",
    "API": "DevWork / FlowBridge",
    "Swagger/OpenAPI": "DevWork / FlowBridge",
    "requirements": "DevWork",
    "UML": "DevWork / BullADM",
    "BPMN": "DevWork / FlowBridge",
    "integrations": "FlowBridge / BullSignal",
    "PostgreSQL": "DevWork / SQL Casebook",
    "Git": "public GitHub portfolio",
}


def build_cover_letter(item: RankedVacancy) -> str:
    vacancy = item.vacancy
    evidence: list[str] = []
    seen: set[str] = set()
    for skill in item.score.matched:
        project = PORTFOLIO_MAP.get(skill)
        if project and project not in seen:
            evidence.append(f"{skill} — {project}")
            seen.add(project)
        if len(evidence) >= 3:
            break

    evidence_text = "; ".join(evidence)
    if evidence_text:
        evidence_sentence = f"В публичном GitHub-портфолио могу показать практические кейсы: {evidence_text}."
    else:
        evidence_sentence = "В публичном GitHub-портфолио могу показать требования, модели данных, API-контракты и тестовые сценарии."

    return (
        f"Здравствуйте! Заинтересовала вакансия «{vacancy.title}». "
        "Развиваюсь в системном анализе, API-интеграциях и работе с данными. "
        f"{evidence_sentence} "
        "Ищу junior/стажёрскую позицию, где смогу быстро включиться в реальные задачи и расти внутри команды. "
        "GitHub: https://github.com/blsoo"
    )
