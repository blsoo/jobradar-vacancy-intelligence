from __future__ import annotations

from datetime import datetime
import sys
import time
from zoneinfo import ZoneInfo

from .config import Settings
from .cover_letter import build_cover_letter
from .hh_client import HHClient
from .hh_inbox import HHInboxClient, classify_employer_message
from .interviews import detect_interview_datetime, reminder_times
from .models import RankedVacancy
from .scoring import score_vacancy
from .storage import VacancyStore
from .telegram import TelegramClient


DIGEST_COOLDOWN_SECONDS = 30 * 60
DIGEST_SIZE = 3

SKIP_REASONS = {
    "salary": "зарплата",
    "office": "офис / география",
    "seniority": "слишком высокий уровень",
    "stack": "не мой стек",
    "other": "другое",
}


def collect(settings: Settings, store: VacancyStore) -> int:
    client = HHClient(settings.hh_user_agent, settings.hh_oauth_token)
    vacancies = client.search_many(
        settings.hh_search_queries,
        area=settings.hh_area,
        per_page=settings.hh_per_page,
    )
    for vacancy in vacancies:
        score = score_vacancy(
            vacancy,
            target_salary_rub=settings.target_salary_rub,
            remote_preferred=settings.remote_preferred,
        )
        store.upsert(RankedVacancy(vacancy=vacancy, score=score))
    return len(vacancies)


def _digest_due(store: VacancyStore, now_epoch: int, *, force: bool = False) -> bool:
    if force:
        return True
    raw = store.get_setting("last_digest_epoch") or "0"
    try:
        last = max(0, int(raw))
    except ValueError:
        last = 0
    return last == 0 or now_epoch - last >= DIGEST_COOLDOWN_SECONDS


def push_new(
    settings: Settings,
    store: VacancyStore,
    telegram: TelegramClient,
    *,
    force: bool = False,
) -> int:
    if not telegram.can_send:
        return 0
    now_epoch = int(time.time())
    if not _digest_due(store, now_epoch, force=force):
        return 0

    limit = max(1, min(DIGEST_SIZE, settings.max_push_per_cycle))
    queue = store.unsent(settings.score_threshold, limit)
    if not queue:
        return 0

    telegram.send_digest(queue, target_salary_rub=settings.target_salary_rub)
    for item in queue:
        store.mark_sent(int(item.local_id))
    store.set_setting("last_digest_epoch", str(now_epoch))
    return len(queue)


def _chat_id_from_update(update: dict) -> str:
    callback = update.get("callback_query") or {}
    message = callback.get("message") or update.get("message") or {}
    chat = message.get("chat") or {}
    return str(chat.get("id", ""))


def _authorized_chat(telegram: TelegramClient, update: dict) -> bool:
    return bool(telegram.chat_id) and _chat_id_from_update(update) == str(telegram.chat_id)


def _stats_text(store: VacancyStore) -> str:
    stats = store.stats()
    pipeline = store.application_stats()
    return (
        "📊 JobRadar\n"
        f"Вакансий в базе: {stats['total']}\n"
        f"Показано: {stats['sent']}\n"
        f"Сохранено: {stats['saved']}\n"
        f"К отклику: {stats['apply_requested']}\n"
        f"Пропущено: {stats['skipped']}\n\n"
        "💼 Воронка\n"
        f"В работе: {pipeline.get('in_progress', 0)}\n"
        f"Приглашений: {pipeline.get('invited', 0)}\n"
        f"Отказов: {pipeline.get('rejected', 0)}\n"
        f"Запланировано собесов: {pipeline.get('interviews', 0)}"
    )


