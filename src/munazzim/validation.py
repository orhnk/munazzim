from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Sequence

from .config import PrayerSchedule
from .models import DayTemplate, FixedEvent


class TemplateValidationError(ValueError):
    def __init__(self, issues: Sequence[str]) -> None:
        self.issues = list(issues)
        message = "\n".join(self.issues)
        super().__init__(message)


class TemplateValidator:
    MIN_WAKE_BUFFER = timedelta(minutes=20)

    @classmethod
    def validate(cls, template: DayTemplate, prayers: PrayerSchedule) -> None:
        issues: list[str] = []
        cls._validate_wake_time(template, prayers, issues)
        cls._validate_fixed_events(template, issues)
        cls._validate_total_duration(template, issues)
        if issues:
            raise TemplateValidationError(issues)

    @classmethod
    def _validate_wake_time(
        cls,
        template: DayTemplate,
        prayers: PrayerSchedule,
        issues: list[str],
    ) -> None:
        anchor_day = date.today()
        wake = datetime.combine(anchor_day, template.start_time)
        fajr = datetime.combine(anchor_day, prayers.fajr)
        if wake > fajr - cls.MIN_WAKE_BUFFER:
            issues.append(
                "Wake-up time must be at least 20 minutes before Fajr. Adjust your template's start_time."
            )

    @classmethod
    def _validate_fixed_events(cls, template: DayTemplate, issues: list[str]) -> None:
        anchor_day = date.today()
        fixed_events = sorted(
            (event for event in template.events if isinstance(event, FixedEvent)),
            key=lambda ev: ev.anchor,
        )
        last_end: datetime | None = None
        for event in fixed_events:
            start = datetime.combine(anchor_day, event.anchor)
            end = start + event.duration
            if last_end and start < last_end:
                issues.append(
                    f"Fixed event '{event.name}' overlaps with a previous Thabbat event."
                )
            last_end = max(last_end, end) if last_end else end

    @classmethod
    def _validate_total_duration(cls, template: DayTemplate, issues: list[str]) -> None:
        total = timedelta()
        for event in template.events:
            if event.duration <= timedelta():
                issues.append(f"Event '{event.name}' must have a positive duration.")
            total += event.duration
        if total > timedelta(hours=24):
            issues.append("Template exceeds 24 hours of planned time.")
