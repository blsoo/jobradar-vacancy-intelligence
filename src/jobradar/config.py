from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _load_env_file(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    db_path: str
    poll_seconds: int
    score_threshold: int
    max_push_per_cycle: int
    target_salary_rub: int
    remote_preferred: bool
    hh_search_queries: tuple[str, ...]
    hh_area: str
    hh_per_page: int
    hh_user_agent: str
    hh_oauth_token: str
    hh_resume_id: str
    timezone: str = "Europe/Moscow"
    inbox_poll_seconds: int = 60
    hh_client_id: str = ""
    hh_client_secret: str = ""
    hh_redirect_uri: str = "https://github.com/blsoo/jobradar-vacancy-intelligence"
    email_imap_host: str = ""
    email_imap_port: int = 993
    email_imap_username: str = ""
    email_imap_password: str = ""
    email_imap_mailbox: str = "INBOX"
    email_poll_seconds: int = 60

    @property
    def email_monitor_enabled(self) -> bool:
        return bool(self.email_imap_host and self.email_imap_username and self.email_imap_password)

    @classmethod
    def from_env(cls) -> "Settings":
        _load_env_file()
        queries = tuple(
            q.strip()
            for q in os.getenv(
                "HH_SEARCH_QUERIES",
                "стажер системный аналитик;junior системный аналитик;системный аналитик стажер;integration analyst",
            ).split(";")
            if q.strip()
        )
        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            db_path=os.getenv("JOBRADAR_DB", "jobradar.db"),
            poll_seconds=int(os.getenv("JOBRADAR_POLL_SECONDS", "300")),
            score_threshold=int(os.getenv("JOBRADAR_SCORE_THRESHOLD", "70")),
            max_push_per_cycle=int(os.getenv("JOBRADAR_MAX_PUSH_PER_CYCLE", "3")),
            target_salary_rub=int(os.getenv("JOBRADAR_TARGET_SALARY_RUB", "70000")),
            remote_preferred=_bool("JOBRADAR_REMOTE_PREFERRED", True),
            hh_search_queries=queries,
            hh_area=os.getenv("HH_AREA", "113"),
            hh_per_page=int(os.getenv("HH_PER_PAGE", "50")),
            hh_user_agent=os.getenv(
                "HH_USER_AGENT",
                "JobRadar/0.2 (317558701+blsoo@users.noreply.github.com)",
            ),
            hh_oauth_token=os.getenv("HH_OAUTH_TOKEN", "").strip(),
            hh_resume_id=os.getenv("HH_RESUME_ID", "").strip(),
            timezone=os.getenv("JOBRADAR_TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow",
            inbox_poll_seconds=max(60, int(os.getenv("JOBRADAR_INBOX_POLL_SECONDS", "60"))),
            hh_client_id=os.getenv("HH_CLIENT_ID", "").strip(),
            hh_client_secret=os.getenv("HH_CLIENT_SECRET", "").strip(),
            hh_redirect_uri=os.getenv(
                "HH_REDIRECT_URI",
                "https://github.com/blsoo/jobradar-vacancy-intelligence",
            ).strip(),
            email_imap_host=os.getenv("EMAIL_IMAP_HOST", "").strip(),
            email_imap_port=int(os.getenv("EMAIL_IMAP_PORT", "993")),
            email_imap_username=os.getenv("EMAIL_IMAP_USERNAME", "").strip(),
            email_imap_password=os.getenv("EMAIL_IMAP_PASSWORD", "").strip(),
            email_imap_mailbox=os.getenv("EMAIL_IMAP_MAILBOX", "INBOX").strip() or "INBOX",
            email_poll_seconds=max(60, int(os.getenv("JOBRADAR_EMAIL_POLL_SECONDS", "60"))),
        )
