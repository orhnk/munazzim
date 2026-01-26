from datetime import time, timedelta
import unittest

from munazzim.config import PrayerSchedule
from munazzim.models import DayTemplate, Event, FixedEvent
from munazzim.validation import TemplateValidationError, TemplateValidator


class TemplateValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.prayers = PrayerSchedule.from_dict(
            {
                "fajr": "05:00",
                "dhuhr": "12:30",
                "asr": "15:30",
                "maghrib": "18:00",
                "isha": "19:30",
            }
        )

    def test_requires_wake_before_fajr(self) -> None:
        template = DayTemplate(
            name="Late Start",
            start_time=time(5, 0),
            description="",
            events=[],
        )
        with self.assertRaises(TemplateValidationError):
            TemplateValidator.validate(template, self.prayers)

    def test_detects_overlapping_fixed_events(self) -> None:
        template = DayTemplate(
            name="Overlap",
            start_time=time(4, 0),
            description="",
            events=[
                FixedEvent(name="Lecture", duration=timedelta(hours=2), anchor=time(8, 0)),
                FixedEvent(name="Exam", duration=timedelta(hours=1), anchor=time(9, 0)),
            ],
        )
        with self.assertRaises(TemplateValidationError):
            TemplateValidator.validate(template, self.prayers)

    def test_valid_template_passes(self) -> None:
        template = DayTemplate(
            name="Balanced",
            start_time=time(4, 0),
            description="",
            events=[
                Event(name="Focus", duration=timedelta(hours=2)),
                FixedEvent(name="Lecture", duration=timedelta(hours=1), anchor=time(9, 0)),
            ],
        )
        TemplateValidator.validate(template, self.prayers)


if __name__ == "__main__":
    unittest.main()
