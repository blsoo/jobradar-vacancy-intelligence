# JobRadar

[![JobRadar checks](https://github.com/blsoo/jobradar-vacancy-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/blsoo/jobradar-vacancy-intelligence/actions/workflows/ci.yml)

**Vacancy intelligence · HH API · explainable scoring · Telegram actions · SQLite**

JobRadar turns a noisy job feed into a short ranked queue of opportunities worth reviewing and applying to.

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

## Working MVP

The current code can:

- search vacancies through the public HeadHunter API;
- rank them for a Junior System Analyst / Integration profile;
- reward SQL, REST, HTTP, JSON, API, requirements, UML/BPMN, integrations and remote work;
- penalize clearly senior/lead roles;
- store vacancies and decisions in SQLite;
- deduplicate by `(source, external_id)`;
- prevent repeated delivery after a successful Telegram send;
- send ranked Telegram cards with inline actions;
- use HH `apply_alternate_url` when available so the button opens the response form directly;
- generate a short cover letter only from portfolio evidence actually matched to the vacancy;
- ask why a vacancy was skipped: salary, office/geography, seniority, stack or other;
- store structured decision events for a future feedback-aware ranker;
- keep application state fail-closed instead of claiming a response was sent when it was not.

## Telegram flow

```text
🔥 87/100 · Junior System Analyst
Company · Remote
Salary: 70 000–90 000 RUB

Matched: SQL, REST, JSON, API
Risks: no explicit blocker detected

[🔥 Prepare application] [📌 Save]
[❌ Skip]                [⚡ HH response form]
```

`🔥 Prepare application` stores `apply_requested`, prepares a truthful short cover letter and opens the vacancy-specific HH response form when HH provides one.

After the real response is sent, `✅ I applied` moves the local funnel to `applied`.

`❌ Skip` asks for a structured reason. Those reasons are already visible through `/stats` and are intended to improve ranking later.

## Why application submission is a separate boundary

Public vacancy discovery does not require applicant OAuth. Actual platform-side mutations are different: JobRadar must not silently claim that an application succeeded.

A future OAuth adapter is tracked in [issue #1](https://github.com/blsoo/jobradar-vacancy-intelligence/issues/1). It may move a vacancy to `applied` only after the official platform confirms success or the user explicitly confirms the real submission.

## Run

Python 3.12+, no third-party runtime dependencies.

```bash
cp .env.example .env
PYTHONPATH=src python -m jobradar.app once
```

Continuous collection + Telegram callbacks:

```bash
PYTHONPATH=src python -m jobradar.app run
```

Windows PowerShell can use the same `.env` file because JobRadar loads it itself.

Tests:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Runtime configuration

See [`.env.example`](.env.example).

Important runtime values:

- `TELEGRAM_BOT_TOKEN` — Telegram bot secret;
- `TELEGRAM_CHAT_ID` — only this chat may mutate JobRadar state;
- `JOBRADAR_SCORE_THRESHOLD` — minimum score for push delivery;
- `JOBRADAR_TARGET_SALARY_RUB` — salary preference signal;
- `HH_SEARCH_QUERIES` — semicolon-separated search phrases;
- `HH_AREA` — HH area ID;
- `HH_USER_AGENT` — required client identity header.

Secrets are never committed.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md).

Core invariants:

1. vacancy identity is stable by `(source, external_id)`;
2. Telegram delivery is marked only after a successful send;
3. repeated identical decisions are idempotent;
4. unknown Telegram chats cannot mutate state;
5. scoring remains explainable;
6. `applied` never means merely "an attempt was made";
7. external OAuth mutations fail closed.

## Roadmap

Real next steps are tracked as issues with acceptance criteria:

- [#1 HH applicant OAuth and safe application adapter](https://github.com/blsoo/jobradar-vacancy-intelligence/issues/1)
- [#2 PostgreSQL repository and migrations](https://github.com/blsoo/jobradar-vacancy-intelligence/issues/2)
- [#3 Multi-source adapters and feedback-aware ranking](https://github.com/blsoo/jobradar-vacancy-intelligence/issues/3)

## Portfolio value

This is not only an architecture exercise: the repository contains executable code for external REST integration, normalization, ranking, persistence, idempotent delivery, Telegram callbacks, structured feedback, application-state modelling, tests and CI.
