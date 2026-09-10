from __future__ import annotations

from datetime import datetime
import sys
import time
from zoneinfo import ZoneInfo

from .config import Settings
from .cover_letter import build_cover_letter
from .email_worker import run as run_email_worker
from .hh_client import HHClient
from .hh_inbox import HHInboxClient, classify_employer_message
from .hh_oauth import HHOAuthManager
from .interviews import detect_interview_datetime, reminder_times
from .models import RankedVacancy
from .scoring import score_vacancy
from .storage import VacancyStore
from .telegram import TelegramClient


DIGEST_COOLDOWN_SECONDS = 30 * 60
DIGEST_SIZE = 3
FALLBACK_MIN_SCORE = 60
FALLBACK_HOUR_LOCAL = 18
TELEGRAM_UI_VERSION = "4"

PANEL_COMMANDS = {
    "🎯 Вакансии": "/vacancies",
    "🔄 Обновить": "/refresh",
    "📌 Сохранённые": "/saved",
    "🔥 Отклики": "/applications",
    "📬 Ответы": "/responses",
    "📊 Статистика": "/stats",
    "🚫 Стоп-лист": "/blacklist",
    "ℹ️ Помощь": "/help",
}

SKIP_REASONS = {
    "salary": "зарплата",
    "office": "офис / география",
    "seniority": "слишком высокий уровень",
    "stack": "не мой стек",
    "direction": "не то направление",
    "other": "другое",
}

MANUAL_RESPONSE_LABELS = {
    "positive": "🎉 Пригласили",
    "message": "💬 Ответили",
    "rejection": "📭 Отказ",
}


def _oauth_manager(settings: Settings, store: VacancyStore) -> HHOAuthManager:
    """Optional legacy/official adapter; public JobRadar UX does not require it."""
    return HHOAuthManager(
        store,
        client_id=settings.hh_client_id,
        client_secret=settings.hh_client_secret,
        redirect_uri=settings.hh_redirect_uri,
        bootstrap_access_token=settings.hh_oauth_token,
        user_agent=settings.hh_user_agent,
    )


def collect(settings: Settings, store: VacancyStore) -> int:
    token = _oauth_manager(settings, store).access_token()
    client = HHClient(settings.hh_user_agent, token)
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


def _local_date(settings: Settings, now_epoch: int) -> str:
    return datetime.fromtimestamp(now_epoch, ZoneInfo(settings.timezone)).date().isoformat()


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
    store.set_setting("last_primary_digest_date", _local_date(settings, now_epoch))
    return len(queue)


def _fallback_due(settings: Settings, store: VacancyStore, now_epoch: int) -> bool:
    local_now = datetime.fromtimestamp(now_epoch, ZoneInfo(settings.timezone))
    today = local_now.date().isoformat()
    if local_now.hour < FALLBACK_HOUR_LOCAL:
        return False
    if store.get_setting("last_primary_digest_date") == today:
        return False
    return store.get_setting("last_fallback_digest_date") != today


def push_daily_fallback(
    settings: Settings,
    store: VacancyStore,
    telegram: TelegramClient,
    *,
    now_epoch: int | None = None,
) -> int:
    if not telegram.can_send:
        return 0
    now_epoch = int(time.time()) if now_epoch is None else int(now_epoch)
    if not _fallback_due(settings, store, now_epoch):
        return 0
    limit = max(1, min(DIGEST_SIZE, settings.max_push_per_cycle))
    candidates = store.unsent(FALLBACK_MIN_SCORE, max(12, limit * 4))
    queue = [item for item in candidates if item.score.total < settings.score_threshold][:limit]
    if not queue:
        return 0
    telegram.send_digest(queue, target_salary_rub=settings.target_salary_rub)
    for item in queue:
        store.mark_sent(int(item.local_id))
    store.set_setting("last_fallback_digest_date", _local_date(settings, now_epoch))
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


def _response_source_status(settings: Settings) -> str:
    if settings.email_monitor_enabled:
        return "🟢 почтовые уведомления HH отслеживаются автоматически"
    return "🟡 автоматическая почта не подключена; ручные статусы работают"


def _home_text(settings: Settings, store: VacancyStore) -> str:
    return (
        "🧭 JobRadar · панель управления\n\n"
        "🎯 Вакансии — показать новые подходящие\n"
        "🔄 Обновить — прямо сейчас проверить HH\n"
        "📌 Сохранённые — вернуться к отложенным\n"
        "🔥 Отклики — текущая воронка\n"
        "📬 Ответы — приглашения, ответы и отказы\n"
        "🚫 Стоп-лист — компании с повторными холодными отказами\n\n"
        f"HH поиск: 🟢 работает · {_response_source_status(settings)}\n"
        f"Основной порог: {settings.score_threshold}/100"
    )


