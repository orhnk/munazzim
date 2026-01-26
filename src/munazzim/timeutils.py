from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


def parse_hhmm(value: str) -> time:
    value = value.strip()
    parts: list[str]
    if ":" in value:
        parts = value.split(":", 1)
    elif "." in value:
        parts = value.split(".", 1)
    else:
        raise ValueError(f"Unsupported time format: {value}")
    hour = int(parts[0])
    minute = int(parts[1])
    if not (0 <= hour < 24 and 0 <= minute < 60):  # pragma: no cover - guard rail
        raise ValueError(f"Invalid time value: {value}")
    return time(hour=hour, minute=minute)


def parse_duration(value: str) -> timedelta:
    cleaned = value.strip().lower()
    if cleaned.endswith("m"):
        return timedelta(minutes=float(cleaned[:-1]))
    if cleaned.endswith("h"):
        return timedelta(hours=float(cleaned[:-1]))
    if ":" in cleaned:
        hours, minutes = cleaned.split(":", 1)
        return timedelta(hours=int(hours), minutes=int(minutes))
    if "." in cleaned:
        hours, minutes = cleaned.split(".", 1)
        return timedelta(hours=int(hours or 0), minutes=int(minutes or 0))
    if cleaned.isdigit():
        return timedelta(minutes=int(cleaned))
    raise ValueError(f"Unsupported duration format: {value}")


def format_hhmm(value: time) -> str:
    return value.strftime("%H:%M")


def format_duration(value: timedelta) -> str:
    total_minutes = int(value.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}"


@dataclass
class TimeCursor:
    """Utility to walk through a day timeline."""

    anchor_date: date
    position: datetime

    @classmethod
    def from_start(cls, start: time, day: date | None = None) -> "TimeCursor":
        base_date = day or date.today()
        return cls(anchor_date=base_date, position=datetime.combine(base_date, start))

    def advance(self, duration: timedelta) -> tuple[datetime, datetime]:
        start = self.position
        self.position += duration
        return start, self.position

    def jump_to(self, moment: time) -> None:
        self.position = datetime.combine(self.anchor_date, moment)

    @property
    def current_time(self) -> time:
        return self.position.time()
