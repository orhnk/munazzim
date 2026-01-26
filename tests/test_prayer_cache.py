from __future__ import annotations

from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from munazzim.config import PrayerSchedule
from munazzim.services.geolocation import GeoLocation
from munazzim.services.prayer import PrayerCache


class PrayerCacheTests(unittest.TestCase):
    def _location(self) -> GeoLocation:
        return GeoLocation(latitude=30.0, longitude=31.0, city="Cairo", country="Egypt", timezone="Africa/Cairo")

    def _schedule(self, fajr: str) -> PrayerSchedule:
        return PrayerSchedule.from_dict({
            "fajr": fajr,
            "dhuhr": "12:30",
            "asr": "15:30",
            "maghrib": "18:00",
            "isha": "20:00",
        })

    def test_put_and_get_same_day(self) -> None:
        with TemporaryDirectory() as tmp:
            cache = PrayerCache(Path(tmp) / "cache.json", max_days=2)
            schedule = self._schedule("05:00")
            location = self._location()

            cache.put("aladhan", date(2024, 1, 1), location, schedule)
            cached = cache.get("aladhan", date(2024, 1, 1), location)

            self.assertIsNotNone(cached)
            assert cached is not None  # help type checkers
            self.assertEqual(cached.fajr, schedule.fajr)
            cache.wait_for_io()
            cache.close()

    def test_prunes_entries_beyond_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            cache = PrayerCache(Path(tmp) / "cache.json", max_days=1)
            location = self._location()

            cache.put("aladhan", date(2024, 1, 1), location, self._schedule("05:00"))
            cache.put("aladhan", date(2024, 1, 2), location, self._schedule("05:10"))

            old_entry = cache.get("aladhan", date(2024, 1, 1), location)
            new_entry = cache.get("aladhan", date(2024, 1, 2), location)

            self.assertIsNone(old_entry)
            self.assertIsNotNone(new_entry)
            cache.wait_for_io()
            cache.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
