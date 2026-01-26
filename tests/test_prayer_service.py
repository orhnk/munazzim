from __future__ import annotations

from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch

from munazzim.config import (
    LocationSettings,
    MunazzimConfig,
    PlannerPreferences,
    PrayerOverrides,
    PrayerSchedule,
    PrayerSettings,
)
from munazzim.services.geolocation import GeoLocation
from munazzim.services.prayer import PrayerService
from munazzim.timeutils import parse_hhmm
from zoneinfo import ZoneInfo


class DummyProvider:
    name = "aladhan"

    def __init__(self, schedule: PrayerSchedule) -> None:
        self.schedule = schedule
        self.calls = 0

    def fetch(self, day, location, settings):
        self.calls += 1
        return self.schedule


class SequenceProvider:
    name = "aladhan"

    def __init__(self) -> None:
        self.calls: list[date] = []

    def fetch(self, day, location, settings):
        self.calls.append(day)
        base_minutes = max(0, (day - date(2024, 1, 1)).days)
        minute_str = f"{base_minutes:02d}" if base_minutes < 60 else "59"
        return PrayerSchedule.from_dict({
            "fajr": f"05:{minute_str}",
            "dhuhr": "12:30",
            "asr": "15:30",
            "maghrib": "18:00",
            "isha": "20:00",
        })


class DummyCache:
    def __init__(self, cached: PrayerSchedule | None = None) -> None:
        self.cached = cached

    def get(self, provider, day, location):
        return self.cached

    def put(self, provider, day, location, schedule):
        self.cached = schedule


class MemoryCache:
    def __init__(self) -> None:
        self.entries: dict[str, PrayerSchedule] = {}

    def _key(self, provider, day, location) -> str:
        lat = location.latitude or 0.0
        lon = location.longitude or 0.0
        return f"{provider}:{day.isoformat()}:{lat:.3f}:{lon:.3f}"

    def get(self, provider, day, location):
        return self.entries.get(self._key(provider, day, location))

    def put(self, provider, day, location, schedule):
        self.entries[self._key(provider, day, location)] = schedule


class ImmediateFuture:
    def __init__(self, value) -> None:
        self._value = value

    def result(self):  # pragma: no cover - trivial container
        return self._value

    def done(self) -> bool:
        return True


class ImmediateExecutor:
    def submit(self, func, *args, **kwargs):
        return ImmediateFuture(func(*args, **kwargs))

    def shutdown(self, wait=False, cancel_futures=False):  # pragma: no cover - noop
        return None


class DummyGeoLocator:
    def __init__(self, location: GeoLocation) -> None:
        self.location = location
        self.calls = 0

    def detect(self):
        self.calls += 1
        return self.location


class DummyConfigManager:
    def __init__(self) -> None:
        self.saved = False

    def save(self, config):  # pragma: no cover - simple state change
        self.saved = True


def _base_config() -> MunazzimConfig:
    return MunazzimConfig(
        location=LocationSettings(),
        prayers=PrayerSchedule.from_dict({
            "fajr": "05:00",
            "dhuhr": "12:30",
            "asr": "15:30",
            "maghrib": "18:00",
            "isha": "20:00",
        }),
        planner=PlannerPreferences(),
        prayer_settings=PrayerSettings(),
        prayer_overrides=PrayerOverrides(),
    )


