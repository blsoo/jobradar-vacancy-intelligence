from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from html import unescape
import imaplib
import re


VACANCY_ID_RE = re.compile(r"(?:hh\.ru|headhunter\.ru)/vacancy/(\d+)", re.IGNORECASE)
HH_HINTS = ("hh.ru", "headhunter", "hh mail", "hh робот")
DIRECT_EMPLOYER_HINTS = (
    "работодатель",
    "ответ работодателя",
    "сообщение от работодателя",
    "новое сообщение",
    "приглаш",
    "собеседован",
    "интервью",
    "созвон",
    "встреч",
    "отказ",
    "другого кандидата",
    "не готовы продолжить",
    "готовы продолжить",
    "следующий этап",
    "технический этап",
)
SYSTEM_HH_SUBJECT_HINTS = (
    "резюме прошло модерацию",
    "резюме опубликовано",
    "резюме обновлено",
    "резюме проверено",
    "резюме успешно создано",
    "резюме заблокировано",
    "модерац",
    "статистика резюме",
    "просмотры резюме",
    "поднимите резюме",
    "автоподнятие резюме",
    "рекомендованные вакансии",
    "вакансии для вас",
    "подборка вакансий",
    "подтвердите почту",
    "изменение пароля",
)

_HTML_DROP_BLOCKS_RE = re.compile(
    r"<(style|script|head)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_BREAKS_RE = re.compile(
    r"</?(?:br|p|div|section|article|tr|td|li|ul|ol|h[1-6])\b[^>]*>",
    re.IGNORECASE,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class EmailEmployerMessage:
    uid: int
    message_id: str
    subject: str
    sender: str
    text: str
    received_at: str
    vacancy_id: str

    @property
    def combined_text(self) -> str:
        return "\n".join(part for part in (self.subject, self.text) if part).strip()


def _decode_header(value: str) -> str:
    parts: list[str] = []
    for chunk, encoding in decode_header(value or ""):
        if isinstance(chunk, bytes):
            for candidate in (encoding, "utf-8", "cp1251", "latin-1"):
                if not candidate:
                    continue
                try:
                    parts.append(chunk.decode(candidate, errors="replace"))
                    break
                except (LookupError, UnicodeDecodeError):
                    continue
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def _part_text(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        value = part.get_payload()
        return value if isinstance(value, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _clean_html(raw: str) -> str:
    cleaned = _HTML_DROP_BLOCKS_RE.sub(" ", raw)
    cleaned = re.sub(r"<!--.*?-->", " ", cleaned, flags=re.DOTALL)
    cleaned = _HTML_BREAKS_RE.sub("\n", cleaned)
    cleaned = _HTML_TAG_RE.sub(" ", cleaned)
    cleaned = unescape(cleaned)
    cleaned = cleaned.replace("\xa0", " ")
    cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _normalize_plain(raw: str) -> str:
    cleaned = unescape(raw).replace("\xa0", " ")
    cleaned = re.sub(r"[ \t\r\f\v]+", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _message_text(message: Message) -> str:
    plain: list[str] = []
    html_parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain":
                plain.append(_part_text(part))
            elif content_type == "text/html":
                html_parts.append(_part_text(part))
    else:
        if message.get_content_type() == "text/html":
            html_parts.append(_part_text(message))
        else:
            plain.append(_part_text(message))

    if plain:
        return _normalize_plain("\n".join(plain))
    return _clean_html("\n".join(html_parts))


def _received_at(message: Message) -> str:
    raw = message.get("Date") or ""
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (TypeError, ValueError, OverflowError):
        return datetime.now(timezone.utc).isoformat()


def parse_email(uid: int, raw: bytes) -> EmailEmployerMessage:
    message = message_from_bytes(raw)
    subject = _decode_header(message.get("Subject") or "")
    sender = _decode_header(message.get("From") or "")
    text = _message_text(message)
    combined = f"{subject}\n{text}"
    vacancy_match = VACANCY_ID_RE.search(combined)
    message_id = (message.get("Message-ID") or message.get("Message-Id") or f"imap-uid-{uid}").strip()
    return EmailEmployerMessage(
        uid=int(uid),
        message_id=message_id,
        subject=subject,
        sender=sender,
        text=text,
        received_at=_received_at(message),
        vacancy_id=vacancy_match.group(1) if vacancy_match else "",
    )


def is_hh_recruiting_message(message: EmailEmployerMessage) -> bool:
    sender = message.sender.lower().replace("ё", "е")
    subject = message.subject.lower().replace("ё", "е")
    haystack = f"{message.sender}\n{message.subject}\n{message.text}".lower().replace("ё", "е")
    from_hh = any(hint in sender for hint in HH_HINTS)

    if from_hh and any(hint in subject for hint in SYSTEM_HH_SUBJECT_HINTS):
        return False

    direct_response = any(hint in haystack for hint in DIRECT_EMPLOYER_HINTS)
    if not direct_response:
        return False

    return bool(from_hh or message.vacancy_id)


class IMAPInboxClient:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 993,
        mailbox: str = "INBOX",
        timeout: int = 20,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = int(port)
        self.mailbox = mailbox or "INBOX"
        self.timeout = timeout

    def _connect(self) -> imaplib.IMAP4_SSL:
        client = imaplib.IMAP4_SSL(self.host, self.port, timeout=self.timeout)
        client.login(self.username, self.password)
        status, _ = client.select(self.mailbox, readonly=True)
        if status != "OK":
            client.logout()
            raise RuntimeError("IMAP mailbox select failed")
        return client

    @staticmethod
    def _uid_list(client: imaplib.IMAP4_SSL, criterion: str = "ALL") -> list[int]:
        status, payload = client.uid("search", None, criterion)
        if status != "OK" or not payload:
            return []
        raw = payload[0] or b""
        return [int(value) for value in raw.split() if value.isdigit()]

    def latest_uid(self) -> int:
        client = self._connect()
        try:
            uids = self._uid_list(client)
            return max(uids) if uids else 0
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def messages_after(self, last_uid: int, *, limit: int = 50) -> list[EmailEmployerMessage]:
        client = self._connect()
        try:
            criterion = f"UID {max(1, int(last_uid) + 1)}:*"
            uids = self._uid_list(client, criterion)
            if not uids:
                return []
            messages: list[EmailEmployerMessage] = []
            for uid in sorted(uids)[-max(1, int(limit)):]:
                status, payload = client.uid("fetch", str(uid), "(RFC822)")
                if status != "OK" or not payload:
                    continue
                raw = next(
                    (part[1] for part in payload if isinstance(part, tuple) and len(part) > 1 and isinstance(part[1], bytes)),
                    None,
                )
                if raw is None:
                    continue
                messages.append(parse_email(uid, raw))
            return messages
        finally:
            try:
                client.logout()
            except Exception:
                pass
