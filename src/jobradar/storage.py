from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import sqlite3
from typing import Iterable

from .models import RankedVacancy, ScoreResult, Vacancy


SCHEMA = """
CREATE TABLE IF NOT EXISTS vacancies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    score INTEGER NOT NULL,
    matched_json TEXT NOT NULL,
    risks_json TEXT NOT NULL,
    sent_at TEXT,
    decision TEXT,
    decision_reason TEXT,
    decision_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS decision_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vacancy_id INTEGER NOT NULL REFERENCES vacancies(id),
    decision TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS runtime_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vacancy_id INTEGER REFERENCES vacancies(id),
    source TEXT NOT NULL DEFAULT 'hh',
    external_vacancy_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'applied',
    applied_at TEXT,
    last_employer_event_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, external_vacancy_id)
);

CREATE TABLE IF NOT EXISTS employer_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    chat_id TEXT,
    source_event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    sender_name TEXT,
    text TEXT NOT NULL,
    event_at TEXT,
    notified_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_event_id)
);

CREATE TABLE IF NOT EXISTS interviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id INTEGER NOT NULL REFERENCES applications(id),
    source_event_id TEXT,
    scheduled_at TEXT NOT NULL,
    timezone TEXT NOT NULL,
    confidence TEXT NOT NULL,
    evidence TEXT,
    status TEXT NOT NULL DEFAULT 'scheduled',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(application_id, scheduled_at)
);

CREATE TABLE IF NOT EXISTS interview_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    interview_id INTEGER NOT NULL REFERENCES interviews(id),
    kind TEXT NOT NULL,
    remind_at TEXT NOT NULL,
    sent_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(interview_id, kind)
);

CREATE INDEX IF NOT EXISTS idx_vacancies_queue
ON vacancies(decision, sent_at, score DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_employer_events_notification
ON employer_events(notified_at, event_at);
CREATE INDEX IF NOT EXISTS idx_interview_reminders_due
ON interview_reminders(sent_at, remind_at);
"""


class VacancyStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._ensure_column("vacancies", "decision_reason", "TEXT")
        self._ensure_column("decision_events", "reason", "TEXT")
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, sql_type: str) -> None:
        columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")

    def close(self) -> None:
        self.conn.close()

    def get_setting(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM runtime_settings WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO runtime_settings(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
            """,
            (key, value),
        )
        self.conn.commit()

    def settings_by_prefix(self, prefix: str) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT key, value FROM runtime_settings WHERE key LIKE ?",
            (f"{prefix}%",),
        ).fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def upsert(self, ranked: RankedVacancy) -> int:
        v = ranked.vacancy
        payload = json.dumps(asdict(v), ensure_ascii=False)
        matched = json.dumps(ranked.score.matched, ensure_ascii=False)
        risks = json.dumps(ranked.score.risks, ensure_ascii=False)
        self.conn.execute(
            """
            INSERT INTO vacancies (
                source, external_id, title, company, url, published_at,
                payload_json, score, matched_json, risks_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, external_id) DO UPDATE SET
                title=excluded.title,
                company=excluded.company,
                url=excluded.url,
                published_at=excluded.published_at,
                payload_json=excluded.payload_json,
                score=excluded.score,
                matched_json=excluded.matched_json,
                risks_json=excluded.risks_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                v.source,
                v.external_id,
                v.title,
                v.company,
                v.url,
                v.published_at,
                payload,
                ranked.score.total,
                matched,
                risks,
            ),
        )
        row = self.conn.execute(
            "SELECT id FROM vacancies WHERE source=? AND external_id=?",
            (v.source, v.external_id),
        ).fetchone()
        self.conn.commit()
        return int(row["id"])

    def upsert_many(self, ranked: Iterable[RankedVacancy]) -> list[int]:
        return [self.upsert(item) for item in ranked]

    def unsent(self, threshold: int, limit: int) -> list[RankedVacancy]:
        rows = self.conn.execute(
            """
            SELECT * FROM vacancies
            WHERE sent_at IS NULL
              AND decision IS NULL
              AND score >= ?
            ORDER BY score DESC, published_at DESC
            LIMIT ?
            """,
            (threshold, limit),
        ).fetchall()
        return [self._to_ranked(row) for row in rows]

    def get(self, local_id: int) -> RankedVacancy | None:
        row = self.conn.execute("SELECT * FROM vacancies WHERE id=?", (local_id,)).fetchone()
        return self._to_ranked(row) if row else None

    def get_by_external_id(self, source: str, external_id: str) -> RankedVacancy | None:
        row = self.conn.execute(
            "SELECT * FROM vacancies WHERE source=? AND external_id=?",
            (source, str(external_id)),
        ).fetchone()
        return self._to_ranked(row) if row else None

    def mark_sent(self, local_id: int) -> None:
        self.conn.execute(
            "UPDATE vacancies SET sent_at=COALESCE(sent_at, CURRENT_TIMESTAMP), updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (local_id,),
        )
        self.conn.commit()

    def decide(self, local_id: int, decision: str, reason: str | None = None) -> bool:
        allowed = {"saved", "skipped", "apply_requested", "applied"}
        if decision not in allowed:
            raise ValueError(f"unsupported decision: {decision}")
        row = self.conn.execute(
            "SELECT decision, decision_reason, external_id FROM vacancies WHERE id=?", (local_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"vacancy not found: {local_id}")
        if row["decision"] == decision and row["decision_reason"] == reason:
            return False
        self.conn.execute(
            """
            UPDATE vacancies
            SET decision=?, decision_reason=?, decision_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (decision, reason, local_id),
        )
        self.conn.execute(
            "INSERT INTO decision_events(vacancy_id, decision, reason) VALUES (?, ?, ?)",
            (local_id, decision, reason),
        )
        if decision == "applied":
            self.ensure_application(str(row["external_id"]), vacancy_id=local_id, status="applied")
        self.conn.commit()
        return True

    def ensure_application(
        self,
        external_vacancy_id: str,
        *,
        vacancy_id: int | None = None,
        status: str = "applied",
        applied_at: str | None = None,
    ) -> int:
        if vacancy_id is None:
            row = self.conn.execute(
                "SELECT id FROM vacancies WHERE source='hh' AND external_id=?",
                (str(external_vacancy_id),),
            ).fetchone()
            vacancy_id = int(row["id"]) if row else None
        self.conn.execute(
            """
            INSERT INTO applications(vacancy_id, source, external_vacancy_id, status, applied_at)
            VALUES (?, 'hh', ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            ON CONFLICT(source, external_vacancy_id) DO UPDATE SET
                vacancy_id=COALESCE(applications.vacancy_id, excluded.vacancy_id),
                status=excluded.status,
                updated_at=CURRENT_TIMESTAMP
            """,
            (vacancy_id, str(external_vacancy_id), status, applied_at),
        )
        row = self.conn.execute(
            "SELECT id FROM applications WHERE source='hh' AND external_vacancy_id=?",
            (str(external_vacancy_id),),
        ).fetchone()
        self.conn.commit()
        return int(row["id"])

    def record_employer_event(
        self,
        *,
        external_vacancy_id: str,
        source_event_id: str,
        event_type: str,
        text: str,
        event_at: str,
        chat_id: str = "",
        sender_name: str = "Работодатель",
    ) -> tuple[bool, int]:
        existing = self.conn.execute(
            "SELECT application_id FROM employer_events WHERE source_event_id=?",
            (str(source_event_id),),
        ).fetchone()
        if existing:
            return False, int(existing["application_id"])

        application_id = self.ensure_application(str(external_vacancy_id), status="in_progress")
        self.conn.execute(
            """
            INSERT INTO employer_events(
                application_id, chat_id, source_event_id, event_type,
                sender_name, text, event_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (application_id, chat_id, str(source_event_id), event_type, sender_name, text, event_at),
        )
        status = "invited" if event_type == "positive" else ("rejected" if event_type == "rejection" else "in_progress")
        self.conn.execute(
            """
            UPDATE applications
            SET status=?, last_employer_event_at=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (status, event_at or None, application_id),
        )
        self.conn.commit()
        return True, application_id

    def mark_employer_event_notified(self, source_event_id: str) -> None:
        self.conn.execute(
            "UPDATE employer_events SET notified_at=COALESCE(notified_at, CURRENT_TIMESTAMP) WHERE source_event_id=?",
            (str(source_event_id),),
        )
        self.conn.commit()

    def schedule_interview(
        self,
        *,
        application_id: int,
        scheduled_at: datetime,
        timezone: str,
        confidence: str,
        evidence: str,
        source_event_id: str,
        reminders: Iterable[tuple[str, datetime]],
    ) -> int:
        scheduled_iso = scheduled_at.isoformat()
        self.conn.execute(
            """
            INSERT INTO interviews(
                application_id, source_event_id, scheduled_at, timezone, confidence, evidence
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(application_id, scheduled_at) DO UPDATE SET
                source_event_id=excluded.source_event_id,
                confidence=excluded.confidence,
                evidence=excluded.evidence,
                updated_at=CURRENT_TIMESTAMP
            """,
            (application_id, source_event_id, scheduled_iso, timezone, confidence, evidence),
        )
        row = self.conn.execute(
            "SELECT id FROM interviews WHERE application_id=? AND scheduled_at=?",
            (application_id, scheduled_iso),
        ).fetchone()
        interview_id = int(row["id"])
        for kind, remind_at in reminders:
            self.conn.execute(
                """
                INSERT INTO interview_reminders(interview_id, kind, remind_at)
                VALUES (?, ?, ?)
                ON CONFLICT(interview_id, kind) DO UPDATE SET remind_at=excluded.remind_at
                """,
                (interview_id, kind, remind_at.isoformat()),
            )
        self.conn.commit()
        return interview_id

    def due_reminders(self, now: datetime) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT r.id AS reminder_id, r.kind, r.remind_at,
                   i.id AS interview_id, i.scheduled_at, i.timezone,
                   a.external_vacancy_id,
                   v.title, v.company, v.url
            FROM interview_reminders r
            JOIN interviews i ON i.id=r.interview_id
            JOIN applications a ON a.id=i.application_id
            LEFT JOIN vacancies v ON v.id=a.vacancy_id
            WHERE r.sent_at IS NULL
              AND i.status='scheduled'
              AND r.remind_at <= ?
            ORDER BY r.remind_at ASC
            """,
            (now.isoformat(),),
        ).fetchall()

    def mark_reminder_sent(self, reminder_id: int) -> None:
        self.conn.execute(
            "UPDATE interview_reminders SET sent_at=COALESCE(sent_at, CURRENT_TIMESTAMP) WHERE id=?",
            (int(reminder_id),),
        )
        self.conn.commit()

    def application_stats(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS total FROM applications GROUP BY status"
        ).fetchall()
        result = {str(row["status"]): int(row["total"]) for row in rows}
        result["interviews"] = int(
            self.conn.execute("SELECT COUNT(*) FROM interviews WHERE status='scheduled'").fetchone()[0]
        )
        return result

    def decision_event_count(self, local_id: int) -> int:
        return int(
            self.conn.execute(
                "SELECT COUNT(*) FROM decision_events WHERE vacancy_id=?", (local_id,)
            ).fetchone()[0]
        )

    def skip_reason_stats(self) -> dict[str, int]:
        rows = self.conn.execute(
            """
            SELECT COALESCE(decision_reason, 'other') AS reason, COUNT(*) AS total
            FROM vacancies
            WHERE decision='skipped'
            GROUP BY COALESCE(decision_reason, 'other')
            ORDER BY total DESC, reason ASC
            """
        ).fetchall()
        return {str(row["reason"]): int(row["total"]) for row in rows}

    def stats(self) -> dict[str, int]:
        total = int(self.conn.execute("SELECT COUNT(*) FROM vacancies").fetchone()[0])
        sent = int(self.conn.execute("SELECT COUNT(*) FROM vacancies WHERE sent_at IS NOT NULL").fetchone()[0])
        applied = int(self.conn.execute("SELECT COUNT(*) FROM vacancies WHERE decision IN ('apply_requested','applied')").fetchone()[0])
        saved = int(self.conn.execute("SELECT COUNT(*) FROM vacancies WHERE decision='saved'").fetchone()[0])
        skipped = int(self.conn.execute("SELECT COUNT(*) FROM vacancies WHERE decision='skipped'").fetchone()[0])
        return {"total": total, "sent": sent, "apply_requested": applied, "saved": saved, "skipped": skipped}

    @staticmethod
    def _to_ranked(row: sqlite3.Row) -> RankedVacancy:
        payload = json.loads(row["payload_json"])
        payload["professional_roles"] = tuple(payload.get("professional_roles") or ())
        vacancy = Vacancy(**payload)
        score = ScoreResult(
            total=int(row["score"]),
            matched=tuple(json.loads(row["matched_json"])),
            risks=tuple(json.loads(row["risks_json"])),
            reasons=(),
        )
        return RankedVacancy(vacancy=vacancy, score=score, local_id=int(row["id"]))
