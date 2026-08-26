# JobRadar

[![JobRadar checks](https://github.com/blsoo/jobradar-vacancy-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/blsoo/jobradar-vacancy-intelligence/actions/workflows/ci.yml)

**Vacancy intelligence · HH discovery · explainable scoring · Telegram actions · SQLite**

JobRadar turns a noisy vacancy feed into a small ranked queue of opportunities worth reviewing.

```text
HH discovery
    -> normalize
    -> deterministic score
    -> deduplicate
    -> SQLite state
    -> quiet top-3 Telegram digest
    -> apply / save / skip
    -> application funnel
```

## Working MVP

The current code can:

- discover HeadHunter vacancies through the API when authorized and use HH public RSS as the anonymous fallback;
- rank them for a Junior System Analyst / Integration profile;
- reward SQL, REST, HTTP, JSON, API, requirements, UML/BPMN, integrations, junior/no-experience signals, salary target and remote work;
- penalize clearly senior/lead roles;
- deduplicate by `(source, external_id)` and store state in SQLite;
- bind one owner Telegram chat on the first `/start`;
- send only vacancies above the configured quality threshold;
- combine up to three best new vacancies into one Telegram digest;
- rate-limit unsolicited vacancy digests to at most one per 30 minutes;
- show salary, format, a profile-match score and a human-readable screening-chance band;
- expose exactly three first-level actions per vacancy: `Apply`, `Save`, `Skip`;
- generate a short cover letter only from portfolio evidence actually matched to the vacancy;
- keep repeated callbacks idempotent;
- keep application state fail-closed instead of pretending HH accepted a response when it did not.

## Telegram flow

```text
🎯 JobRadar · лучшие новые вакансии

1️⃣ Junior System Analyst
Company · Москва
💰 70 000–90 000 ₽ · 🏠 Удалённо
📈 Шанс первичного скрининга: высокий · 86/100
✅ SQL · REST · требования · без опыта

2️⃣ ...
3️⃣ ...

[1 🔥 Отклик] [1 📌 Сохранить] [1 ❌ Мимо]
[2 🔥 Отклик] [2 📌 Сохранить] [2 ❌ Мимо]
[3 🔥 Отклик] [3 📌 Сохранить] [3 ❌ Мимо]
```

The screening label is an **explainable heuristic**, not an empirical probability of receiving an offer. It reflects how strongly the vacancy matches the configured junior profile and known portfolio evidence.

`🔥 Отклик` stores `apply_requested`, prepares a truthful cover letter and provides the HH application page. Until an official applicant OAuth adapter confirms a platform-side response, JobRadar does not claim that an application was sent.

`📌 Сохранить` and `❌ Мимо` are one-tap actions and do not create extra chat messages.

## Anti-spam behaviour

Search and Telegram control are intentionally separated:

- vacancy discovery may run every few minutes;
- low-score vacancies stay silent;
- old backlog is not drained into Telegram;
- at most three opportunities are shown in one digest;
- unsolicited digests have a 30-minute cooldown;
- button callbacks and `/stats` can still be processed every minute.

## First-run Telegram binding

`TELEGRAM_CHAT_ID` is optional. If empty, the first `/start` claims the bot and persists the owner chat ID. Other chats cannot mutate JobRadar state after binding.

## Run

Python 3.12+, no third-party runtime dependencies.

```bash
cp .env.example .env
PYTHONPATH=src python -m jobradar.app once
```

Continuous worker:

```bash
PYTHONPATH=src python -m jobradar.app run
```

Cron-friendly short-lived worker:

```bash
PYTHONPATH=src python -m jobradar.app cron
```

Tests:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## Runtime configuration

See [`.env.example`](.env.example).

Important values:

- `TELEGRAM_BOT_TOKEN` — Telegram secret, never committed;
- `TELEGRAM_CHAT_ID` — optional fixed owner chat;
- `JOBRADAR_SCORE_THRESHOLD` — minimum score for Telegram delivery, default `70`;
- `JOBRADAR_MAX_PUSH_PER_CYCLE` — hard cap per digest, default `3`;
- `JOBRADAR_TARGET_SALARY_RUB` — salary preference signal;
- `HH_SEARCH_QUERIES` — semicolon-separated search phrases;
- `HH_AREA` — HH area ID.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md).

Core invariants:

1. vacancy identity is stable by `(source, external_id)`;
2. delivery is marked only after a successful Telegram send;
3. repeated identical decisions are idempotent;
4. owner chat binding is persistent;
5. scoring remains explainable;
6. `applied` never means merely "an attempt was made";
7. external OAuth mutations fail closed;
8. discovery frequency must not imply notification frequency.

## Roadmap

- [#1 HH applicant OAuth and safe application adapter](https://github.com/blsoo/jobradar-vacancy-intelligence/issues/1)
- [#2 PostgreSQL repository and migrations](https://github.com/blsoo/jobradar-vacancy-intelligence/issues/2)
- [#3 Multi-source adapters and feedback-aware ranking](https://github.com/blsoo/jobradar-vacancy-intelligence/issues/3)

## Portfolio value

The repository demonstrates external integration, normalization, deterministic ranking, persistence, deduplication, idempotent Telegram callbacks, rate-limited notifications, application-state modelling, tests and CI without presenting a design prototype as production automation.
