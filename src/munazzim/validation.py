from __future__ import annotations

from datetime import date, datetime, time, timedelta
import re
from typing import Sequence

from .config import PrayerSchedule
from .models import DayTemplate, FixedEvent, PrayerBoundEvent, PrayerEvent
from .timeutils import format_duration


class TemplateValidationError(ValueError):
    def __init__(self, issues: Sequence[str]) -> None:
        self.issues = list(issues)
        message = "\n".join(self.issues)
        super().__init__(message)


class TemplateValidator:
    MIN_WAKE_BUFFER = timedelta(minutes=20)
    _PRAYER_OFFSET_TOKEN = re.compile(r"^(?P<prayer>[A-Za-z]+)(?P<sign>[+-])(?P<minutes>\d{1,3})$")

    @classmethod
    def validate(cls, template: DayTemplate, prayers: PrayerSchedule) -> list[str]:
        issues: list[str] = []
        warnings: list[str] = []
        cls._validate_wake_time(template, prayers, issues)
        cls._validate_prayer_bounds(template, prayers, issues)
        cls._validate_fixed_events(template, issues)
        cls._validate_total_duration(template, prayers, issues)
        cls._warn_relative_ranges(template, prayers, warnings)
        if issues:
            raise TemplateValidationError(issues)
        return warnings

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
    def _validate_total_duration(
        cls,
        template: DayTemplate,
        prayers: PrayerSchedule,
        issues: list[str],
    ) -> None:
        total = timedelta()
        overage_event: str | None = None
        overage_amount: timedelta | None = None
        anchor_day = date.today()
        cursor = datetime.combine(anchor_day, template.start_time)
        for event in template.events:
            if isinstance(event, PrayerBoundEvent):
                if event.end_ref is not None:
                    start_dt = cls._resolve_ref(event.start_ref, prayers)
                    if start_dt is None:
                        start_dt = cursor
                    end_dt = cls._resolve_ref(event.end_ref, prayers)
                    if end_dt is None:
                        continue
                    if end_dt <= start_dt:
                        end_dt = end_dt + timedelta(days=1)
                    duration = end_dt - start_dt
                    if duration <= timedelta():
                        issues.append(f"Event '{event.name}' must have a positive duration.")
                    total += duration
                    if event.start_ref is not None:
                        cursor = start_dt
                    cursor = end_dt
                else:
                    duration = event.duration
                    if duration <= timedelta():
                        issues.append(f"Event '{event.name}' must have a positive duration.")
                    total += duration
                    cursor = cursor + duration
                if overage_event is None and total > timedelta(hours=24):
                    overage_event = event.name
                    overage_amount = total - timedelta(hours=24)
                continue
            if event.duration <= timedelta():
                issues.append(f"Event '{event.name}' must have a positive duration.")
            if isinstance(event, FixedEvent):
                start_dt = datetime.combine(anchor_day, event.anchor)
            elif isinstance(event, PrayerEvent):
                if event.anchor is not None:
                    start_dt = datetime.combine(anchor_day, event.anchor)
                else:
                    resolved = cls._resolve_ref(event.prayer, prayers)
                    start_dt = resolved if resolved is not None else cursor
            else:
                start_dt = cursor
            total += event.duration
            cursor = start_dt + event.duration
            if overage_event is None and total > timedelta(hours=24):
                overage_event = event.name
                overage_amount = total - timedelta(hours=24)
        if total > timedelta(hours=24):
            if overage_event:
                overage_text = format_duration(overage_amount or timedelta())
                total_text = format_duration(total)
                issues.append(
                    f"Template exceeds 24 hours of planned time. Total planned time is {total_text}. "
                    f"'{overage_event}' pushes it over by {overage_text}."
                )
            else:
                issues.append("Template exceeds 24 hours of planned time.")

    @classmethod
    def _validate_prayer_bounds(
        cls,
        template: DayTemplate,
        prayers: PrayerSchedule,
        issues: list[str],
    ) -> None:
        prayer_order = ["fajr", "dhuhr", "asr", "maghrib", "isha"]
        prayer_times = {
            "fajr": prayers.fajr,
            "dhuhr": prayers.dhuhr,
            "duhr": prayers.dhuhr,
            "asr": prayers.asr,
            "maghrib": prayers.maghrib,
            "isha": prayers.isha,
        }
        for event in template.events:
            if isinstance(event, PrayerEvent) and event.anchor is not None:
                key = event.prayer.strip().lower()
                key = "dhuhr" if key == "duhr" else key
                base = prayer_times.get(key)
                if base is None:
                    continue
                try:
                    idx = prayer_order.index("dhuhr" if key == "duhr" else key)
                except ValueError:
                    idx = -1
                next_time = None
                if idx != -1 and idx + 1 < len(prayer_order):
                    next_time = prayer_times.get(prayer_order[idx + 1])
                anchor_dt = datetime.combine(date.today(), event.anchor)
                base_dt = datetime.combine(date.today(), base)
                if anchor_dt < base_dt:
                    issues.append(
                        f"Prayer '{event.prayer}' is scheduled before its calculated start time."
                    )
                if next_time:
                    next_dt = datetime.combine(date.today(), next_time)
                    if anchor_dt >= next_dt:
                        issues.append(
                            f"Prayer '{event.prayer}' must be before the next prayer time."
                        )
                    if event.duration and anchor_dt + event.duration > next_dt:
                        issues.append(
                            f"Prayer '{event.prayer}' exceeds the next prayer window."
                        )
            if isinstance(event, PrayerBoundEvent) and event.end_ref is not None:
                duration = cls._resolve_prayer_bound_duration(event, prayers, template.start_time)
                if duration is not None and duration <= timedelta():
                    issues.append(
                        f"Event '{event.name}' has an invalid prayer-bound range."
                    )

    @classmethod
    def _resolve_prayer_bound_duration(
        cls,
        event: PrayerBoundEvent,
        prayers: PrayerSchedule,
        fallback_start: time,
    ) -> timedelta | None:
        start_dt = cls._resolve_ref(event.start_ref, prayers)
        if start_dt is None:
            start_dt = datetime.combine(date.today(), fallback_start)
        end_dt = cls._resolve_ref(event.end_ref, prayers) if event.end_ref is not None else None
        if end_dt is None:
            if event.duration <= timedelta():
                return None
            end_dt = start_dt + event.duration
        if end_dt <= start_dt:
            end_dt = end_dt + timedelta(days=1)
        return end_dt - start_dt

    @classmethod
    def _resolve_ref(
        cls,
        value: time | str | None,
        prayers: PrayerSchedule,
    ) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, time):
            return datetime.combine(date.today(), value)
        raw = value.strip()
        offset_match = cls._PRAYER_OFFSET_TOKEN.match(raw)
        if offset_match:
            key = offset_match.group("prayer").strip().lower()
            sign = offset_match.group("sign")
            minutes = int(offset_match.group("minutes"))
            if key == "duhr":
                key = "dhuhr"
            base = getattr(prayers, key, None)
            if base is None:
                return None
            base_dt = datetime.combine(date.today(), base)
            delta = timedelta(minutes=minutes)
            if sign == "-":
                delta = -delta
            return base_dt + delta
        key = raw.lower()
        if key == "duhr":
            key = "dhuhr"
        base = getattr(prayers, key, None)
        if base is None:
            return None
        return datetime.combine(date.today(), base)

    @classmethod
    def _warn_relative_ranges(
        cls,
        template: DayTemplate,
        prayers: PrayerSchedule,
        warnings: list[str],
    ) -> None:
        anchor_day = date.today()
        cursor = datetime.combine(anchor_day, template.start_time)
        fixed_refs: list[tuple[str, datetime]] = []

        for event in template.events:
            if isinstance(event, FixedEvent):
                fixed_refs.append((event.name, datetime.combine(anchor_day, event.anchor)))
            if isinstance(event, PrayerEvent):
                if event.anchor is not None:
                    fixed_refs.append((event.name, datetime.combine(anchor_day, event.anchor)))
                else:
                    resolved = cls._resolve_ref(event.prayer, prayers)
                    if resolved is not None:
                        fixed_refs.append((event.name, resolved))

        for event in template.events:
            if isinstance(event, PrayerBoundEvent) and event.end_ref is not None:
                start_dt = cls._resolve_ref(event.start_ref, prayers)
                if start_dt is None:
                    start_dt = cursor
                end_dt = cls._resolve_ref(event.end_ref, prayers)
                if end_dt is None:
                    continue
                if end_dt <= start_dt:
                    warnings.append(
                        f"Event '{event.name}' spans midnight in its '..' range; review for overlaps."
                    )
                    end_dt = end_dt + timedelta(days=1)
                for other_name, other_dt in fixed_refs:
                    if other_dt >= start_dt and other_dt < end_dt:
                        warnings.append(
                            f"Event '{event.name}' overlaps with '{other_name}' due to its '..' range."
                        )
                        break
                if event.start_ref is not None:
                    cursor = start_dt
                cursor = end_dt
                continue

            if isinstance(event, FixedEvent):
                cursor = datetime.combine(anchor_day, event.anchor) + event.duration
                continue

            if isinstance(event, PrayerEvent):
                anchor = event.anchor
                if anchor is None:
                    resolved = cls._resolve_ref(event.prayer, prayers)
                    if resolved is not None:
                        cursor = resolved + event.duration
                        continue
                if anchor is not None:
                    cursor = datetime.combine(anchor_day, anchor) + event.duration
                else:
                    cursor = cursor + event.duration
                continue

            cursor = cursor + event.duration
