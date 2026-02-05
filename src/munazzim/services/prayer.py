from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, Executor
import json
from pathlib import Path
from threading import Lock
from typing import Protocol
from zoneinfo import ZoneInfo
import re

import httpx  # type: ignore[import]
from pyIslam.praytimes import LIST_FAJR_ISHA_METHODS, Prayer as PyIslamPrayer, PrayerConf  # type: ignore[import]

from ..config import ConfigManager, MunazzimConfig, PrayerOverrides, PrayerSchedule, PrayerSettings
from .geolocation import GeoLocation, GeoLocator

ALADHAN_API = "https://api.aladhan.com/v1/timings/{day}"
VAKIT_API = "https://vakit.vercel.app/api/timesForGPS"
EZANVAKTI_API = "https://ezanvakti.imsakiyem.com/api"

ALADHAN_METHODS = {
    "Shia Ithna-Ansari": 0,
    "University of Islamic Sciences, Karachi": 1,
    "Islamic Society of North America": 2,
    "MuslimWorldLeague": 3,
    "UmmAlQura": 4,
    "EgyptianGeneralAuthority": 5,
    "Karachi": 1,
    "Diyanet": 13,
}

PYISLAM_METHODS = {
    "muslimworldleague": 2,
    "islamicsocietyofnorthamerica": 5,
    "egyptiangeneralauthority": 3,
    "karachi": 1,
    "universityofislamicscienceskarachi": 1,
    "ummalqura": 4,
    "makkah": 4,
    "diyanet": 4,
    "turkey": 4,
    "shiaithnaansari": 2,
    "jafari": 2,
    "tehran": 2,
    "islamicreligiouscouncilofsingapore": 7,
    "islamicreligiouscouncilofsignapore": 7,
    "muis": 7,
    "jakim": 7,
    "kemenag": 7,
    "frenchmuslims": 6,
    "uoif": 6,
    "spiritualadministrationofmuslimsofrussia": 8,
    "russia": 8,
    "fixedishaatimeinterval90min": 9,
}


class PrayerProvider(Protocol):
    name: str

    def fetch(self, day: date, location: GeoLocation, settings: PrayerSettings) -> PrayerSchedule:
        ...


class AladhanProvider:
    name = "aladhan"

    def fetch(self, day: date, location: GeoLocation, settings: PrayerSettings) -> PrayerSchedule:
        method_setting = settings.calculation_method
        method_value = ALADHAN_METHODS.get(method_setting, method_setting)
        try:
            method_value = int(method_value)
        except (TypeError, ValueError):  # pragma: no cover - fallback to MWL
            method_value = 3
        params = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "method": method_value,
            "school": 1 if settings.madhab.lower() == "hanafi" else 0,
        }
        url = ALADHAN_API.format(day=day.strftime("%Y-%m-%d"))
        response = httpx.get(url, params=params, timeout=10.0)
        response.raise_for_status()
        payload = response.json()
        timings = payload.get("data", {}).get("timings", {})
        return PrayerSchedule.from_dict(
            {
                "fajr": _sanitize_time(timings.get("Fajr", "05:00")),
                "dhuhr": _sanitize_time(timings.get("Dhuhr", "12:30")),
                "asr": _sanitize_time(timings.get("Asr", "15:30")),
                "maghrib": _sanitize_time(timings.get("Maghrib", "18:05")),
                "isha": _sanitize_time(timings.get("Isha", "19:45")),
                "sunrise": _sanitize_time(timings.get("Sunrise", "")),
            }
        )


class VakitProvider:
    name = "vakit"

    def fetch(self, day: date, location: GeoLocation, settings: PrayerSettings) -> PrayerSchedule:
        tz = _resolve_timezone(location.timezone)
        offset_minutes = int(_offset_minutes(tz, day))
        params = {
            "lat": location.latitude,
            "lng": location.longitude,
            "date": day.strftime("%Y-%m-%d"),
            "days": 1,
            "timezoneOffset": offset_minutes,
            "calculationMethod": settings.calculation_method or "Turkey",
        }
        response = httpx.get(VAKIT_API, params=params, timeout=10.0)
        response.raise_for_status()
        payload = response.json()
        times = payload.get("times", {})
        day_values = next(iter(times.values()), [])
        if len(day_values) < 6:
            raise ValueError("Unexpected response from Vakit API")
        # Vakit order: Imsak, Gunes, Ogle, Ikindi, Aksam, Yatsi
        return PrayerSchedule.from_dict(
            {
                "fajr": day_values[0],
                "sunrise": day_values[1],
                "dhuhr": day_values[2],
                "asr": day_values[3],
                "maghrib": day_values[4],
                "isha": day_values[5],
            }
        )


