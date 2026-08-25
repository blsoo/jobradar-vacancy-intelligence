from __future__ import annotations

import html
import json
from urllib.request import Request, urlopen

from .models import RankedVacancy


class TelegramClient:
    def __init__(self, token: str, chat_id: str = "", timeout: int = 20) -> None:
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    @property
    def can_send(self) -> bool:
        return bool(self.token and self.chat_id)

    def bind_chat(self, chat_id: str) -> None:
        self.chat_id = str(chat_id)

    def _call(self, method: str, payload: dict | None = None) -> dict:
        if not self.token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
        body = json.dumps(payload or {}).encode("utf-8")
        request = Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not result.get("ok"):
            raise RuntimeError(f"Telegram API error: {result}")
        return result

    def _target(self, chat_id: str | None = None) -> str:
        target = str(chat_id or self.chat_id or "")
        if not target:
            raise RuntimeError("Telegram chat is not bound yet")
        return target

    @staticmethod
    def _salary_text(item: RankedVacancy) -> str:
        v = item.vacancy
        if v.salary_from is None and v.salary_to is None:
            return "не указана"
        cur = v.salary_currency or ""
        if v.salary_from is not None and v.salary_to is not None:
            return f"{v.salary_from:,}–{v.salary_to:,} {cur}".replace(",", " ")
        value = v.salary_from if v.salary_from is not None else v.salary_to
        prefix = "от" if v.salary_from is not None else "до"
        return f"{prefix} {value:,} {cur}".replace(",", " ")

    def send_vacancy(self, item: RankedVacancy) -> None:
        if item.local_id is None:
            raise ValueError("local_id is required for Telegram callbacks")
        v = item.vacancy
        matched = ", ".join(item.score.matched[:7]) or "нет явных совпадений"
        risks = ", ".join(item.score.risks[:4]) or "явных рисков не найдено"
        text = (
            f"<b>🔥 {item.score.total}/100 · {html.escape(v.title)}</b>\n"
            f"{html.escape(v.company)} · {html.escape(v.area or 'локация не указана')}\n"
            f"Формат: {html.escape(v.schedule or 'не указан')}\n"
            f"Зарплата: {html.escape(self._salary_text(item))}\n\n"
            f"<b>Совпало:</b> {html.escape(matched)}\n"
            f"<b>Риски:</b> {html.escape(risks)}"
        )
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "🔥 Подготовить отклик", "callback_data": f"apply:{item.local_id}"},
                    {"text": "📌 Сохранить", "callback_data": f"save:{item.local_id}"},
                ],
                [
                    {"text": "❌ Пропустить", "callback_data": f"skip:{item.local_id}"},
                    {"text": "⚡ Форма отклика HH", "url": v.application_url},
                ],
            ]
        }
        self._call(
            "sendMessage",
            {
                "chat_id": self._target(),
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": keyboard,
            },
        )

    def send_text(self, text: str, *, reply_markup: dict | None = None, chat_id: str | None = None) -> None:
        payload = {"chat_id": self._target(chat_id), "text": text, "disable_web_page_preview": True}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self._call("sendMessage", payload)

    def answer_callback(self, callback_query_id: str, text: str) -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})

    def get_updates(self, *, offset: int = 0, timeout: int = 1) -> list[dict]:
        if not self.enabled:
            return []
        result = self._call(
            "getUpdates",
            {"offset": int(offset), "timeout": timeout, "allowed_updates": ["message", "callback_query"]},
        )
        return list(result.get("result") or [])
