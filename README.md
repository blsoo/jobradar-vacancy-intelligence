# JobRadar

[![JobRadar checks](https://github.com/blsoo/jobradar-vacancy-intelligence/actions/workflows/ci.yml/badge.svg)](https://github.com/blsoo/jobradar-vacancy-intelligence/actions/workflows/ci.yml)

**Vacancy intelligence · HH discovery · applicant inbox tracking · career fit · interview reminders · Telegram**

JobRadar turns a noisy vacancy feed into a small ranked queue and then keeps the application funnel in one place.

```text
HH discovery
    -> explainable ranking
    -> quiet top-3 Telegram digest
    -> apply / save / skip
    -> application funnel
    -> HH employer chat monitor (OAuth)
    -> positive response / rejection
    -> interview date detection
    -> reminders: 24h / 2h / 30m
```

## Working behaviour

The current code can:

- discover HeadHunter vacancies through the API when authorized and use HH public RSS as the anonymous fallback;
- rank them for a Junior System Analyst / Integration profile;
- deduplicate vacancies and keep state in SQLite;
- send one quiet digest with at most three high-score vacancies, no more than once per 30 minutes;
- expose exactly three first-level actions per vacancy: `Apply`, `Save`, `Skip`;
- show salary, screening-fit band, personal interest-fit score and a short explanation of what the job actually involves;
- build cover letters only from evidence already present in the portfolio profile;
- keep applications, employer events, invitations, rejections, interviews and reminders as persistent entities;
- use the current HH chat API after applicant OAuth is configured;
- activate applicant OAuth through `/hh_auth` without storing the user's HH login/password;
- persist access/refresh tokens only in the protected runtime DB and refresh them after access-token expiry;
- detect new employer messages once and avoid duplicate notifications;
- classify clear invitations/interview messages and clear rejections;
- extract explicit interview date/time from Russian messages (`29.08 15:30`, `29 августа в 15:30`, `завтра в 12:00`);
- schedule Telegram reminders for 24 hours, 2 hours and 30 minutes before a detected interview;
- show a fresh career-fit summary again when an invitation arrives: salary, likely day-to-day work, fit for the target direction and screening match;
- expose the accumulated funnel through `/stats`.

## Vacancy Telegram flow

```text
🎯 JobRadar · лучшие новые вакансии

1️⃣ Junior System Analyst
Company · Москва
💰 70 000–90 000 ₽
🎯 Скрининг: высокий · 86/100
❤️ Тебе должно зайти: скорее должно зайти · 82/100
🛠 требования · REST API и интеграции · SQL и данные
✅ целевая роль · junior · удалённый формат

[1 🔥 Отклик] [1 📌 Сохранить] [1 ❌ Мимо]
[2 🔥 Отклик] [2 📌 Сохранить] [2 ❌ Мимо]
[3 🔥 Отклик] [3 📌 Сохранить] [3 ❌ Мимо]
```

The scores are explainable heuristics, not fabricated probabilities of an offer. They are intended to answer two separate questions: **how well the vacancy matches the current profile** and **how likely the day-to-day work is to fit the chosen career direction**.

## Positive response flow

With applicant OAuth enabled, JobRadar checks HH employer chats every minute. A clear interview/invitation message becomes a persistent employer event and produces a Telegram notification such as:

```text
🎉 ПОЛОЖИТЕЛЬНЫЙ ОТВЕТ
Junior System Analyst · Company
💰 80 000 ₽
🎯 Скрининг: высокий · 84/100
❤️ Насколько тебе подходит: скорее должно зайти · 81/100
🛠 С чем работать: требования · API · SQL · интеграции
📅 Собеседование: 29.08.2026 15:30 MSK
⏰ Напоминания: за 24 часа, 2 часа и 30 минут
```

If the employer message is positive but contains no reliable date/time, the response is still stored and notified; JobRadar does **not** invent a meeting time.

## One-time HH authorization

The runtime never asks for or stores the applicant's HeadHunter password.

After an HH API application is configured with `HH_CLIENT_ID` and `HH_CLIENT_SECRET`:

1. send `/hh_status` to check the integration state;
2. send `/hh_auth` and open the generated official HH authorization link;
3. approve access on hh.ru;
4. copy the complete redirect URL and send `/hhcode <redirect URL>` back to the owner-only bot;
5. JobRadar validates OAuth `state`, exchanges the one-time code, verifies that the token belongs to an applicant, then stores the token pair only in the private runtime database;
6. the Telegram message containing the one-time authorization code is deleted on a best-effort basis.

The refresh token is rotated only after the current access token expires, following HH's token lifecycle rules.

## Persistence model

SQLite keeps separate records for:

- vacancies and Telegram decisions;
- applications;
- employer messages/events;
- scheduled interviews;
- reminder deliveries;
- runtime cursors for Telegram and HH chats;
- the private OAuth token pair after the user completes authorization.

Repeated polling or worker restarts therefore do not create duplicate invitations or duplicate reminders.

## Anti-spam behaviour

Search and Telegram control are intentionally separated:

- vacancy discovery may run every few minutes;
- low-score vacancies stay silent;
- old backlog is not drained into Telegram;
- at most three opportunities are shown in one digest;
- unsolicited vacancy digests have a 30-minute cooldown;
- employer responses and interview reminders are event notifications and bypass the vacancy-digest cooldown;
- button callbacks and `/stats` can still be processed every minute.

## HH OAuth boundary

Vacancy discovery can work without applicant OAuth. Reading personal HH chats, invitations and responses cannot: HeadHunter requires OAuth2 for those endpoints.

Only the HH application's client credentials are injected into the private runtime environment. User access/refresh tokens obtained by `/hh_auth` stay in the protected VPS database and are never committed to the public repository.

The project uses the current `/common/chats` API for employer messages. Platform-side automatic application submission remains a separate mutation boundary and must only be marked successful after HH confirms it.

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
- `JOBRADAR_SCORE_THRESHOLD` — minimum vacancy score for Telegram delivery, default `70`;
- `JOBRADAR_MAX_PUSH_PER_CYCLE` — hard cap per digest, default `3`;
- `JOBRADAR_TARGET_SALARY_RUB` — salary preference signal;
- `JOBRADAR_TIMEZONE` — timezone used for interview parsing and reminders;
- `JOBRADAR_INBOX_POLL_SECONDS` — employer inbox check cadence;
- `HH_CLIENT_ID` / `HH_CLIENT_SECRET` — HH API application credentials kept only in runtime secrets;
- `HH_REDIRECT_URI` — redirect URI registered for the HH API application;
- `HH_OAUTH_TOKEN` — optional bootstrap token; normal user authorization uses `/hh_auth`;
- `HH_SEARCH_QUERIES` and `HH_AREA` — discovery scope.

## Core invariants

1. vacancy identity is stable by `(source, external_id)`;
2. delivery is marked only after a successful Telegram send;
3. repeated identical decisions and employer events are idempotent;
4. owner chat binding is persistent;
5. scoring remains explainable;
6. `applied` never means merely "an attempt was made";
7. OAuth-only features fail closed when OAuth is absent;
8. discovery frequency does not imply notification frequency;
9. interview times are stored only when a date/time can be extracted from employer evidence;
10. reminders are marked sent only after Telegram accepts the notification;
11. HH login/password is never collected by JobRadar;
12. OAuth authorization code/state and refresh rotation are handled as security boundaries.

## Roadmap

- [#1 Complete HH applicant OAuth and safe application adapter](https://github.com/blsoo/jobradar-vacancy-intelligence/issues/1)
- [#2 PostgreSQL repository and migrations](https://github.com/blsoo/jobradar-vacancy-intelligence/issues/2)
- [#3 Multi-source adapters and feedback-aware ranking](https://github.com/blsoo/jobradar-vacancy-intelligence/issues/3)

## Portfolio value

The repository now demonstrates vacancy discovery, explainable ranking, career-fit reasoning, persistent application-state modelling, OAuth lifecycle design, personal inbox integration, event deduplication, date extraction, reminder scheduling, Telegram UX, tests and CI without pretending that an OAuth-disabled feature is already active.
