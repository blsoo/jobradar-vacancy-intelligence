from __future__ import annotations

from dataclasses import dataclass

from .models import RankedVacancy


@dataclass(frozen=True)
class InterviewPrep:
    priorities: tuple[str, ...]
    likely_questions: tuple[str, ...]


PREP_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("sql", "postgres", "database", "база данных"),
     "SQL: SELECT, WHERE, ORDER BY, JOIN/LEFT JOIN, NULL, COUNT/GROUP BY",
     "объяснить PK/FK, INNER vs LEFT JOIN и написать простой SELECT/JOIN"),
    (("rest", "api", "http", "swagger", "openapi"),
     "HTTP/REST: методы, status codes, JSON, request/response, Swagger/OpenAPI",
     "чем GET отличается от POST, что означают 200/201/400/404/409/500 и как устроен REST API"),
    (("требован", "requirements", "user story", "use case", "сценари"),
     "Системный анализ: требования, use cases, acceptance criteria, edge cases",
     "как собрать/уточнить требование и превратить его в понятный сценарий для разработки"),
    (("bpmn", "uml", "sequence", "диаграм"),
     "UML/BPMN: sequence/activity/process basics и чтение диаграмм",
     "нарисовать простой сценарий взаимодействия пользователь → API → БД"),
    (("kafka", "queue", "очеред"),
     "Очереди/Kafka: producer, consumer, topic, delivery и зачем нужна асинхронность",
     "когда очередь лучше синхронного REST-вызова и что делать с повторной доставкой"),
    (("test", "тест", "qa", "чек-лист", "test case"),
     "Тестирование: test case/checklist, позитивные/негативные сценарии, boundary cases",
     "как проверить API и какие негативные кейсы добавить к happy path"),
    (("git", "github"),
     "Git: commit, branch, pull request, merge/conflict basics",
     "как обычно работаешь с веткой и pull request"),
    (("python",),
     "Python basics: структуры данных, функции, условия/циклы и чтение простого кода",
     "прочитать небольшой Python-фрагмент и объяснить его поведение"),
    (("java", "junit", "selenium"),
     "Java basics: if/loops, classes/OOP; если есть QA-блок — JUnit/TestNG basics",
     "объяснить класс/объект и прочитать небольшой Java-фрагмент"),
    (("xml",),
     "XML: структура документа и отличие от JSON",
     "прочитать простой XML и сопоставить его с JSON"),
)


def build_interview_prep(item: RankedVacancy) -> InterviewPrep:
    text = item.vacancy.searchable_text
    priorities: list[str] = []
    questions: list[str] = []

    for needles, prep, question in PREP_RULES:
        if any(needle in text for needle in needles):
            priorities.append(prep)
            questions.append(question)

    title = item.vacancy.title.lower()
    analyst_role = ("аналит" in title) or ("analyst" in title)
    if analyst_role:
        defaults = (
            "SQL: SELECT/WHERE/JOIN/NULL/GROUP BY на простых таблицах",
            "HTTP/REST + JSON: методы, коды ответа, API request/response",
            "Требования и пользовательские сценарии: уточнение, декомпозиция, edge cases",
        )
        default_questions = (
            "написать простой SQL-запрос с JOIN и объяснить результат",
            "разобрать REST-запрос и выбрать подходящий HTTP status code",
            "уточнить расплывчатое требование и описать основной + негативный сценарий",
        )
        for prep in defaults:
            if prep not in priorities:
                priorities.append(prep)
        for question in default_questions:
            if question not in questions:
                questions.append(question)

    return InterviewPrep(
        priorities=tuple(dict.fromkeys(priorities))[:6],
        likely_questions=tuple(dict.fromkeys(questions))[:5],
    )
