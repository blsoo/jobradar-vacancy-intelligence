from __future__ import annotations

from datetime import datetime
import html
import json
from urllib.request import Request, urlopen

from .career_fit import evaluate_career_fit
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
        cur = "₽" if (v.salary_currency or "") in {"RUR", "RUB"} else (v.salary_currency or "")
        if v.salary_from is not None and v.salary_to is not None:
            return f"{v.salary_from:,}–{v.salary_to:,} {cur}".replace(",", " ")
        value = v.salary_from if v.salary_from is not None else v.salary_to
        prefix = "от" if v.salary_from is not None else "до"
        return f"{prefix} {value:,} {cur}".replace(",", " ")

    def send_digest(self, items: list[RankedVacancy], *, target_salary_rub: int = 70_000) -> None:
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
            fit = evaluate_career_fit(item, target_salary_rub=target_salary_rub)
            mark = number_marks[index]
            title = html.escape(v.title)
            company = html.escape(v.company or "компания не указана")
            area = html.escape(v.area or "локация не указана")
            salary = html.escape(self._salary_text(item))
            url = html.escape(v.url or v.application_url, quote=True)
            work = html.escape(" · ".join(fit.work_with[:4]) or "задачи стоит уточнить")
            advantages = html.escape(" · ".join(fit.advantages[:4]) or "есть совпадение с целевым профилем")

            chunks.append(
                "\n"
                f"{mark} <a href=\"{url}\"><b>{title}</b></a>\n"
                f"{company} · {area}\n"
                f"💰 <b>{salary}</b>\n"
                f"🎯 Скрининг: <b>{fit.screening_label}</b> · {item.score.total}/100\n"
                f"❤️ Тебе должно зайти: <b>{fit.interest_label}</b> · {fit.interest_score}/100\n"
                f"🛠 {work}\n"
                f"✅ {advantages}"
            )
            keyboard.append(
                [
                    {"text": f"{index + 1} 🔥 Отклик", "callback_data": f"apply:{item.local_id}"},
                    {"text": f"{index + 1} 📌 Сохранить", "callback_data": f"save:{item.local_id}"},
                    {"text": f"{index + 1} ❌ Мимо", "callback_data": f"skip:{item.local_id}"},
                ]
            )

        chunks.append(
            "\n<i>Оценки — объяснимая эвристика по требованиям вакансии и целевому профилю, а не обещание оффера.</i>"
        )
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

    def send_positive_response(
        self,
        item: RankedVacancy | None,
        *,
        sender_name: str,
        message_text: str,
        interview_at: datetime | None = None,
        target_salary_rub: int = 70_000,
    ) -> None:
        if item is None:
            text = f"🎉 Положительный ответ от работодателя\n{sender_name}\n\n{message_text[:900]}"
            self.send_text(text)
            return
        fit = evaluate_career_fit(item, target_salary_rub=target_salary_rub)
        work = " · ".join(fit.work_with[:5]) or "задачи стоит уточнить"
        advantages = " · ".join(fit.advantages[:5]) or "есть совпадение с целевым профилем"
        lines = [
            "🎉 ПОЛОЖИТЕЛЬНЫЙ ОТВЕТ",
            f"{item.vacancy.title} · {item.vacancy.company}",
            f"💰 {self._salary_text(item)}",
            f"🎯 Скрининг: {fit.screening_label} · {item.score.total}/100",
            f"❤️ Насколько тебе подходит: {fit.interest_label} · {fit.interest_score}/100",
            f"🛠 С чем работать: {work}",
            f"✅ Почему интересно: {advantages}",
        ]
        if interview_at is not None:
            lines.append(f"📅 Собеседование: {interview_at.strftime('%d.%m.%Y %H:%M %Z')}")
            lines.append("⏰ Напоминания: за 24 часа, 2 часа и 30 минут")
        lines.extend(["", f"Сообщение: {message_text[:800]}"])
        self.send_text("\n".join(lines))

    def send_employer_message(self, item: RankedVacancy | None, sender_name: str, message_text: str) -> None:
        title = item.vacancy.title if item else "вакансия"
        company = item.vacancy.company if item else sender_name
        self.send_text(f"💬 Ответ работодателя\n{title} · {company}\n\n{message_text[:1000]}")

    def send_rejection(self, item: RankedVacancy | None, sender_name: str, message_text: str) -> None:
        title = item.vacancy.title if item else "вакансия"
        company = item.vacancy.company if item else sender_name
        self.send_text(f"📭 Ответ по отклику\n{title} · {company}\nСтатус: отказ\n\n{message_text[:700]}")

    def send_interview_reminder(
        self,
        *,
        title: str,
        company: str,
        scheduled_at: datetime,
        kind: str,
    ) -> None:
        labels = {
            "day_before": "завтра",
            "two_hours": "через 2 часа",
            "thirty_minutes": "через 30 минут",
        }
        when = labels.get(kind, "скоро")
        self.send_text(
            "⏰ СОБЕС " + when + "\n"
            f"{title or 'Вакансия'} · {company or 'Компания'}\n"
            f"📅 {scheduled_at.strftime('%d.%m.%Y %H:%M %Z')}\n\n"
            "Проверь ссылку/контакт, перечитай вакансию и подготовь 3–5 вопросов работодателю."
        )

    def send_text(self, text: str, *, reply_markup: dict | None = None, chat_id: str | None = None) -> None:
        payload = {"chat_id": self._target(chat_id), "text": text, "disable_web_page_preview": True}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self._call("sendMessage", payload)

    def answer_callback(self, callback_query_id: str, text: str) -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})

    def delete_message(self, chat_id: str, message_id: int) -> None:
        self._call("deleteMessage", {"chat_id": str(chat_id), "message_id": int(message_id)})

    def get_updates(self, *, offset: int = 0, timeout: int = 1) -> list[dict]:
        if not self.enabled:
            return []
        result = self._call(
            "getUpdates",
            {"offset": int(offset), "timeout": timeout, "allowed_updates": ["message", "callback_query"]},
        )
        return list(result.get("result") or [])