def handle_update(settings: Settings, store: VacancyStore, telegram: TelegramClient, update: dict) -> None:
    message = update.get("message") or {}
    text = (message.get("text") or "").strip().lower()
    incoming_chat_id = _chat_id_from_update(update)

    if not telegram.chat_id:
        if text == "/start" and incoming_chat_id:
            telegram.bind_chat(incoming_chat_id)
            store.set_setting("telegram_chat_id", incoming_chat_id)
            telegram.send_text(
                "✅ JobRadar привязан.\n"
                "Тихий режим: максимум один дайджест за 30 минут и до 3 лучших вакансий.\n"
                "После HH OAuth бот также следит за ответами работодателей и собеседованиями.\n"
                "/stats — воронка."
            )
        return

    if not _authorized_chat(telegram, update):
        callback = update.get("callback_query") or {}
        if callback.get("id"):
            telegram.answer_callback(callback["id"], "Нет доступа")
        return

    if text == "/start":
        oauth = "подключён" if settings.hh_oauth_token else "ещё не подключён"
        telegram.send_text(
            "✅ JobRadar работает в тихом режиме.\n"
            f"HH Inbox: {oauth}.\n"
            "/stats — вакансии, отклики, приглашения и собесы."
        )
        return

    if text == "/stats":
        telegram.send_text(_stats_text(store))
        return

    callback = update.get("callback_query") or {}
    data = callback.get("data") or ""
    callback_id = callback.get("id")
    parts = data.split(":") if data else []
    if len(parts) < 2 or not parts[1].isdigit():
        if callback_id and data:
            telegram.answer_callback(callback_id, "Некорректная команда")
        return

    action = parts[0]
    local_id = int(parts[1])
    extra = parts[2] if len(parts) > 2 else None
    item = store.get(local_id)
    if item is None:
        if callback_id:
            telegram.answer_callback(callback_id, "Вакансия уже недоступна в локальной базе")
        return

    if action == "save":
        store.decide(local_id, "saved")
        telegram.answer_callback(callback_id, "📌 Сохранено")
        return

    if action == "skip":
        store.decide(local_id, "skipped", reason="other")
        telegram.answer_callback(callback_id, "❌ Пропущено")
        return

    if action == "skipr" and extra in SKIP_REASONS:
        store.decide(local_id, "skipped", reason=extra)
        telegram.answer_callback(callback_id, f"❌ Учёл: {SKIP_REASONS[extra]}")
        return

    if action == "apply":
        store.decide(local_id, "apply_requested")
        telegram.answer_callback(callback_id, "🔥 Готовлю отклик")
        letter = build_cover_letter(item)
        telegram.send_text(
            "🔥 Подготовленный отклик\n\n"
            f"{letter}\n\n"
            "Открой форму HH, отправь и нажми «Я откликнулся». После HH OAuth JobRadar будет сам отслеживать ответ работодателя.",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "⚡ Открыть форму HH", "url": item.vacancy.application_url}],
                    [{"text": "✅ Я откликнулся", "callback_data": f"applied:{local_id}"}],
                ]
            },
        )
        return

    if action == "applied":
        store.decide(local_id, "applied")
        telegram.answer_callback(callback_id, "✅ Отклик записан и добавлен в воронку")
        return

    if callback_id:
        telegram.answer_callback(callback_id, "Неизвестное действие")


def poll_updates(
    settings: Settings,
    store: VacancyStore,
    telegram: TelegramClient,
    *,
    timeout: int = 1,
) -> int:
    if not telegram.enabled:
        return 0
    raw_offset = store.get_setting("telegram_update_offset") or "0"
    try:
        offset = max(0, int(raw_offset))
    except ValueError:
        offset = 0

    processed = 0
    updates = telegram.get_updates(offset=offset, timeout=timeout)
    for update in sorted(updates, key=lambda item: int(item.get("update_id", 0))):
        handle_update(settings, store, telegram, update)
        update_id = int(update.get("update_id", 0))
        store.set_setting("telegram_update_offset", str(update_id + 1))
        processed += 1
    return processed


