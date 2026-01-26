from datetime import date, time, timedelta
import unittest

from munazzim.config import MunazzimConfig, PrayerDurations, PrayerSchedule
from munazzim.models import DayTemplate, Event, FixedEvent, PrayerEvent
from munazzim.scheduler import Scheduler


class SchedulerPrayerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = MunazzimConfig.default()
        self.config.prayers = PrayerSchedule.from_dict(
            {
                "fajr": "05:00",
                "dhuhr": "09:30",
                "asr": "15:30",
                "maghrib": "19:00",
                "isha": "21:00",
            }
        )
        self.config.prayer_settings.durations = PrayerDurations(
            fajr=timedelta(minutes=10),
            dhuhr=timedelta(minutes=10),
            asr=timedelta(minutes=10),
            maghrib=timedelta(minutes=10),
            isha=timedelta(minutes=10),
        )
        self.scheduler = Scheduler(self.config)

    def test_prayer_scheduled_after_fixed_event(self) -> None:
        template = DayTemplate(
            name="Thabbat",
            start_time=time(8, 0),
            description="",
            events=[
                Event(name="Prep", duration=timedelta(hours=1)),
                FixedEvent(name="Lecture", duration=timedelta(hours=1), anchor=time(9, 0)),
                Event(name="Study", duration=timedelta(hours=1)),
            ],
        )
        plan = self.scheduler.build_plan(template, plan_date=date(2025, 1, 1), prayer_schedule=self.config.prayers)
        dhuhr_events = [item for item in plan.items if isinstance(item.event, PrayerEvent) and item.event.prayer == "Dhuhr"]
        self.assertEqual(len(dhuhr_events), 1)
        dhuhr_event = dhuhr_events[0]
        self.assertEqual(dhuhr_event.start.time(), time(10, 0))
        self.assertEqual(dhuhr_event.end.time(), time(10, 10))

    def test_scheduler_uses_prayer_service_when_no_schedule_parameter(self) -> None:
        class DummyPrayerService:
            def __init__(self, schedule):
                self.schedule = schedule

            def get_schedule(self, d):
                return self.schedule

        schedule = PrayerSchedule.from_dict({
            "fajr": "05:10",
            "dhuhr": "13:05",
            "asr": "15:45",
            "maghrib": "18:15",
            "isha": "20:05",
        })
        fake_service = DummyPrayerService(schedule)
        scheduler = Scheduler(self.config, prayer_service=fake_service)
        template = DayTemplate(
            name="Test",
            start_time=time(8, 0),
            description="",
            events=[Event(name="Prep", duration=timedelta(hours=1))],
        )
        plan = scheduler.build_plan(template, plan_date=date(2025, 1, 1))
        dhuhr_events = [item for item in plan.items if isinstance(item.event, PrayerEvent) and item.event.prayer == "Dhuhr"]
        self.assertEqual(len(dhuhr_events), 1)
        self.assertEqual(dhuhr_events[0].start.time(), schedule.dhuhr)

    def test_template_prayer_placeholder_resolves_to_prayer_slot(self) -> None:
        from munazzim.models import PrayerEvent

        # Template includes a prayer placeholder explicitly
        template = DayTemplate(
            name="PrayerTemplate",
            start_time=time(4, 30),
            description="",
            events=[PrayerEvent(name="Fajr Prayer", prayer="Fajr", duration=timedelta(minutes=15))],
        )
        plan = self.scheduler.build_plan(template, plan_date=date(2025, 1, 1), prayer_schedule=self.config.prayers)
        fajr_events = [item for item in plan.items if isinstance(item.event, PrayerEvent) and item.event.prayer == "Fajr"]
        self.assertEqual(len(fajr_events), 1)
        self.assertEqual(fajr_events[0].start.time(), time(5, 0))
        self.assertEqual(fajr_events[0].end - fajr_events[0].start, timedelta(minutes=15))

    def test_template_prayer_placeholders_all_prayers(self) -> None:
        # Verify all prayer placeholders schedule into their native prayer slots
        prayers = [
            ("Fajr", time(5, 0)),
            ("Dhuhr", time(9, 30)),
            ("Asr", time(15, 30)),
            ("Maghrib", time(19, 0)),
            ("Isha", time(21, 0)),
        ]
        for p, expected in prayers:
            template = DayTemplate(
                name=f"{p}Template",
                start_time=time(4, 0),
                description="",
                events=[PrayerEvent(name=f"{p} Prayer", prayer=p, duration=timedelta(minutes=10))],
            )
            plan = self.scheduler.build_plan(template, plan_date=date(2025, 1, 1), prayer_schedule=self.config.prayers)
            darr = [item for item in plan.items if isinstance(item.event, PrayerEvent) and item.event.prayer == p]
            self.assertEqual(len(darr), 1)
            self.assertEqual(darr[0].start.time(), expected)
            self.assertEqual(darr[0].end - darr[0].start, timedelta(minutes=10))

    def test_relative_prayer_placeholder_after_flush_updates_scheduled_prayer(self) -> None:
        # This ensures a relative prayer placeholder appearing after the
        # prayer would have been scheduled by flush_prayers updates that prayer's duration
        from datetime import time
        template = DayTemplate(
            name='DelayedPrayer',
            start_time=time(4, 0),
            description='',
            events=[
                Event(name='Prep', duration=timedelta(hours=1)),  # 04:00-05:00
                Event(name='Read', duration=timedelta(hours=1)),  # 05:00-06:00
                Event(name='Fajr', duration=timedelta(minutes=20)),  # placeholder -> .20 Fajr
            ],
        )
        # But event is a relative placeholder; we simulate as PrayerEvent created by template
        # The builder logic yields Event for name 'Fajr', but our actual parsing will create PrayerEvent.
        # For this test, we directly put a PrayerEvent in the template's events to simulate
        from munazzim.models import PrayerEvent
        template.events[-1] = PrayerEvent(name='Fajr', prayer='Fajr', duration=timedelta(minutes=20))
        plan = self.scheduler.build_plan(template, plan_date=date(2025, 1, 1), prayer_schedule=self.config.prayers)
        fajr_events = [item for item in plan.items if isinstance(item.event, PrayerEvent) and item.event.prayer == 'Fajr']
        self.assertEqual(len(fajr_events), 1)
        # The scheduled prayer should have the override duration of 20 minutes
        self.assertEqual(fajr_events[0].end - fajr_events[0].start, timedelta(minutes=20))

    def test_qalib_relative_prayer_placeholder_applies_override(self) -> None:
        from datetime import time
        from munazzim.qalib import parse_qalib
        from munazzim.models import PrayerEvent

        # Build a template via Qalib where a relative prayer placeholder appears
        # after the prayer was scheduled by flush_prayers; scheduling should
        # update the previously scheduled prayer to the placeholder duration.
        raw = """
        04:00
        1 Prep
        .20 Fajr
        """
        template = parse_qalib(raw, default_name="qtest")
        plan = self.scheduler.build_plan(template, plan_date=date(2025, 1, 1), prayer_schedule=self.config.prayers)
        fajr_events = [item for item in plan.items if isinstance(item.event, PrayerEvent) and item.event.prayer == 'Fajr']
        self.assertEqual(len(fajr_events), 1)
        self.assertEqual(fajr_events[0].end - fajr_events[0].start, timedelta(minutes=20))

    def test_qalib_relative_prayer_placeholder_all_prayers(self) -> None:
        from datetime import time
        from munazzim.qalib import parse_qalib
        from munazzim.models import PrayerEvent
        prayers = [
            ('Fajr', '05:00'),
            ('Dhuhr', '09:30'),
            ('Asr', '15:30'),
            ('Maghrib', '19:00'),
            ('Isha', '21:00'),
        ]
        for p, _ in prayers:
            raw = f"""
            04:00
            1 Prep
            .15 {p}
            """
            template = parse_qalib(raw, default_name=f'q_{p}')
            plan = self.scheduler.build_plan(template, plan_date=date(2025, 1, 1), prayer_schedule=self.config.prayers)
            parr = [item for item in plan.items if isinstance(item.event, PrayerEvent) and item.event.prayer.lower() == p.lower()]
            self.assertEqual(len(parr), 1)
            self.assertEqual(parr[0].end - parr[0].start, timedelta(minutes=15))


if __name__ == "__main__":
    unittest.main()
