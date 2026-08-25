# JobRadar

**Vacancy intelligence · HH API · deterministic scoring · Telegram actions · SQLite**

JobRadar is a vacancy-monitoring assistant that turns a noisy job feed into a short ranked queue of opportunities worth reviewing.

```text
HH vacancy search
    -> normalize
    -> deterministic score
    -> deduplicate
    -> SQLite state
    -> Telegram card
    -> save / skip / prepare application
    -> feedback history
```

## MVP

The current MVP can:

- search vacancies through the public HeadHunter API;
- rank them for a Junior System Analyst / Integration profile;
- reward SQL, REST, HTTP, JSON, API, requirements, UML/BPMN and remote work;
- penalize clearly senior/lead roles;
- store vacancies and decisions in SQLite;
- prevent duplicate Telegram notifications;
- send ranked vacancy cards with inline buttons;
- record `saved`, `skipped` and `apply_requested` decisions;
- keep the application boundary explicit instead of pretending an external application succeeded.

## Telegram card

```text
🔥 87/100 · Junior System Analyst
Company · Remote
Salary: 70 000–90 000 RUB

Matched: SQL, REST, JSON, API
Risks: BPMN not mentioned

[🔥 Prepare application] [📌 Save]
[❌ Skip]                [🔗 Open HH]
```

`Prepare application` records intent and opens the vacancy flow. A future OAuth adapter may submit an application only when the official platform exposes an allowed applicant action for that vacancy.

## Run

Python 3.12+, no third-party runtime dependencies.

```bash
cp .env.example .env
set -a && . ./.env && set +a
PYTHONPATH=src python -m jobradar.app once
```

Continuous mode:

```bash
PYTHONPATH=src python -m jobradar.app run
```

Tests:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Environment

See [`.env.example`](.env.example). Secrets are never committed.

## Design boundaries

- Vacancy discovery works without HH OAuth.
- Telegram bot token and chat ID are runtime secrets.
- Application submission is a separate adapter and must fail closed when OAuth/action metadata is unavailable.
- Scoring is deterministic and explainable; an LLM is not required to decide what gets sent.

## Portfolio value

The repository demonstrates a useful end-to-end analyst/backend case: external REST API, normalization, ranking rules, idempotent delivery, persistence, Telegram callbacks, audit decisions, failure boundaries and CI.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the system design.