def poll_hh_inbox(settings: Settings, store: VacancyStore, telegram: TelegramClient) -> int:
    """Notify once for new employer chat messages and schedule detected interviews."""
    if not settings.hh_oauth_token or not telegram.can_send:
        return 0
    client = HHInboxClient(settings.hh_oauth_token, settings.hh_user_agent)
    raw_seen = store.settings_by_prefix("hh_chat_last_")
    last_seen = {key.removeprefix("hh_chat_last_"): value for key, value in raw_seen.items()}
    messages = client.new_employer_messages(last_seen)
    processed = 0

    for msg in messages:
        if not msg.vacancy_id:
            # Still advance the cursor; an unbound chat must not notify forever.
            store.set_setting(f"hh_chat_last_{msg.chat_id}", msg.message_id)
            continue
        event_type = classify_employer_message(msg.text)
        created, application_id = store.record_employer_event(
            external_vacancy_id=msg.vacancy_id,
            source_event_id=f"hh-chat:{msg.chat_id}:{msg.message_id}",
            event_type=event_type,
            text=msg.text,
            event_at=msg.created_at,
            chat_id=msg.chat_id,
            sender_name=msg.sender_name,
        )
        item = store.get_by_external_id("hh", msg.vacancy_id)
        interview_at = None

        if created and event_type == "positive":
            detection = detect_interview_datetime(msg.text, msg.created_at, settings.timezone)
            if detection is not None:
                interview_at = detection.scheduled_at
                store.schedule_interview(
                    application_id=application_id,
                    scheduled_at=detection.scheduled_at,
                    timezone=settings.timezone,
                    confidence=detection.confidence,
                    evidence=detection.evidence,
                    source_event_id=f"hh-chat:{msg.chat_id}:{msg.message_id}",
                    reminders=reminder_times(detection.scheduled_at),
                )
            telegram.send_positive_response(
                item,
                sender_name=msg.sender_name,
                message_text=msg.text,
                interview_at=interview_at,
                target_salary_rub=settings.target_salary_rub,
            )
        elif created and event_type == "rejection":
            telegram.send_rejection(item, msg.sender_name, msg.text)
        elif created:
            telegram.send_employer_message(item, msg.sender_name, msg.text)

        if created:
            store.mark_employer_event_notified(f"hh-chat:{msg.chat_id}:{msg.message_id}")
            processed += 1
        store.set_setting(f"hh_chat_last_{msg.chat_id}", msg.message_id)

    return processed


def send_due_reminders(settings: Settings, store: VacancyStore, telegram: TelegramClient) -> int:
    if not telegram.can_send:
        return 0
    now = datetime.now(ZoneInfo(settings.timezone))
    sent = 0
    for row in store.due_reminders(now):
        scheduled = datetime.fromisoformat(str(row["scheduled_at"]))
        telegram.send_interview_reminder(
            title=str(row["title"] or "Вакансия"),
            company=str(row["company"] or "Компания"),
            scheduled_at=scheduled,
            kind=str(row["kind"]),
        )
        store.mark_reminder_sent(int(row["reminder_id"]))
        sent += 1
    return sent


def cycle(settings: Settings, store: VacancyStore, telegram: TelegramClient) -> tuple[int, int]:
    found = collect(settings, store)
    sent = push_new(settings, store, telegram)
    return found, sent


def _telegram_client(settings: Settings, store: VacancyStore) -> TelegramClient:
    configured = settings.telegram_chat_id
    persisted = store.get_setting("telegram_chat_id") or ""
    chat_id = configured or persisted
    if configured and configured != persisted:
        store.set_setting("telegram_chat_id", configured)
    return TelegramClient(settings.telegram_bot_token, chat_id)


def _due(store: VacancyStore, key: str, interval_seconds: int, now_epoch: int) -> bool:
    raw = store.get_setting(key) or "0"
    try:
        last = max(0, int(raw))
    except ValueError:
        last = 0
    return last == 0 or now_epoch - last >= max(60, interval_seconds)


def run_once(settings: Settings) -> int:
    store = VacancyStore(settings.db_path)
    telegram = _telegram_client(settings, store)
    try:
        found, sent = cycle(settings, store, telegram)
        inbox = poll_hh_inbox(settings, store, telegram)
        reminders = send_due_reminders(settings, store, telegram)
        print(f"JobRadar: collected={found} pushed={sent} inbox={inbox} reminders={reminders} stats={store.stats()}")
        return 0
    finally:
        store.close()