class PrayerServiceTests(unittest.TestCase):
    def setUp(self) -> None:  # pragma: no cover - unittest hook
        self.maxDiff = None

    def _service(self, config: MunazzimConfig, cache: DummyCache | None = None, locator: DummyGeoLocator | None = None,
                 manager: DummyConfigManager | None = None) -> PrayerService:
        service = PrayerService(config, config_manager=manager)
        if cache is not None:
            service.cache = cache
        if locator is not None:
            service.geolocator = locator
        return service

    def test_prayer_service_applies_overrides(self) -> None:
        config = _base_config()
        config.prayer_settings.provider = "aladhan"
        overrides = PrayerOverrides(dhuhr=parse_hhmm("13:05"), maghrib=parse_hhmm("18:30"))
        config.prayer_overrides = overrides
        schedule = PrayerSchedule.from_dict({
            "fajr": "05:10",
            "dhuhr": "12:45",
            "asr": "15:45",
            "maghrib": "18:15",
            "isha": "20:05",
        })
        provider = DummyProvider(schedule)
        detected = GeoLocation(latitude=30.0, longitude=31.0, city="Cairo", country="Egypt", timezone="Africa/Cairo")
        service = self._service(config, cache=DummyCache(), locator=DummyGeoLocator(detected))
        service.providers[config.prayer_settings.provider] = provider

        result = service.get_schedule(date(2024, 1, 1))

        self.assertEqual(result.dhuhr, overrides.dhuhr)
        self.assertEqual(result.maghrib, overrides.maghrib)
        self.assertEqual(result.fajr, schedule.fajr)
        self.assertEqual(provider.calls, 1)

    def test_prayer_service_applies_relative_overrides(self) -> None:
        config = _base_config()
        config.prayer_settings.provider = "aladhan"
        overrides = PrayerOverrides.from_dict({
            "fajr": "sunrise - 25",
            "dhuhr": "dhuhr - 2",
        })
        config.prayer_overrides = overrides
        schedule = PrayerSchedule.from_dict({
            "fajr": "05:10",
            "sunrise": "06:00",
            "dhuhr": "12:45",
            "asr": "15:45",
            "maghrib": "18:15",
            "isha": "20:05",
        })
        provider = DummyProvider(schedule)
        detected = GeoLocation(latitude=30.0, longitude=31.0, city="Cairo", country="Egypt", timezone="Africa/Cairo")
        service = self._service(config, cache=DummyCache(), locator=DummyGeoLocator(detected))
        service.providers[config.prayer_settings.provider] = provider

        result = service.get_schedule(date(2024, 1, 1))
        # sunrise 06:00 - 25 = 05:35
        self.assertEqual(result.fajr, parse_hhmm("05:35"))
        # dhuhr 12:45 - 2 = 12:43
        self.assertEqual(result.dhuhr, parse_hhmm("12:43"))
        self.assertEqual(provider.calls, 1)

    def test_prayer_service_fetches_provider_when_cache_missing_base(self) -> None:
        config = _base_config()
        config.prayer_settings.provider = "aladhan"
        overrides = PrayerOverrides.from_dict({
            "fajr": "sunrise - 25",
        })
        config.prayer_overrides = overrides
        # Create cached schedule lacking sunrise
        cached_schedule = PrayerSchedule.from_dict({
            "fajr": "05:10",
            "dhuhr": "12:45",
            "asr": "15:45",
            "maghrib": "18:15",
            "isha": "20:05",
        })
        provider_schedule = PrayerSchedule.from_dict({
            "fajr": "05:10",
            "sunrise": "06:00",
            "dhuhr": "12:45",
            "asr": "15:45",
            "maghrib": "18:15",
            "isha": "20:05",
        })
        provider = DummyProvider(provider_schedule)
        detected = GeoLocation(latitude=30.0, longitude=31.0, city="Cairo", country="Egypt", timezone="Africa/Cairo")
        # Cache contains entry without sunrise; service should fetch provider instead
        service = self._service(config, cache=DummyCache(cached_schedule), locator=DummyGeoLocator(detected))
        service.providers[config.prayer_settings.provider] = provider

        result = service.get_schedule(date(2024, 1, 1))
        # sunrise 06:00 - 25 = 05:35
        self.assertEqual(result.fajr, parse_hhmm("05:35"))
        self.assertEqual(provider.calls, 1)

    def test_auto_detection_updates_config_without_saving(self) -> None:
        config = _base_config()
        config.prayer_settings.provider = "aladhan"
        config.location.city = ""
        config.location.country = ""
        config.location.use_geolocation = True
        cached_schedule = PrayerSchedule.from_dict({
            "fajr": "05:10",
            "dhuhr": "12:45",
            "asr": "15:45",
            "maghrib": "18:15",
            "isha": "20:05",
        })
        detected = GeoLocation(latitude=51.5, longitude=-0.12, city="London", country="UK", timezone="Europe/London")
        dummy_config_manager = DummyConfigManager()
        service = self._service(
            config,
            cache=DummyCache(cached_schedule),
            locator=DummyGeoLocator(detected),
            manager=dummy_config_manager,
        )

        result = service.get_schedule(date(2024, 2, 2))

        self.assertEqual(result, cached_schedule)
        self.assertEqual(config.location.city, "London")
        self.assertEqual(config.location.country, "UK")
        self.assertEqual(config.location.latitude, detected.latitude)
        self.assertEqual(config.location.longitude, detected.longitude)
        self.assertEqual(config.location.timezone, detected.timezone)
        self.assertFalse(dummy_config_manager.saved)

    def test_pyislam_provider_uses_dependency(self) -> None:
        config = _base_config()
        config.prayer_settings.provider = "praytimes"
        config.location.latitude = 30.0444
        config.location.longitude = 31.2357
        config.location.timezone = "Africa/Cairo"
        config.prayer_settings.calculation_method = "ISNA"
        config.prayer_settings.madhab = "Hanafi"
        service = self._service(config, cache=DummyCache())
        created: list[dict[str, object]] = []

        class DummyPrayer:
            def __init__(self, conf, dt):
                created.append({
                    "conf": conf,
                    "date": dt,
                })

            def fajr_time(self):
                return parse_hhmm("04:30")

            def dohr_time(self):
                return parse_hhmm("12:05")

            def asr_time(self):
                return parse_hhmm("15:31")

            def maghreb_time(self):
                return parse_hhmm("18:45")

            def ishaa_time(self):
                return parse_hhmm("20:05")

        with patch("munazzim.services.prayer.PyIslamPrayer", DummyPrayer):
            schedule = service.get_schedule(date(2024, 6, 1))

        self.assertEqual(schedule.fajr.strftime("%H:%M"), "04:30")
        self.assertEqual(schedule.asr.strftime("%H:%M"), "15:31")
        created_conf = created[0]["conf"]
        self.assertEqual(created_conf.asr_madhab, 2)
        tz = ZoneInfo("Africa/Cairo")
        expected_offset = datetime(2024, 6, 1, tzinfo=tz).utcoffset().total_seconds() / 3600
        self.assertAlmostEqual(created_conf.timezone, expected_offset, places=3)

    def test_prefetches_future_days_when_online(self) -> None:
        config = _base_config()
        config.prayer_settings.provider = "aladhan"
        config.prayer_settings.cache_days = 3
        locator = DummyGeoLocator(GeoLocation(latitude=40.0, longitude=29.0, city="Bursa", country="Türkiye", timezone="Europe/Istanbul"))
        cache = MemoryCache()
        provider = SequenceProvider()
        service = PrayerService(
            config,
            geolocator=locator,
            cache=cache,  # type: ignore[arg-type]
            prefetch_executor=ImmediateExecutor(),
        )
        service.providers[config.prayer_settings.provider] = provider

        requested_day = date(2024, 1, 1)
        service.get_schedule(requested_day)
        future_day = requested_day + timedelta(days=1)
        cached_future = cache.get("aladhan", future_day, locator.location)

        self.assertIsNotNone(cached_future)
        self.assertGreaterEqual(len(provider.calls), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()