from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email import message_from_bytes
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
import imaplib
import re


VACANCY_ID_RE = re.compile(r"(?:hh\.ru|headhunter\.ru)/vacancy/(\d+)", re.IGNORECASE)
HH_HINTS = ("hh.ru", "headhunter", "hh mail", "hh робот")
RECRUITING_HINTS = (
    "ваканси",
    "отклик",
    "резюме",
    "собеседован",
    "интервью",
    "приглаш",
    "работодатель",
)


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


def _message_text(message: Message) -> str:
    plain: list[str] = []
    html: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain":
                plain.append(_part_text(part))
            elif content_type == "text/html":
                html.append(_part_text(part))
    else:
        if message.get_content_type() == "text/html":
            html.append(_part_text(message))
        else:
            plain.append(_part_text(message))
    raw = "\n".join(plain or html)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


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
    haystack = f"{message.sender}\n{message.subject}\n{message.text}".lower()
    from_hh = any(hint in message.sender.lower() for hint in HH_HINTS)
    recruiting = any(hint in haystack for hint in RECRUITING_HINTS)
    return bool((from_hh and recruiting) or (message.vacancy_id and recruiting))


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