def run_tick(settings: Settings) -> int:
    store = VacancyStore(settings.db_path)
    telegram = _telegram_client(settings, store)
    try:
        found = collect(settings, store)
        processed = poll_updates(settings, store, telegram, timeout=0)
        inbox = poll_hh_inbox(settings, store, telegram)
        reminders = send_due_reminders(settings, store, telegram)
        sent = push_new(settings, store, telegram)
        print(
            f"JobRadar tick: collected={found} telegram_updates={processed} inbox={inbox} "
            f"reminders={reminders} pushed={sent} stats={store.stats()}"
        )
        return 0
    finally:
        store.close()


def run_cron(settings: Settings) -> int:
    """Short-lived resilient worker intended to run once per minute from user cron."""
    store = VacancyStore(settings.db_path)
    telegram = _telegram_client(settings, store)
    try:
        now_epoch = int(time.time())
        found = 0
        collected = False
        processed = 0
        inbox = 0
        reminders = 0
        sent = 0
        was_bound = telegram.can_send

        try:
            processed = poll_updates(settings, store, telegram, timeout=0)
        except Exception as exc:
            print(f"telegram poll error: {exc}", file=sys.stderr)

        just_bound = (not was_bound) and telegram.can_send

        if _due(store, "last_collection_epoch", settings.poll_seconds, now_epoch):
            store.set_setting("last_collection_epoch", str(now_epoch))
            try:
                found = collect(settings, store)
                store.set_setting("last_collection_success_epoch", str(now_epoch))
                collected = True
            except Exception as exc:
                print(f"collection error: {exc}", file=sys.stderr)

        if settings.hh_oauth_token and _due(store, "last_inbox_poll_epoch", settings.inbox_poll_seconds, now_epoch):
            store.set_setting("last_inbox_poll_epoch", str(now_epoch))
            try:
                inbox = poll_hh_inbox(settings, store, telegram)
            except Exception as exc:
                print(f"HH inbox error: {exc}", file=sys.stderr)

        try:
            reminders = send_due_reminders(settings, store, telegram)
        except Exception as exc:
            print(f"reminder error: {exc}", file=sys.stderr)

        if just_bound or collected:
            try:
                sent = push_new(settings, store, telegram, force=just_bound)
            except Exception as exc:
                print(f"telegram push error: {exc}", file=sys.stderr)

        print(
            f"JobRadar cron: collection_success={int(collected)} collected={found} "
            f"telegram_updates={processed} inbox={inbox} reminders={reminders} "
            f"pushed={sent} stats={store.stats()}"
        )
        return 0
    finally:
        store.close()


def run_forever(settings: Settings) -> int:
    store = VacancyStore(settings.db_path)
    telegram = _telegram_client(settings, store)
    next_collect = 0.0
    next_inbox = 0.0
    try:
        while True:
            now = time.monotonic()
            if now >= next_collect:
                try:
                    cycle(settings, store, telegram)
                except Exception as exc:
                    print(f"collection error: {exc}", file=sys.stderr)
                next_collect = now + max(settings.poll_seconds, 60)

            if settings.hh_oauth_token and now >= next_inbox:
                try:
                    poll_hh_inbox(settings, store, telegram)
                except Exception as exc:
                    print(f"HH inbox error: {exc}", file=sys.stderr)
                next_inbox = now + max(settings.inbox_poll_seconds, 60)

            try:
                send_due_reminders(settings, store, telegram)
                if telegram.enabled:
                    poll_updates(settings, store, telegram, timeout=1)
                else:
                    time.sleep(2)
            except Exception as exc:
                print(f"runtime error: {exc}", file=sys.stderr)
                time.sleep(2)
    except KeyboardInterrupt:
        return 0
    finally:
        store.close()


def main() -> int:
    settings = Settings.from_env()
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "once"
    if mode == "once":
        return run_once(settings)
    if mode == "tick":
        return run_tick(settings)
    if mode == "cron":
        return run_cron(settings)
    if mode == "run":
        return run_forever(settings)
    print("Usage: python -m jobradar.app [once|tick|cron|run]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