class PyIslamProvider:
    name = "pyislam"

    def fetch(self, day: date, location: GeoLocation, settings: PrayerSettings) -> PrayerSchedule:
        tz = _resolve_timezone(location.timezone)
        timezone_hours = _offset_minutes(tz, day) / 60
        method_id = _map_pyislam_method(settings.calculation_method)
        conf = PrayerConf(
            longitude=location.longitude or 0.0,
            latitude=location.latitude or 0.0,
            timezone=timezone_hours,
            angle_ref=method_id,
            asr_madhab=2 if settings.madhab.lower() == "hanafi" else 1,
            enable_summer_time=_is_dst(tz, day),
        )
        calculator = PyIslamPrayer(conf, datetime(day.year, day.month, day.day))
        return PrayerSchedule.from_dict(
            {
                "fajr": _format_pyislam_time(calculator.fajr_time()),
                "sunrise": _format_pyislam_time(getattr(calculator, "sunrise_time", lambda: "")()),
                "dhuhr": _format_pyislam_time(calculator.dohr_time()),
                "asr": _format_pyislam_time(calculator.asr_time()),
                "maghrib": _format_pyislam_time(calculator.maghreb_time()),
                "isha": _format_pyislam_time(calculator.ishaa_time()),
            }
        )


class EzanVaktiProvider:
    """Diyanet-backed prayer times via EzanVakti İmsakiyem API."""

    name = "ezanvakti"

    def fetch(self, day: date, location: GeoLocation, settings: PrayerSettings) -> PrayerSchedule:
        district_id = _resolve_ezanvakti_district_id(location)
        if not district_id:
            raise ValueError("EzanVakti requires district_id or (state + district) in location settings")
        url = f"{EZANVAKTI_API}/prayer-times/{district_id}/daily"
        response = httpx.get(url, timeout=10.0)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or []
        if not data:
            raise ValueError("EzanVakti returned empty data")
        entry = _select_ezanvakti_day(data, day)
        times = entry.get("times", {})
        return PrayerSchedule.from_dict(
            {
                "fajr": _sanitize_time(times.get("imsak", "")),
                "sunrise": _sanitize_time(times.get("gunes", "")),
                "dhuhr": _sanitize_time(times.get("ogle", "")),
                "asr": _sanitize_time(times.get("ikindi", "")),
                "maghrib": _sanitize_time(times.get("aksam", "")),
                "isha": _sanitize_time(times.get("yatsi", "")),
            }
        )


