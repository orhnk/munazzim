from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time as time_type, timedelta
import re

from .config import MunazzimConfig, PrayerSchedule, PrayerDurations
from typing import Any
from .models import DayPlan, DayTemplate, Event, FixedEvent, PrayerBoundEvent, PrayerEvent, ScheduledEvent
from .timeutils import TimeCursor


@dataclass(slots=True)
class PrayerSlot:
    label: str
    start: datetime
    duration: timedelta


class Scheduler:
    def __init__(self, config: MunazzimConfig, prayer_service: Any | None = None) -> None:
        self.config = config
        # prayer_service should be an instance that exposes get_schedule(date) -> PrayerSchedule
        self.prayer_service = prayer_service

    def build_plan(
        self,
        template: DayTemplate,
        *,
        plan_date: date | None = None,
        prayer_schedule: PrayerSchedule | None = None,
    ) -> DayPlan:
        cursor = TimeCursor.from_start(template.start_time or self.config.planner.day_start, plan_date)
        plan = DayPlan(template_name=template.name, generated_for=cursor.anchor_date)
        schedule = prayer_schedule
        # If no prayer_schedule provided, prefer the dynamic schedule from prayer_service
        if schedule is None and self.prayer_service is not None and plan_date is not None:
            try:
                schedule = self.prayer_service.get_schedule(plan_date)
            except Exception:
                schedule = None
        if schedule is None:
            schedule = self.config.prayers
        durations = getattr(self.config.prayer_settings, "durations", None)
        if durations is None:  # pragma: no cover - legacy configs
            durations = PrayerDurations()
        prayer_slots = self._prayer_slots(cursor.anchor_date, schedule, durations)
        prayer_index = 0

        prayer_time_map = {
            "fajr": schedule.fajr,
            "dhuhr": schedule.dhuhr,
            "duhr": schedule.dhuhr,
            "asr": schedule.asr,
            "maghrib": schedule.maghrib,
            "isha": schedule.isha,
        }
        prayer_offset_token = re.compile(r"^(?P<prayer>[A-Za-z]+)(?P<sign>[+-])(?P<minutes>\d{1,3})$")

        def resolve_ref(value: time_type | str | None) -> datetime | None:
            if value is None:
                return None
            if isinstance(value, time_type):
                return datetime.combine(cursor.anchor_date, value)
            raw = value.strip()
            offset_match = prayer_offset_token.match(raw)
            if offset_match:
                key = offset_match.group("prayer").strip().lower()
                sign = offset_match.group("sign")
                minutes = int(offset_match.group("minutes"))
                moment = prayer_time_map.get("dhuhr" if key == "duhr" else key)
                if moment is None:
                    return None
                base_dt = datetime.combine(cursor.anchor_date, moment)
                delta = timedelta(minutes=minutes)
                if sign == "-":
                    delta = -delta
                return base_dt + delta
            key = raw.lower()
            moment = prayer_time_map.get(key)
            if moment is None:
                return None
            return datetime.combine(cursor.anchor_date, moment)

        def peek_prayer() -> PrayerSlot | None:
            return prayer_slots[prayer_index] if prayer_index < len(prayer_slots) else None

        def pop_prayer() -> PrayerSlot | None:
            nonlocal prayer_index
            if prayer_index >= len(prayer_slots):
                return None
            slot = prayer_slots[prayer_index]
            prayer_index += 1
            return slot

        def schedule_prayer(slot: PrayerSlot) -> None:
            event = PrayerEvent(
                name=f"{slot.label} Prayer",
                prayer=slot.label,
                duration=slot.duration,
                flexible=False,
            )
            cursor.position = slot.start
            start, end = cursor.advance(event.duration)
            plan.add(ScheduledEvent(event=event, start=start, end=end))

        def flush_prayers(before: datetime) -> None:
            while True:
                slot = peek_prayer()
                if not slot or slot.start > before:
                    break
                schedule_prayer(pop_prayer())

        def push_prayers_after(boundary: datetime) -> None:
            for idx in range(prayer_index, len(prayer_slots)):
                slot = prayer_slots[idx]
                if slot.start < boundary:
                    slot.start = boundary
                else:
                    break

        for event in template.events:
            flush_prayers(cursor.position)
            self._schedule_event(
                event,
                cursor,
                plan,
                peek_prayer,
                pop_prayer,
                schedule_prayer,
                push_prayers_after,
                resolve_ref,
            )

        # add any remaining prayers at the end
        while True:
            slot = pop_prayer()
            if not slot:
                break
            schedule_prayer(slot)

        return plan

    def _schedule_event(
        self,
        event: Event,
        cursor: TimeCursor,
        plan: DayPlan,
        peek_prayer,
        pop_prayer,
        schedule_prayer,
        push_prayers_after,
        resolve_ref,
    ) -> None:
        if isinstance(event, PrayerBoundEvent):
            start_dt = resolve_ref(event.start_ref) if event.start_ref is not None else cursor.position
            end_dt = resolve_ref(event.end_ref) if event.end_ref is not None else None
            if start_dt is None:
                return
            if end_dt is None:
                if event.duration <= timedelta(0):
                    return
                end_dt = start_dt + event.duration
            if end_dt <= start_dt:
                end_dt = end_dt + timedelta(days=1)
            if event.start_ref is not None:
                cursor.position = start_dt
            start, end = cursor.advance(end_dt - start_dt)
            plan.add(
                ScheduledEvent(
                    event=replace(event, duration=end - start),
                    start=start,
                    end=end,
                )
            )
            push_prayers_after(end)
            return
        # If this is a dedicated prayer placeholder from a template, schedule
        # the matching prayer slot instead of treating it as a normal event.
        if isinstance(event, PrayerEvent):
            # Find the next prayer slot matching the prayer name and schedule it.
            target_label = event.prayer.strip().lower()
            # Schedule any prayers that occur before the next matching one
            # If the prayer was already scheduled (e.g., flush_prayers queued it
            # earlier), find it in 'plan' and update its duration/anchor per
            # the placeholder. This covers the case where a relative placeholder
            # appears after the slot was already scheduled.
            for idx in range(len(plan.items) - 1, -1, -1):
                scheduled_event = plan.items[idx]
                if isinstance(scheduled_event.event, PrayerEvent) and scheduled_event.event.prayer.strip().lower() == target_label:
                    # Update duration and anchor if provided and return
                    if getattr(event, "anchor", None) is not None:
                        scheduled_event.start = datetime.combine(cursor.anchor_date, event.anchor)
                    if event.duration and event.duration > timedelta(0):
                        scheduled_event.event.duration = event.duration
                        scheduled_event.end = scheduled_event.start + event.duration
                    return

            while True:
                next_slot = peek_prayer()
                if not next_slot:
                    # Nothing left to schedule
                    return
                # If the next slot is earlier than the requested prayer, schedule it now.
                if next_slot.label.strip().lower() != target_label:
                    schedule_prayer(pop_prayer())
                    continue
                # Found a slot matching the request
                slot = pop_prayer()
                # If event has an anchor (fixed prayer override), prefer that and
                # set the scheduled slot's start accordingly (this will update
                # the cursor after scheduling when we call schedule_prayer).
                if getattr(event, "anchor", None) is not None:
                    # Adjust slot start to anchor (date combined)
                    slot.start = datetime.combine(cursor.anchor_date, event.anchor)
                if event.duration and event.duration > timedelta(0):
                    slot.duration = event.duration
                schedule_prayer(slot)
                # If a matching prayer was scheduled already earlier (e.g., via flush_prayers),
                # we need to update that scheduled event's duration and end time.
                # Search for the most recent matching scheduled prayer in the plan and override.
                for idx in range(len(plan.items) - 1, -1, -1):
                    scheduled_event = plan.items[idx]
                    if isinstance(scheduled_event.event, PrayerEvent) and scheduled_event.event.prayer.strip().lower() == target_label:
                        # Update duration/end if override specified
                        if event.duration and event.duration > timedelta(0):
                            old_start = scheduled_event.start
                            scheduled_event.event.duration = event.duration
                            scheduled_event.end = old_start + event.duration
                        break
                return
            # Should not reach here

        if isinstance(event, FixedEvent):
            cursor.jump_to(event.anchor)
            start, end = cursor.advance(event.duration)
            plan.add(
                ScheduledEvent(
                    event=replace(event, duration=end - start),
                    start=start,
                    end=end,
                )
            )
            push_prayers_after(end)
            return

        remaining = event.duration
        zero = timedelta()
        while remaining > zero:
            next_slot = peek_prayer()
            if next_slot and next_slot.start < cursor.position:
                schedule_prayer(pop_prayer())
                continue

            if next_slot:
                time_until_prayer = next_slot.start - cursor.position
                if time_until_prayer <= zero:
                    schedule_prayer(pop_prayer())
                    continue
                chunk = min(remaining, time_until_prayer)
            else:
                time_until_prayer = None
                chunk = remaining

            start, end = cursor.advance(chunk)
            plan.add(
                ScheduledEvent(
                    event=replace(event, duration=chunk),
                    start=start,
                    end=end,
                )
            )
            remaining -= chunk

            if time_until_prayer is not None and chunk == time_until_prayer:
                schedule_prayer(pop_prayer())

    def _prayer_slots(
        self,
        day: date,
        schedule: PrayerSchedule,
        durations,
    ) -> list[PrayerSlot]:
        prayers: list[tuple[str, time_type, timedelta]] = [
            ("Fajr", schedule.fajr, durations.fajr),
            ("Dhuhr", schedule.dhuhr, durations.dhuhr),
            ("Asr", schedule.asr, durations.asr),
            ("Maghrib", schedule.maghrib, durations.maghrib),
            ("Isha", schedule.isha, durations.isha),
        ]
        return [
            PrayerSlot(label=label, start=datetime.combine(day, moment), duration=duration)
            for label, moment, duration in prayers
        ]
