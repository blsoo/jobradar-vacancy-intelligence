from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo


MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


@dataclass(frozen=True)
class InterviewDetection:
    scheduled_at: datetime
    confidence: str
    evidence: str


def _reference(created_at: str, tz: ZoneInfo) -> datetime:
    try:
        value = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=tz)
        return value.astimezone(tz)
    except (ValueError, TypeError):
        return datetime.now(tz)


def detect_interview_datetime(text: str, created_at: str, timezone: str = "Europe/Moscow") -> InterviewDetection | None:
    if not text:
        return None
    lower = text.lower().replace("ё", "е")
    if not any(word in lower for word in ("собесед", "интервью", "встреч", "созвон", "звонок", "техническ")):
        return None

    tz = ZoneInfo(timezone)
    ref = _reference(created_at, tz)

    # 29.08.2026 15:30 / 29.08 15:30 / 29-08 в 15:30
    m = re.search(
        r"\b(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\b[^\n]{0,35}?\b(?:в\s*)?(\d{1,2})[:.](\d{2})\b",
        lower,
    )
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else ref.year
        if year < 100:
            year += 2000
        dt = datetime(year, month, day, int(m.group(4)), int(m.group(5)), tzinfo=tz)
        if not m.group(3) and dt < ref - timedelta(days=7):
            dt = dt.replace(year=dt.year + 1)
        return InterviewDetection(dt, "high", m.group(0).strip())

    # 29 августа в 15:30
    month_names = "|".join(MONTHS)
    m = re.search(
        rf"\b(\d{{1,2}})\s+({month_names})(?:\s+(\d{{4}}))?[^\n]{{0,25}}?\b(?:в\s*)?(\d{{1,2}})[:.](\d{{2}})\b",
        lower,
    )
    if m:
        year = int(m.group(3)) if m.group(3) else ref.year
        dt = datetime(year, MONTHS[m.group(2)], int(m.group(1)), int(m.group(4)), int(m.group(5)), tzinfo=tz)
        if not m.group(3) and dt < ref - timedelta(days=7):
            dt = dt.replace(year=dt.year + 1)
        return InterviewDetection(dt, "high", m.group(0).strip())

    # завтра / послезавтра в 15:30
    m = re.search(r"\b(завтра|послезавтра)\b[^\n]{0,25}?\b(?:в\s*)?(\d{1,2})[:.](\d{2})\b", lower)
    if m:
        days = 1 if m.group(1) == "завтра" else 2
        base = (ref + timedelta(days=days)).date()
        dt = datetime(base.year, base.month, base.day, int(m.group(2)), int(m.group(3)), tzinfo=tz)
        return InterviewDetection(dt, "medium", m.group(0).strip())

    return None


def reminder_times(scheduled_at: datetime) -> tuple[tuple[str, datetime], ...]:
    candidates = (
        ("day_before", scheduled_at - timedelta(hours=24)),
        ("two_hours", scheduled_at - timedelta(hours=2)),
        ("thirty_minutes", scheduled_at - timedelta(minutes=30)),
    )
    now = datetime.now(scheduled_at.tzinfo)
    return tuple((kind, when) for kind, when in candidates if when > now)