def _help_text(settings: Settings) -> str:
    return (
        "ℹ️ JobRadar\n\n"
        "Бот собирает и ранжирует вакансии HH без необходимости в applicant OAuth, "
        "пишет отдельное сопроводительное под каждую, помнит решения и перестаёт "
        "предлагать компании после повторных холодных отказов.\n\n"
        "У вакансии:\n"
        "🔥 Отклик — персональное сопроводительное + официальная форма HH\n"
        "📌 Сохранить — отложить\n"
        "❌ Мимо — выбрать причину для обратной связи\n"
        "👁 HH — открыть оригинал вакансии\n\n"
        "После «✅ Я откликнулся» можно отметить: 🎉 Пригласили, 💬 Ответили или 📭 Отказ. "
        "Эти статусы идут в воронку и учитываются при авто-стопе компаний.\n\n"
        f"Ответы: {_response_source_status(settings)}."
    )


def _saved_items(store: VacancyStore, limit: int = 3) -> list[RankedVacancy]:
    rows = store.conn.execute(
        """
        SELECT * FROM vacancies
        WHERE decision='saved'
        ORDER BY COALESCE(decision_at, updated_at) DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    ).fetchall()
    return [store._to_ranked(row) for row in rows]


def _applications_text(store: VacancyStore) -> str:
    rows = store.conn.execute(
        """
        SELECT a.status, a.applied_at, a.last_employer_event_at,
               v.title, v.company
        FROM applications a
        LEFT JOIN vacancies v ON v.id=a.vacancy_id
        ORDER BY COALESCE(a.last_employer_event_at, a.applied_at, a.updated_at) DESC
        LIMIT 10
        """
    ).fetchall()
    if not rows:
        return "🔥 Откликов пока нет. У вакансии нажми «🔥 Отклик», а после отправки — «✅ Я откликнулся»."
    labels = {
        "applied": "📨 отклик",
        "in_progress": "💬 в работе",
        "invited": "🎉 приглашение",
        "rejected": "📭 отказ",
    }
    lines = ["🔥 Отклики · последние 10"]
    for row in rows:
        status = labels.get(str(row["status"]), str(row["status"]))
        title = str(row["title"] or "Вакансия")
        company = str(row["company"] or "Компания")
        lines.append(f"• {status} · {title} · {company}")
    return "\n".join(lines)


def _responses_text(settings: Settings, store: VacancyStore) -> str:
    counts = {
        str(row["event_type"]): int(row["total"])
        for row in store.conn.execute(
            "SELECT event_type, COUNT(*) AS total FROM employer_events GROUP BY event_type"
        ).fetchall()
    }
    rows = store.conn.execute(
        """
        SELECT e.event_type, e.event_at, e.created_at,
               v.title, v.company, e.sender_name
        FROM employer_events e
        JOIN applications a ON a.id=e.application_id
        LEFT JOIN vacancies v ON v.id=a.vacancy_id
        ORDER BY COALESCE(e.event_at, e.created_at) DESC, e.id DESC
        LIMIT 8
        """
    ).fetchall()
    lines = [
        "📬 Ответы работодателей",
        _response_source_status(settings),
        "",
        f"🎉 Приглашений: {counts.get('positive', 0)}",
        f"💬 Других ответов: {counts.get('message', 0)}",
        f"📭 Отказов: {counts.get('rejection', 0)}",
    ]
    if rows:
        labels = {"positive": "🎉", "message": "💬", "rejection": "📭"}
        lines.append("\nПоследние:")
        for row in rows:
            mark = labels.get(str(row["event_type"]), "💬")
            title = str(row["title"] or "Вакансия")
            company = str(row["company"] or row["sender_name"] or "Компания")
            lines.append(f"• {mark} {title} · {company}")
    else:
        lines.append("\nПока ответов не записано. После отклика используй кнопки статуса под вакансией.")
    return "\n".join(lines)


def _blacklist_text(store: VacancyStore) -> str:
    feedback = store.company_feedback()
    blocked = store.suppressed_companies()
    if not blocked:
        return "🚫 Стоп-лист пока пуст. Компания попадёт сюда после 3 отдельных холодных отказов."
    rows = sorted(
        ((name, feedback.get(name, {})) for name in blocked),
        key=lambda item: (-int(item[1].get("cold_rejections", 0)), item[0]),
    )
    lines = ["🚫 Авто-стоп компаний"]
    for name, stats in rows[:20]:
        lines.append(f"• {name} — холодных отказов: {stats.get('cold_rejections', 0)}")
    lines.append("\nИх новые вакансии JobRadar больше не присылает автоматически.")
    return "\n".join(lines)


def _skip_reason_markup(local_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "💰 Зарплата", "callback_data": f"skipr:{local_id}:salary"},
                {"text": "🏢 Офис / город", "callback_data": f"skipr:{local_id}:office"},
            ],
            [
                {"text": "🎓 Уровень", "callback_data": f"skipr:{local_id}:seniority"},
                {"text": "🧩 Стек", "callback_data": f"skipr:{local_id}:stack"},
            ],
            [
                {"text": "🧭 Не то направление", "callback_data": f"skipr:{local_id}:direction"},
                {"text": "📝 Другое", "callback_data": f"skipr:{local_id}:other"},
            ],
        ]
    }


def _response_markup(local_id: int, hh_url: str = "") -> dict:
    rows = [
        [
            {"text": "🎉 Пригласили", "callback_data": f"resp:{local_id}:positive"},
            {"text": "💬 Ответили", "callback_data": f"resp:{local_id}:message"},
            {"text": "📭 Отказ", "callback_data": f"resp:{local_id}:rejection"},
        ]
    ]
    if hh_url:
        rows.append([{"text": "👁 Открыть вакансию на HH", "url": hh_url}])
    return {"inline_keyboard": rows}


def _ensure_telegram_ui(store: VacancyStore, telegram: TelegramClient, settings: Settings) -> None:
    if not telegram.enabled:
        return
    try:
        telegram.configure_ui()
        if telegram.can_send and store.get_setting("telegram_ui_version") != TELEGRAM_UI_VERSION:
            telegram.send_home("✅ Панель JobRadar обновлена.\n\n" + _home_text(settings, store))
            store.set_setting("telegram_ui_version", TELEGRAM_UI_VERSION)
    except Exception as exc:
        print(f"telegram ui error: {type(exc).__name__}", file=sys.stderr)


def _handle_panel_command(
    settings: Settings,
    store: VacancyStore,
    telegram: TelegramClient,
    command: str,
) -> bool:
    text = command.lower()

    if text in {"/start", "/menu"}:
        telegram.send_home(_home_text(settings, store))
        return True

    if text == "/vacancies":
        sent = push_new(settings, store, telegram, force=True)
        if sent == 0:
            telegram.send_home("🎯 Новых вакансий выше текущего порога пока нет.")
        return True

    if text == "/refresh":
        try:
            found = collect(settings, store)
            sent = push_new(settings, store, telegram, force=True)
            if sent == 0:
                telegram.send_home(f"🔄 Проверил HH: найдено/обновлено {found}. Новых подходящих для отправки сейчас нет.")
            else:
                telegram.send_text(f"🔄 HH обновлён: обработано {found}, показано новых вакансий: {sent}.")
        except Exception as exc:
            telegram.send_text(f"⚠️ Не удалось обновить вакансии: {type(exc).__name__}")
        return True

    if text == "/saved":
        items = _saved_items(store)
        if not items:
            telegram.send_home("📌 Сохранённых вакансий пока нет.")
        else:
            telegram.send_digest(items, target_salary_rub=settings.target_salary_rub, header="📌 Сохранённые вакансии")
        return True

    if text == "/applications":
        telegram.send_text(_applications_text(store))
        return True

    if text in {"/responses", "/hh_status"}:
        telegram.send_text(_responses_text(settings, store))
        return True

    if text == "/stats":
        telegram.send_text(_stats_text(store))
        return True

    if text == "/blacklist":
        telegram.send_text(_blacklist_text(store))
        return True

    if text == "/help":
        telegram.send_home(_help_text(settings))
        return True

    return False


def handle_update(settings: Settings, store: VacancyStore, telegram: TelegramClient, update: dict) -> None:
    message = update.get("message") or {}
    raw_text = (message.get("text") or "").strip()
    raw_text = PANEL_COMMANDS.get(raw_text, raw_text)
    text = raw_text.lower()
    incoming_chat_id = _chat_id_from_update(update)

    if not telegram.chat_id:
        if text == "/start" and incoming_chat_id:
            telegram.bind_chat(incoming_chat_id)
            store.set_setting("telegram_chat_id", incoming_chat_id)
            try:
                telegram.configure_ui()
            except Exception:
                pass
            telegram.send_home("✅ JobRadar привязан.\n\n" + _home_text(settings, store))
            store.set_setting("telegram_ui_version", TELEGRAM_UI_VERSION)
        return

    if not _authorized_chat(telegram, update):
        callback = update.get("callback_query") or {}
        if callback.get("id"):
            telegram.answer_callback(callback["id"], "Нет доступа")
        return

    if raw_text and _handle_panel_command(settings, store, telegram, raw_text):
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
        telegram.answer_callback(callback_id, "Выбери причину")
        telegram.send_text(
            f"❌ Почему мимо: {item.vacancy.title} · {item.vacancy.company}?",
            reply_markup=_skip_reason_markup(local_id),
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
            "Открой официальную форму HH, отправь отклик и нажми «✅ Я откликнулся».",
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
        telegram.answer_callback(callback_id, "✅ Отклик записан")
        telegram.send_text(
            "✅ Отклик добавлен в воронку. Когда работодатель ответит, отметь результат:",
            reply_markup=_response_markup(local_id, item.vacancy.url),
        )
        return

    if action == "resp" and extra in MANUAL_RESPONSE_LABELS:
        now = datetime.now(ZoneInfo(settings.timezone)).isoformat()
        source_id = f"manual:{callback_id or f'{local_id}:{extra}:{time.time_ns()}'}"
        created, _ = store.record_employer_event(
            external_vacancy_id=item.vacancy.external_id,
            source_event_id=source_id,
            event_type=extra,
            text=f"Ручной статус: {MANUAL_RESPONSE_LABELS[extra]}",
            event_at=now,
            chat_id=str(telegram.chat_id),
            sender_name=item.vacancy.company or "Работодатель",
        )
        store.mark_employer_event_notified(source_id)
        if callback_id:
            telegram.answer_callback(callback_id, MANUAL_RESPONSE_LABELS[extra] if created else "Уже учтено")
        if created and extra == "rejection":
            blocked = item.vacancy.company and item.vacancy.company.casefold() in store.suppressed_companies()
            suffix = " Компания теперь в авто-стопе." if blocked else ""
            telegram.send_text(f"📭 Отказ записан.{suffix}")
        elif created and extra == "positive":
            telegram.send_text("🎉 Приглашение записано. Вакансия отмечена как перспективная.")
        return

    if callback_id:
        telegram.answer_callback(callback_id, "Неизвестное действие")


def poll_updates(settings: Settings, store: VacancyStore, telegram: TelegramClient, *, timeout: int = 1) -> int:
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


def _refresh_full_vacancy(
    settings: Settings,
    store: VacancyStore,
    vacancy_id: str,
    access_token: str,
) -> RankedVacancy | None:
    try:
        vacancy = HHClient(settings.hh_user_agent, access_token).get_vacancy(vacancy_id)
        score = score_vacancy(vacancy, target_salary_rub=settings.target_salary_rub, remote_preferred=settings.remote_preferred)
        local_id = store.upsert(RankedVacancy(vacancy=vacancy, score=score))
        return store.get(local_id)
    except Exception:
        return store.get_by_external_id("hh", vacancy_id)


def poll_hh_inbox(settings: Settings, store: VacancyStore, telegram: TelegramClient) -> int:
    """Optional official HH inbox adapter; inactive without a valid applicant token."""
    access_token = _oauth_manager(settings, store).access_token()
    if not access_token or not telegram.can_send:
        return 0
    client = HHInboxClient(access_token, settings.hh_user_agent)
    raw_seen = store.settings_by_prefix("hh_chat_last_")
    last_seen = {key.removeprefix("hh_chat_last_"): value for key, value in raw_seen.items()}
    messages = client.new_employer_messages(last_seen)
    processed = 0

    for msg in messages:
        if not msg.vacancy_id:
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
            item = _refresh_full_vacancy(settings, store, msg.vacancy_id, access_token)
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


def _poll_email(settings: Settings) -> None:
    if not settings.email_monitor_enabled:
        return
    run_email_worker(settings)


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
        _ensure_telegram_ui(store, telegram, settings)
        found, sent = cycle(settings, store, telegram)
        inbox = poll_hh_inbox(settings, store, telegram)
        if settings.email_monitor_enabled:
            _poll_email(settings)
        reminders = send_due_reminders(settings, store, telegram)
        print(f"JobRadar: collected={found} pushed={sent} inbox={inbox} reminders={reminders} stats={store.stats()}")
        return 0
    finally:
        store.close()


def run_tick(settings: Settings) -> int:
    store = VacancyStore(settings.db_path)
    telegram = _telegram_client(settings, store)
    try:
        _ensure_telegram_ui(store, telegram, settings)
        found = collect(settings, store)
        processed = poll_updates(settings, store, telegram, timeout=0)
        inbox = poll_hh_inbox(settings, store, telegram)
        if settings.email_monitor_enabled:
            _poll_email(settings)
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
    store = VacancyStore(settings.db_path)
    telegram = _telegram_client(settings, store)
    try:
        _ensure_telegram_ui(store, telegram, settings)
        now_epoch = int(time.time())
        found = 0
        collected = False
        processed = 0
        inbox = 0
        reminders = 0
        sent = 0
        fallback_sent = 0
        was_bound = telegram.can_send

        try:
            processed = poll_updates(settings, store, telegram, timeout=0)
        except Exception as exc:
            print(f"telegram poll error: {type(exc).__name__}", file=sys.stderr)
        just_bound = (not was_bound) and telegram.can_send

        if _due(store, "last_collection_epoch", settings.poll_seconds, now_epoch):
            store.set_setting("last_collection_epoch", str(now_epoch))
            try:
                found = collect(settings, store)
                store.set_setting("last_collection_success_epoch", str(now_epoch))
                collected = True
            except Exception as exc:
                print(f"collection error: {type(exc).__name__}", file=sys.stderr)

        oauth = _oauth_manager(settings, store)
        if oauth.connected() and _due(store, "last_inbox_poll_epoch", settings.inbox_poll_seconds, now_epoch):
            store.set_setting("last_inbox_poll_epoch", str(now_epoch))
            try:
                inbox = poll_hh_inbox(settings, store, telegram)
            except Exception as exc:
                print(f"HH inbox error: {type(exc).__name__}", file=sys.stderr)

        if settings.email_monitor_enabled and _due(store, "last_email_poll_epoch", settings.email_poll_seconds, now_epoch):
            store.set_setting("last_email_poll_epoch", str(now_epoch))
            try:
                _poll_email(settings)
            except Exception as exc:
                print(f"email inbox error: {type(exc).__name__}", file=sys.stderr)

        try:
            reminders = send_due_reminders(settings, store, telegram)
        except Exception as exc:
            print(f"reminder error: {type(exc).__name__}", file=sys.stderr)

        if just_bound or collected:
            try:
                sent = push_new(settings, store, telegram, force=just_bound)
            except Exception as exc:
                print(f"telegram push error: {type(exc).__name__}", file=sys.stderr)

        if collected and sent == 0:
            try:
                fallback_sent = push_daily_fallback(settings, store, telegram, now_epoch=now_epoch)
            except Exception as exc:
                print(f"telegram fallback error: {type(exc).__name__}", file=sys.stderr)

        print(
            f"JobRadar cron: collection_success={int(collected)} collected={found} "
            f"telegram_updates={processed} inbox={inbox} reminders={reminders} "
            f"pushed={sent} fallback={fallback_sent} stats={store.stats()}"
        )
        return 0
    finally:
        store.close()


def run_forever(settings: Settings) -> int:
    store = VacancyStore(settings.db_path)
    telegram = _telegram_client(settings, store)
    next_collect = 0.0
    next_inbox = 0.0
    next_email = 0.0
    try:
        _ensure_telegram_ui(store, telegram, settings)
        while True:
            now = time.monotonic()
            if now >= next_collect:
                try:
                    cycle(settings, store, telegram)
                except Exception as exc:
                    print(f"collection error: {type(exc).__name__}", file=sys.stderr)
                next_collect = now + max(settings.poll_seconds, 60)

            if _oauth_manager(settings, store).connected() and now >= next_inbox:
                try:
                    poll_hh_inbox(settings, store, telegram)
                except Exception as exc:
                    print(f"HH inbox error: {type(exc).__name__}", file=sys.stderr)
                next_inbox = now + max(settings.inbox_poll_seconds, 60)

            if settings.email_monitor_enabled and now >= next_email:
                try:
                    _poll_email(settings)
                except Exception as exc:
                    print(f"email inbox error: {type(exc).__name__}", file=sys.stderr)
                next_email = now + max(settings.email_poll_seconds, 60)

            try:
                send_due_reminders(settings, store, telegram)
                if telegram.enabled:
                    poll_updates(settings, store, telegram, timeout=1)
                else:
                    time.sleep(2)
            except Exception as exc:
                print(f"runtime error: {type(exc).__name__}", file=sys.stderr)
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
