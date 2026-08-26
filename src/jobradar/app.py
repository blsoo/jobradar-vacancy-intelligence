from __future__ import annotations

import sys
import time

from .config import Settings
from .cover_letter import build_cover_letter
from .hh_client import HHClient
from .models import RankedVacancy
from .scoring import score_vacancy
from .storage import VacancyStore
from .telegram import TelegramClient


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


def push_new(settings: Settings, store: VacancyStore, telegram: TelegramClient) -> int:
    if not telegram.can_send:
        return 0
    queue = store.unsent(settings.score_threshold, settings.max_push_per_cycle)
    sent = 0
    for item in queue:
        telegram.send_vacancy(item)
        store.mark_sent(int(item.local_id))
        sent += 1
    return sent


def _chat_id_from_update(update: dict) -> str:
    callback = update.get("callback_query") or {}
    message = callback.get("message") or update.get("message") or {}
    chat = message.get("chat") or {}
    return str(chat.get("id", ""))


def _authorized_chat(telegram: TelegramClient, update: dict) -> bool:
    return bool(telegram.chat_id) and _chat_id_from_update(update) == str(telegram.chat_id)


def _stats_text(store: VacancyStore) -> str:
    stats = store.stats()
    reasons = store.skip_reason_stats()
    reason_text = ", ".join(f"{SKIP_REASONS.get(k, k)}: {v}" for k, v in list(reasons.items())[:4])
    return (
        "📊 JobRadar\n"
        f"Вакансий в базе: {stats['total']}\n"
        f"Отправлено: {stats['sent']}\n"
        f"Сохранено: {stats['saved']}\n"
        f"К отклику: {stats['apply_requested']}\n"
        f"Пропущено: {stats['skipped']}"
        + (f"\nПричины пропуска: {reason_text}" if reason_text else "")
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
                "✅ JobRadar привязан к этому чату.\n"
                "Теперь сюда будут приходить только вакансии выше порога.\n"
                "Команда /stats покажет текущую воронку."
            )
        return

    if not _authorized_chat(telegram, update):
        callback = update.get("callback_query") or {}
        if callback.get("id"):
            telegram.answer_callback(callback["id"], "Нет доступа")
        return

    if text == "/start":
        telegram.send_text("✅ JobRadar уже привязан к этому чату. /stats — статистика.")
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
        telegram.answer_callback(callback_id, "Почему пропускаем?")
        telegram.send_text(
            f"❌ Почему не подходит «{item.vacancy.title}»?",
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": "💸 Зарплата", "callback_data": f"skipr:{local_id}:salary"},
                        {"text": "🏢 Офис", "callback_data": f"skipr:{local_id}:office"},
                    ],
                    [
                        {"text": "📈 Seniority", "callback_data": f"skipr:{local_id}:seniority"},
                        {"text": "🧩 Стек", "callback_data": f"skipr:{local_id}:stack"},
                    ],
                    [{"text": "Другое", "callback_data": f"skipr:{local_id}:other"}],
                ]
            },
        )
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
            "Кнопка ведёт прямо в форму отклика HH, если HH отдал её для вакансии. После реальной отправки отметь это ниже.",
            reply_markup={
                "inline_keyboard": [
                    [{"text": "⚡ Открыть форму отклика HH", "url": item.vacancy.application_url}],
                    [{"text": "✅ Я откликнулся", "callback_data": f"applied:{local_id}"}],
                ]
            },
        )
        return

    if action == "applied":
        store.decide(local_id, "applied")
        telegram.answer_callback(callback_id, "✅ Отклик отмечен")
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
    """Process Telegram updates exactly once across short-lived worker runs."""
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


def _collection_due(store: VacancyStore, interval_seconds: int, now_epoch: int) -> bool:
    raw = store.get_setting("last_collection_epoch") or "0"
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
        print(f"JobRadar: collected={found} pushed={sent} stats={store.stats()}")
        return 0
    finally:
        store.close()


def run_tick(settings: Settings) -> int:
    store = VacancyStore(settings.db_path)
    telegram = _telegram_client(settings, store)
    try:
        found = collect(settings, store)
        processed = poll_updates(settings, store, telegram, timeout=0)
        sent = push_new(settings, store, telegram)
        print(
            f"JobRadar tick: collected={found} telegram_updates={processed} "
            f"pushed={sent} stats={store.stats()}"
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
        sent = 0
        was_bound = telegram.can_send

        # Chat control is independent from vacancy collection. A temporary HH
        # outage must not stop /start, /stats or callback buttons from working.
        try:
            processed = poll_updates(settings, store, telegram, timeout=0)
        except Exception as exc:
            print(f"telegram poll error: {exc}", file=sys.stderr)

        just_bound = (not was_bound) and telegram.can_send

        if _collection_due(store, settings.poll_seconds, now_epoch):
            # Record the attempt before network I/O so a failing source does not
            # get hammered every minute. Normal retry cadence remains 5 minutes.
            store.set_setting("last_collection_epoch", str(now_epoch))
            try:
                found = collect(settings, store)
                store.set_setting("last_collection_success_epoch", str(now_epoch))
                collected = True
            except Exception as exc:
                print(f"collection error: {exc}", file=sys.stderr)

        # Do not drain the backlog every minute. Send one bounded batch only
        # after the owner first binds or after a successful vacancy scan.
        if just_bound or collected:
            try:
                sent = push_new(settings, store, telegram)
            except Exception as exc:
                print(f"telegram push error: {exc}", file=sys.stderr)

        print(
            f"JobRadar cron: collection_success={int(collected)} collected={found} "
            f"telegram_updates={processed} pushed={sent} stats={store.stats()}"
        )
        return 0
    finally:
        store.close()


def run_forever(settings: Settings) -> int:
    store = VacancyStore(settings.db_path)
    telegram = _telegram_client(settings, store)
    if not telegram.enabled:
        print("Warning: Telegram is disabled; configure TELEGRAM_BOT_TOKEN")
    elif not telegram.chat_id:
        print("Telegram token configured; waiting for the first /start to bind the owner chat")

    next_collect = 0.0
    try:
        while True:
            now = time.monotonic()
            if now >= next_collect:
                try:
                    found, sent = cycle(settings, store, telegram)
                    print(f"cycle collected={found} pushed={sent}")
                except Exception as exc:
                    print(f"collection error: {exc}", file=sys.stderr)
                next_collect = now + max(settings.poll_seconds, 60)

            if telegram.enabled:
                try:
                    poll_updates(settings, store, telegram, timeout=1)
                except Exception as exc:
                    print(f"telegram error: {exc}", file=sys.stderr)
                    time.sleep(2)
            else:
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
