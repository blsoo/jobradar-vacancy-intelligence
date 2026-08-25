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
    client = HHClient(settings.hh_user_agent)
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
    if not telegram.enabled:
        return 0
    queue = store.unsent(settings.score_threshold, settings.max_push_per_cycle)
    sent = 0
    for item in queue:
        telegram.send_vacancy(item)
        store.mark_sent(int(item.local_id))
        sent += 1
    return sent


def _authorized_chat(settings: Settings, update: dict) -> bool:
    callback = update.get("callback_query") or {}
    message = callback.get("message") or update.get("message") or {}
    chat = message.get("chat") or {}
    return str(chat.get("id", "")) == str(settings.telegram_chat_id)


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
    if not _authorized_chat(settings, update):
        callback = update.get("callback_query") or {}
        if callback.get("id"):
            telegram.answer_callback(callback["id"], "Нет доступа")
        return

    message = update.get("message") or {}
    text = (message.get("text") or "").strip().lower()
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


def cycle(settings: Settings, store: VacancyStore, telegram: TelegramClient) -> tuple[int, int]:
    found = collect(settings, store)
    sent = push_new(settings, store, telegram)
    return found, sent


def run_once(settings: Settings) -> int:
    store = VacancyStore(settings.db_path)
    telegram = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
    try:
        found, sent = cycle(settings, store, telegram)
        print(f"JobRadar: collected={found} pushed={sent} stats={store.stats()}")
        return 0
    finally:
        store.close()


def run_forever(settings: Settings) -> int:
    store = VacancyStore(settings.db_path)
    telegram = TelegramClient(settings.telegram_bot_token, settings.telegram_chat_id)
    if not telegram.enabled:
        print("Warning: Telegram is disabled; configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")

    next_collect = 0.0
    try:
        while True:
            now = time.monotonic()
            if now >= next_collect:
                try:
                    found, sent = cycle(settings, store, telegram)
                    print(f"cycle collected={found} pushed={sent}")
                except Exception as exc:  # runtime boundary: keep the monitor alive
                    print(f"collection error: {exc}", file=sys.stderr)
                next_collect = now + max(settings.poll_seconds, 60)

            if telegram.enabled:
                try:
                    for update in telegram.get_updates(timeout=1):
                        handle_update(settings, store, telegram, update)
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
    if mode == "run":
        return run_forever(settings)
    print("Usage: python -m jobradar.app [once|run]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
