from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Iterable


class EventType(str, Enum):
    RELATIVE = "relative"
    FIXED = "fixed"
    PRAYER = "prayer"
    PRAYER_BOUND = "prayer_bound"


@dataclass(slots=True)
class Task:
    label: str
    note: str | None = None
    total_occurrences: int | None = None
    remaining_occurrences: int | None = None
    task_id: str | None = None
    completed_occurrences: int = 0
    last_completed: date | None = None


@dataclass(slots=True)
class Event:
    name: str
    duration: timedelta
    flexible: bool = True
    tasks: list[Task] = field(default_factory=list)
    event_type: EventType = EventType.RELATIVE


@dataclass(slots=True)
class FixedEvent(Event):
    anchor: time = field(default_factory=lambda: time(hour=0, minute=0))
    event_type: EventType = EventType.FIXED


@dataclass(slots=True)
class PrayerEvent(Event):
    prayer: str = ""
    anchor: time | None = None
    event_type: EventType = EventType.PRAYER


@dataclass(slots=True)
class PrayerBoundEvent(Event):
    start_ref: time | str | None = None
    end_ref: time | str | None = None
    event_type: EventType = EventType.PRAYER_BOUND


@dataclass(slots=True)
class DayTemplate:
    name: str
    start_time: time
    events: list[Event]
    description: str = ""
    prayer_durations: dict[str, str] = field(default_factory=dict)
    prayer_overrides: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ScheduledEvent:
    event: Event
    start: datetime
    end: datetime

    @property
    def display_name(self) -> str:
        suffix = ""
        if isinstance(self.event, PrayerEvent):
            suffix = " (Prayer)"
        elif isinstance(self.event, FixedEvent):
            suffix = " (Fixed)"
        return f"{self.event.name}{suffix}"


@dataclass(slots=True)
class DayPlan:
    template_name: str
    generated_for: date
    items: list[ScheduledEvent] = field(default_factory=list)

    def add(self, scheduled: ScheduledEvent) -> None:
        self.items.append(scheduled)

    def extend(self, scheduled_events: Iterable[ScheduledEvent]) -> None:
        self.items.extend(scheduled_events)