class PrayerCache:
    def __init__(self, path: Path, max_days: int) -> None:
        self.path = path
        self.max_days = max(1, max_days)
        self._lock = Lock()
        self._executor: ThreadPoolExecutor | None = ThreadPoolExecutor(max_workers=1)
        self._pending: Future | None = None
        self._cache = self._load()
        with self._lock:
            self._prune_locked()

    def _load(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _persist_async(self, snapshot: str) -> None:
        def _write(payload: str) -> None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(payload, encoding="utf-8")

        executor = self._executor
        if executor is None:
            _write(snapshot)
            return
        self._pending = executor.submit(_write, snapshot)

    def _key(self, provider: str, day: date, location: GeoLocation) -> str:
        lat = location.latitude or 0.0
        lon = location.longitude or 0.0
        return f"{provider}:{day.isoformat()}:{lat:.3f}:{lon:.3f}"

    def _prune_locked(self) -> None:
        buckets: dict[str, list[tuple[date, str]]] = defaultdict(list)
        for key in list(self._cache.keys()):
            parts = key.split(":", 3)
            if len(parts) != 4:
                continue
            provider, day_str, lat, lon = parts
            try:
                day_value = date.fromisoformat(day_str)
            except ValueError:
                self._cache.pop(key, None)
                continue
            bucket_key = f"{provider}:{lat}:{lon}"
            buckets[bucket_key].append((day_value, key))
        for entries in buckets.values():
            entries.sort(key=lambda item: item[0], reverse=True)
            for _, key in entries[self.max_days:]:
                self._cache.pop(key, None)

    def get(self, provider: str, day: date, location: GeoLocation) -> PrayerSchedule | None:
        key = self._key(provider, day, location)
        with self._lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            return PrayerSchedule.from_dict(entry["times"])

    def put(self, provider: str, day: date, location: GeoLocation, schedule: PrayerSchedule) -> None:
        key = self._key(provider, day, location)
        record = {
            "day": day.isoformat(),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "times": schedule.to_dict(),
        }
        with self._lock:
            self._cache[key] = record
            self._prune_locked()
            snapshot = json.dumps(self._cache, indent=2)
        self._persist_async(snapshot)

    def wait_for_io(self) -> None:
        pending = self._pending
        if pending is not None:
            pending.result()
            self._pending = None

    def close(self) -> None:
        executor = self._executor
        if executor is None:
            return
        self.wait_for_io()
        executor.shutdown(wait=False, cancel_futures=False)
        self._executor = None

    def __del__(self) -> None:  # pragma: no cover - defensive cleanup
        try:
            self.close()
        except Exception:
            pass


class PrayerService:
    def __init__(
        self,
        config: MunazzimConfig,
        geolocator: GeoLocator | None = None,
        config_manager: ConfigManager | None = None,
        cache: PrayerCache | None = None,
        prefetch_executor: Executor | None = None,
    ) -> None:
        self.config = config
        self.geolocator = geolocator or GeoLocator()
        self.config_manager = config_manager
        self._detected_location: GeoLocation | None = None
        cache_path = Path.home() / ".cache" / "munazzim" / "prayer_times.json"
        cache_days = max(1, self.config.prayer_settings.cache_days)
        self.cache = cache or PrayerCache(cache_path, cache_days)
        self._prefetch_executor = prefetch_executor or ThreadPoolExecutor(max_workers=1)
        self._prefetch_executor_owned = prefetch_executor is None
        self._prefetch_future: Future | None = None
        pyislam_provider = PyIslamProvider()
        self.providers = {
            "praytimes": pyislam_provider,
            "pyislam": pyislam_provider,
            "aladhan": AladhanProvider(),
            "vakit": VakitProvider(),
            "ezanvakti": EzanVaktiProvider(),
        }

    def get_schedule(self, day: date) -> PrayerSchedule:
        provider = self.providers.get(
            self.config.prayer_settings.provider,
            self.providers["praytimes"],
        )
        location = self._resolve_location()
        cached = self.cache.get(provider.name, day, location)
        if cached:
            # If cached schedule lacks bases referenced by relative overrides,
            # prefer fetching fresh data from provider so overrides can be applied.
            overrides = getattr(self.config, "prayer_overrides", None)
            if overrides and not overrides.is_empty():
                def _missing_bases(schedule: PrayerSchedule) -> bool:
                    for attr in ("fajr", "dhuhr", "asr", "maghrib", "isha"):
                        value = getattr(overrides, attr, None)
                        # Relative overrides are stored as objects with base/minutes
                        if isinstance(value, getattr(overrides, "Relative", None)):
                            base = value.base.lower()
                            # map bases to schedule attributes
                            base_map = {
                                "fajr": "fajr",
                                "dhuhr": "dhuhr",
                                "asr": "asr",
                                "maghrib": "maghrib",
                                "isha": "isha",
                                "sunrise": "sunrise",
                            }
                            base_key = base_map.get(base)
                            if base_key and not getattr(schedule, base_key, None):
                                return True
                    return False

                if _missing_bases(cached):
                    # fallback to fresh provider schedule; if fetch fails, fall back to cached
                    try:
                        schedule = provider.fetch(day, location, self.config.prayer_settings)
                    except Exception:
                        return self._apply_overrides(cached)
                    else:
                        self.cache.put(provider.name, day, location, schedule)
                        return self._apply_overrides(schedule)
            return self._apply_overrides(cached)
        try:
            schedule = provider.fetch(day, location, self.config.prayer_settings)
        except Exception:  # pragma: no cover - network fallbacks
            return self._apply_overrides(self.config.prayers)
        self.cache.put(provider.name, day, location, schedule)
        self._ensure_prefetch(provider, location, day)
        return self._apply_overrides(schedule)

    def _ensure_prefetch(self, provider: PrayerProvider, location: GeoLocation, start_day: date) -> None:
        horizon = max(1, self.config.prayer_settings.cache_days)
        base_day = min(start_day, date.today())
        to_fetch: list[date] = []
        for offset in range(horizon):
            target_day = base_day + timedelta(days=offset)
            if self.cache.get(provider.name, target_day, location):
                continue
            to_fetch.append(target_day)
        if not to_fetch:
            return
        if self._prefetch_future and not self._prefetch_future.done():
            return

        def _prefetch() -> None:
            for target_day in to_fetch:
                try:
                    schedule = provider.fetch(target_day, location, self.config.prayer_settings)
                except Exception:  # pragma: no cover - network fallbacks
                    break
                self.cache.put(provider.name, target_day, location, schedule)

        self._prefetch_future = self._prefetch_executor.submit(_prefetch)

    def _resolve_location(self) -> GeoLocation:
        if self._detected_location:
            return self._detected_location
        loc = self.config.location
        missing_coordinates = (
            loc.latitude is None or loc.longitude is None or not loc.timezone
        )
        needs_detection = loc.use_geolocation and missing_coordinates
        if needs_detection:
            resolved = self.geolocator.detect()
            if resolved:
                merged = GeoLocation(
                    latitude=loc.latitude if loc.latitude is not None else resolved.latitude,
                    longitude=loc.longitude if loc.longitude is not None else resolved.longitude,
                    city=loc.city or resolved.city,
                    country=loc.country or resolved.country,
                    state=loc.state or resolved.state,
                    district=loc.district or resolved.district,
                    district_id=loc.district_id or resolved.district_id,
                    timezone=loc.timezone or resolved.timezone,
                )
                self._remember_location(merged)
                self._detected_location = merged
                return merged
        if loc.latitude is not None and loc.longitude is not None and loc.timezone:
            return GeoLocation(
                latitude=loc.latitude,
                longitude=loc.longitude,
                city=loc.city,
                country=loc.country,
                state=loc.state,
                district=loc.district,
                district_id=loc.district_id,
                timezone=loc.timezone,
            )
        fallback_timezone = loc.timezone or datetime.now().astimezone().tzinfo.tzname(None) or "UTC"
        return GeoLocation(
            latitude=loc.latitude or 0.0,
            longitude=loc.longitude or 0.0,
            city=loc.city or "",
            country=loc.country or "",
            state=loc.state or "",
            district=loc.district or "",
            district_id=loc.district_id,
            timezone=fallback_timezone,
        )

    def _remember_location(self, geo: GeoLocation) -> None:
        loc = self.config.location
        if geo.latitude is not None:
            loc.latitude = geo.latitude
        if geo.longitude is not None:
            loc.longitude = geo.longitude
        if geo.city:
            loc.city = geo.city
        if geo.country:
            loc.country = geo.country
        if geo.state:
            loc.state = geo.state
        if geo.district:
            loc.district = geo.district
        if geo.district_id:
            loc.district_id = geo.district_id
        if geo.timezone:
            loc.timezone = geo.timezone
        if not loc.use_geolocation:
            loc.use_geolocation = True
        if loc.persist_geolocation and self.config_manager:
            self.config_manager.save(self.config)

    def _apply_overrides(self, schedule: PrayerSchedule) -> PrayerSchedule:
        overrides = getattr(self.config, "prayer_overrides", None)
        if not overrides or overrides.is_empty():
            return schedule
        def _resolve_override(value, fallback):
            # if override is None -> fallback
            if value is None:
                return fallback
            # absolute time
            if isinstance(value, time):
                return value
            # relative override
            rel = value
            # base map for prayer names to schedule attribute names
            base = rel.base.lower()
            base_map = {
                "dhuhr": "dhuhr",
                "fajr": "fajr",
                "asr": "asr",
                "maghrib": "maghrib",
                "isha": "isha",
                "sunrise": "sunrise",
            }
            base_key = base_map.get(base)
            if not base_key:
                return fallback
            base_time = getattr(schedule, base_key, None)
            if base_time is None:
                return fallback
            # compute shifted time
            dt = datetime.combine(datetime.today(), base_time) + timedelta(minutes=rel.minutes)
            return dt.time()

        return PrayerSchedule(
            fajr=_resolve_override(overrides.fajr, schedule.fajr),
            dhuhr=_resolve_override(overrides.dhuhr, schedule.dhuhr),
            asr=_resolve_override(overrides.asr, schedule.asr),
            maghrib=_resolve_override(overrides.maghrib, schedule.maghrib),
            isha=_resolve_override(overrides.isha, schedule.isha),
            sunrise=schedule.sunrise,
        )

    def close(self) -> None:
        cache_close = getattr(self.cache, "close", None)
        if callable(cache_close):  # pragma: no branch - simple guard
            cache_close()
        if self._prefetch_executor_owned and isinstance(self._prefetch_executor, ThreadPoolExecutor):
            self._prefetch_executor.shutdown(wait=False, cancel_futures=False)

    def __del__(self) -> None:  # pragma: no cover - defensive cleanup
        try:
            self.close()
        except Exception:
            pass


def _sanitize_time(value: str) -> str:
    value = value.strip()
    if " " in value:
        value = value.split(" ", 1)[0]
    if "+" in value:
        value = value.split("+", 1)[0]
    if "-" in value and value.count(":") == 1 and value.split("-", 1)[1].isdigit():
        value = value.split("-", 1)[0]
    return value


def _normalize_ezanvakti_name(value: str) -> str:
    cleaned = value.strip().casefold()
    return re.sub(r"[^\w]+", "", cleaned)


def _ezanvakti_get(path: str, params: dict[str, object] | None = None) -> list[dict] | dict:
    response = httpx.get(f"{EZANVAKTI_API}{path}", params=params, timeout=10.0)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("success") is False:
        raise ValueError(payload.get("message") or "EzanVakti request failed")
    return payload.get("data", payload)


def _ezanvakti_country_id(country_name: str | None) -> str | None:
    if not country_name:
        return None
    countries = _ezanvakti_get("/locations/countries")
    if not isinstance(countries, list):
        return None
    needle = _normalize_ezanvakti_name(country_name)
    for entry in countries:
        name = _normalize_ezanvakti_name(str(entry.get("name", "")))
        name_en = _normalize_ezanvakti_name(str(entry.get("name_en", "")))
        if needle == name or needle == name_en or needle in name or needle in name_en:
            return str(entry.get("_id"))
    return None


def _resolve_ezanvakti_district_id(location: GeoLocation) -> str | None:
    if getattr(location, "district_id", None):
        return location.district_id
    query = (location.district or location.city or "").strip()
    if not query:
        return None
    results = _ezanvakti_get("/locations/search/districts", params={"q": query})
    if not isinstance(results, list):
        return None
    country_id = _ezanvakti_country_id(location.country)
    if country_id:
        results = [item for item in results if str(item.get("country_id")) == str(country_id)]
    if not results:
        return None
    needle = _normalize_ezanvakti_name(query)
    for item in results:
        if needle in (
            _normalize_ezanvakti_name(str(item.get("name", ""))),
            _normalize_ezanvakti_name(str(item.get("name_en", ""))),
        ):
            return str(item.get("_id"))
    return str(results[0].get("_id"))


def _select_ezanvakti_day(entries: list[dict], target_day: date) -> dict:
    if not entries:
        return {}
    target = target_day.isoformat()
    for entry in entries:
        raw = str(entry.get("date", ""))
        if raw[:10] == target:
            return entry
    return entries[0]


def _resolve_timezone(tz_name: str | None) -> timezone:
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:  # pragma: no cover - fallback
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def _offset_minutes(tz: timezone, day: date) -> int:
    dt = datetime(day.year, day.month, day.day, tzinfo=tz)
    offset = dt.utcoffset() or timedelta()
    return int(offset.total_seconds() // 60)


def _normalize_method_key(method_name: str) -> str:
    return "".join(ch for ch in method_name.lower() if ch.isalnum())


def _map_pyislam_method(method_name: str | None) -> int:
    default_method = 2
    if not method_name:
        return default_method
    try:
        method_id = int(method_name)
    except (TypeError, ValueError):
        method_id = None
    if isinstance(method_id, int) and 1 <= method_id <= len(LIST_FAJR_ISHA_METHODS):
        return method_id
    key = _normalize_method_key(method_name)
    return PYISLAM_METHODS.get(key, default_method)


def _is_dst(tz: timezone, day: date) -> bool:
    dt = datetime(day.year, day.month, day.day, tzinfo=tz)
    delta = dt.dst()
    return bool(delta and delta.total_seconds())


def _format_pyislam_time(value: time | datetime | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, str):
        return value.strip()
    return ""