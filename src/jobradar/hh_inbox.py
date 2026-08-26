from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class EmployerMessage:
    chat_id: str
    message_id: str
    vacancy_id: str
    created_at: str
    text: str
    sender_name: str


class HHInboxClient:
    """Read-only applicant inbox adapter for HeadHunter chats.

    Requires applicant OAuth. The adapter intentionally does not send messages or
    mutate negotiations; JobRadar only observes employer activity here.
    """

    BASE_URL = "https://api.hh.ru"

    def __init__(self, oauth_token: str, user_agent: str, timeout: int = 15) -> None:
        self.oauth_token = oauth_token.strip()
        self.user_agent = user_agent
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.oauth_token)

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.oauth_token:
            raise RuntimeError("HH applicant OAuth token is not configured")
        query = urlencode(params or {})
        url = f"{self.BASE_URL}{path}" + (f"?{query}" if query else "")
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.oauth_token}",
                "HH-User-Agent": self.user_agent,
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            },
        )
        with urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def list_chats(self, per_page: int = 50) -> list[dict]:
        payload = self._get("/common/chats", {"page": 0, "per_page": min(max(per_page, 1), 50)})
        return list(payload.get("items") or [])

    def chat_messages(self, chat_id: str, limit: int = 30) -> tuple[str, list[dict]]:
        payload = self._get(
            f"/common/chats/{chat_id}/messages",
            {"limit": min(max(limit, 1), 50), "order": "prev"},
        )
        return str(payload.get("vacancy_id") or ""), list(payload.get("items") or [])

    @staticmethod
    def _message_text(message: dict) -> str:
        payload = message.get("payload") or {}
        return str(payload.get("text") or "").strip()

    @staticmethod
    def _is_employer(message: dict) -> bool:
        info = message.get("sender_display_info") or {}
        return str(info.get("role") or "").upper() == "EMPLOYER"

    def new_employer_messages(self, last_seen: dict[str, str]) -> list[EmployerMessage]:
        found: list[EmployerMessage] = []
        for chat in self.list_chats():
            chat_id = str(chat.get("id") or "")
            if not chat_id:
                continue
            last_message = chat.get("last_message") or {}
            last_id = str(last_message.get("id") or "")
            if last_id and last_seen.get(chat_id) == last_id:
                continue

            vacancy_id, messages = self.chat_messages(chat_id)
            for message in messages:
                message_id = str(message.get("id") or "")
                if not message_id or message_id == last_seen.get(chat_id):
                    continue
                if not self._is_employer(message):
                    continue
                text = self._message_text(message)
                if not text:
                    continue
                sender = message.get("sender_display_info") or {}
                found.append(
                    EmployerMessage(
                        chat_id=chat_id,
                        message_id=message_id,
                        vacancy_id=vacancy_id,
                        created_at=str(message.get("creation_time") or ""),
                        text=text,
                        sender_name=str(sender.get("name") or "Работодатель"),
                    )
                )
        found.sort(key=lambda item: (item.created_at, item.message_id))
        return found


def classify_employer_message(text: str) -> str:
    lower = (text or "").lower().replace("ё", "е")
    rejection = ("отказ", "не готовы", "другого кандидата", "не сможем продолжить", "не готовы продолжить")
    positive = (
        "приглашаем",
        "пригласить",
        "собеседован",
        "интервью",
        "созвон",
        "встреч",
        "хотели бы пообщаться",
        "готовы продолжить",
        "следующий этап",
        "технический этап",
    )
    if any(marker in lower for marker in rejection):
        return "rejection"
    if any(marker in lower for marker in positive):
        return "positive"
    return "message"
