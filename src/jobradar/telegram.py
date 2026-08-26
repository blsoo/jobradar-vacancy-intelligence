from __future__ import annotations

import html
import json
from urllib.request import Request, urlopen

from .models import RankedVacancy


MATCH_LABELS = {
    "system analysis": "системный анализ",
    "internship": "стажировка",
    "junior": "junior",
    "SQL": "SQL",
    "REST": "REST",
    "HTTP": "HTTP",
    "JSON": "JSON",
    "API": "API",
    "Swagger/OpenAPI": "Swagger/OpenAPI",
    "requirements": "требования",
    "UML": "UML",
    "BPMN": "BPMN",
    "integrations": "интеграции",
    "PostgreSQL": "PostgreSQL",
    "Git": "Git",
    "core junior analyst match": "прямое попадание в junior SA",
    "remote": "удалёнка",
    "no experience required": "без опыта",
}


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
        cur = "₽" if (v.salary_currency or "") in {"RUR", "RUB"} else (v.salary_currency or "")
        if v.salary_from is not None and v.salary_to is not None:
            return f"{v.salary_from:,}–{v.salary_to:,} {cur}".replace(",", " ")
        value = v.salary_from if v.salary_from is not None else v.salary_to
        prefix = "от" if v.salary_from is not None else "до"
        return f"{prefix} {value:,} {cur}".replace(",", " ")

    @staticmethod
    def _screening_label(score: int) -> str:
        if score >= 88:
            return "очень высокая"
        if score >= 78:
            return "высокая"
        if score >= 68:
            return "выше средней"
        return "средняя"

    @staticmethod
    def _strengths(item: RankedVacancy) -> str:
        strengths: list[str] = []
        for raw in item.score.matched:
            if raw.startswith("salary ≥"):
                label = "зарплата от твоей цели"
            else:
                label = MATCH_LABELS.get(raw, raw)
            if label not in strengths:
                strengths.append(label)
            if len(strengths) >= 4:
                break
        return " · ".join(strengths) or "есть совпадение с junior-профилем"

    def send_digest(self, items: list[RankedVacancy]) -> None:
        if not items:
            return
        items = items[:3]
        number_marks = ["1️⃣", "2️⃣", "3️⃣"]
        chunks = ["🎯 <b>JobRadar · лучшие новые вакансии</b>"]
        keyboard: list[list[dict]] = []

        for index, item in enumerate(items):
            if item.local_id is None:
                raise ValueError("local_id is required for Telegram callbacks")
            v = item.vacancy
            mark = number_marks[index]
            title = html.escape(v.title)
            company = html.escape(v.company or "компания не указана")
            area = html.escape(v.area or "локация не указана")
            schedule = html.escape(v.schedule or "формат не указан")
            salary = html.escape(self._salary_text(item))
            screening = self._screening_label(item.score.total)
            strengths = html.escape(self._strengths(item))
            url = html.escape(v.url or v.application_url, quote=True)

            chunks.append(
                "\n"
                f"{mark} <a href=\"{url}\"><b>{title}</b></a>\n"
                f"{company} · {area}\n"
                f"💰 {salary} · 🏠 {schedule}\n"
                f"📈 Шанс первичного скрининга: <b>{screening}</b> · {item.score.total}/100\n"
                f"✅ {strengths}"
            )
            keyboard.append(
                [
                    {"text": f"{index + 1} 🔥 Отклик", "callback_data": f"apply:{item.local_id}"},
                    {"text": f"{index + 1} 📌 Сохранить", "callback_data": f"save:{item.local_id}"},
                    {"text": f"{index + 1} ❌ Мимо", "callback_data": f"skip:{item.local_id}"},
                ]
            )

        chunks.append("\n<i>Шанс скрининга — эвристическая оценка JobRadar по совпадению требований с текущим профилем, не статистическая гарантия оффера.</i>")
        self._call(
            "sendMessage",
            {
                "chat_id": self._target(),
                "text": "\n".join(chunks),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {"inline_keyboard": keyboard},
            },
        )

    def send_vacancy(self, item: RankedVacancy) -> None:
        self.send_digest([item])

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
