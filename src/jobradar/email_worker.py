from __future__ import annotations

from datetime import datetime
import hashlib
import re
import sqlite3

from .config import Settings
from .email_inbox import IMAPInboxClient, EmailEmployerMessage, is_hh_recruiting_message
from .hh_client import HHClient
from .hh_inbox import classify_employer_message
from .interviews import detect_interview_datetime, reminder_times
from .models import RankedVacancy
from .scoring import score_vacancy
from .storage import VacancyStore
from .telegram import TelegramClient


def _telegram(settings: Settings, store: VacancyStore) -> TelegramClient:
    chat_id = settings.telegram_chat_id or store.get_setting("telegram_chat_id") or ""
    return TelegramClient(settings.telegram_bot_token, chat_id)


def _event_id(message: EmailEmployerMessage) -> str:
    raw = message.message_id or f"uid:{message.uid}"
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:32]
    return f"email:{digest}"


def _external_vacancy_id(message: EmailEmployerMessage) -> str:
    if message.vacancy_id:
        return message.vacancy_id
    digest = hashlib.sha256(
        f"{message.sender}|{message.subject}".encode("utf-8", errors="ignore")
    ).hexdigest()[:20]
    return f"email-{digest}"


def _record_event(
    store: VacancyStore,
    *,
    message: EmailEmployerMessage,
    event_type: str,
) -> tuple[bool, int]:
    source_event_id = _event_id(message)
    existing = store.conn.execute(
        "SELECT application_id FROM employer_events WHERE source_event_id=?",
        (source_event_id,),
    ).fetchone()
    if existing:
        return False, int(existing["application_id"])

    external_id = _external_vacancy_id(message)
    vacancy = store.get_by_external_id("hh", external_id) if message.vacancy_id else None
    application_id = store.ensure_application(
        external_id,
        vacancy_id=int(vacancy.local_id) if vacancy and vacancy.local_id is not None else None,
        status="in_progress",
    )
    store.conn.execute(
        """
        INSERT INTO employer_events(
            application_id, chat_id, source_event_id, event_type,
            sender_name, text, event_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            application_id,
            "email",
            source_event_id,
            event_type,
            message.sender or "Работодатель",
            message.combined_text,
            message.received_at,
        ),
    )
    if event_type == "positive":
        store.conn.execute(
            "UPDATE applications SET status='invited', last_employer_event_at=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (message.received_at, application_id),
        )
    elif event_type == "rejection":
        store.conn.execute(
            "UPDATE applications SET status='rejected', last_employer_event_at=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (message.received_at, application_id),
        )
    else:
        store.conn.execute(
            "UPDATE applications SET last_employer_event_at=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (message.received_at, application_id),
        )
    store.conn.commit()
    return True, application_id


def _refresh_vacancy(settings: Settings, store: VacancyStore, vacancy_id: str) -> RankedVacancy | None:
    if not vacancy_id:
        return None
    try:
        vacancy = HHClient(settings.hh_user_agent).get_vacancy(vacancy_id)
        score = score_vacancy(
            vacancy,
            target_salary_rub=settings.target_salary_rub,
            remote_preferred=settings.remote_preferred,
        )
        local_id = store.upsert(RankedVacancy(vacancy=vacancy, score=score))
        return store.get(local_id)
    except Exception:
        return store.get_by_external_id("hh", vacancy_id)


def _rejection_display_text(text: str) -> str:
    normalized = (text or "").lower().replace("ё", "е")
    normalized = re.sub(r"\s+", " ", normalized)
    if re.search(r"\bне\s+(?:готов(?:а|ы)?\s+)?приглас", normalized):
        return "Работодатель не готов пригласить вас."
    if "другого кандидата" in normalized:
        return "Работодатель выбрал другого кандидата."
    if any(marker in normalized for marker in ("не готовы продолжить", "не готов продолжить", "не сможем продолжить", "не можем продолжить")):
        return "Работодатель не готов продолжить процесс."
    return "Работодатель отказал по отклику."


def _repair_legacy_rejection_events(store: VacancyStore) -> int:
    """Repair older events that were marked positive because 'не готов пригласить'
    contained the positive substring 'пригласить'. Only rows that the current
    classifier now deterministically calls a rejection are changed.
    """
    rows = store.conn.execute(
        "SELECT id, application_id, text FROM employer_events WHERE event_type='positive'"
    ).fetchall()
    repaired = 0
    for row in rows:
        if classify_employer_message(str(row["text"] or "")) != "rejection":
            continue
        event_id = int(row["id"])
        application_id = int(row["application_id"])
        store.conn.execute("UPDATE employer_events SET event_type='rejection' WHERE id=?", (event_id,))
        latest = store.conn.execute(
            """
            SELECT id FROM employer_events
            WHERE application_id=?
            ORDER BY COALESCE(event_at, created_at) DESC, id DESC
            LIMIT 1
            """,
            (application_id,),
        ).fetchone()
        if latest and int(latest["id"]) == event_id:
            store.conn.execute(
                "UPDATE applications SET status='rejected', updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (application_id,),
            )
        repaired += 1
    if repaired:
        store.conn.commit()
    return repaired


def process_message(
    settings: Settings,
    store: VacancyStore,
    telegram: TelegramClient,
    message: EmailEmployerMessage,
) -> bool:
    if not is_hh_recruiting_message(message):
        return False

    event_type = classify_employer_message(message.combined_text)
    created, application_id = _record_event(store, message=message, event_type=event_type)
    if not created:
        return False

    item = _refresh_vacancy(settings, store, message.vacancy_id)
    interview_at = None
    if event_type == "positive":
        detection = detect_interview_datetime(
            message.combined_text,
            message.received_at,
            settings.timezone,
        )
        if detection is not None:
            interview_at = detection.scheduled_at
            store.schedule_interview(
                application_id=application_id,
                scheduled_at=detection.scheduled_at,
                timezone=settings.timezone,
                confidence=detection.confidence,
                evidence=detection.evidence,
                source_event_id=_event_id(message),
                reminders=reminder_times(detection.scheduled_at),
            )
        telegram.send_positive_response(
            item,
            sender_name=message.sender or "Работодатель",
            message_text=message.combined_text,
            interview_at=interview_at,
            target_salary_rub=settings.target_salary_rub,
        )
    elif event_type == "rejection":
        telegram.send_rejection(
            item,
            message.sender or "Работодатель",
            _rejection_display_text(message.combined_text),
        )
    else:
        telegram.send_employer_message(item, message.sender or "Работодатель", message.combined_text)

    store.mark_employer_event_notified(_event_id(message))
    return True


def run(settings: Settings | None = None) -> int:
    settings = settings or Settings.from_env()
    if not settings.email_monitor_enabled:
        print("JobRadar email: disabled")
        return 0

    store = VacancyStore(settings.db_path)
    telegram = _telegram(settings, store)
    client = IMAPInboxClient(
        settings.email_imap_host,
        settings.email_imap_username,
        settings.email_imap_password,
        port=settings.email_imap_port,
        mailbox=settings.email_imap_mailbox,
    )
    try:
        repaired = _repair_legacy_rejection_events(store)
        if repaired:
            print(f"JobRadar email: repaired_rejections={repaired}")

        raw_cursor = store.get_setting("email_last_uid")
        if raw_cursor is None:
            latest = client.latest_uid()
            store.set_setting("email_last_uid", str(latest))
            print(f"JobRadar email: bootstrap_uid={latest} processed=0")
            return 0

        try:
            last_uid = max(0, int(raw_cursor))
        except ValueError:
            last_uid = 0

        messages = client.messages_after(last_uid, limit=50)
        processed = 0
        max_uid = last_uid
        for message in messages:
            max_uid = max(max_uid, message.uid)
            try:
                processed += int(process_message(settings, store, telegram, message))
            finally:
                store.set_setting("email_last_uid", str(max_uid))
        print(f"JobRadar email: fetched={len(messages)} processed={processed} cursor={max_uid}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(run())
