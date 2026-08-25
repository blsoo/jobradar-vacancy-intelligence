from __future__ import annotations

from dataclasses import asdict
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

CREATE INDEX IF NOT EXISTS idx_vacancies_queue
ON vacancies(decision, sent_at, score DESC, created_at DESC);
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
            "SELECT decision, decision_reason FROM vacancies WHERE id=?", (local_id,)
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
        self.conn.commit()
        return True

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
