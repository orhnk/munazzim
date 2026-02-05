from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta, time
import re
from pathlib import Path
import tomllib

from .timeutils import format_duration, parse_duration, parse_hhmm


def _default_config_root() -> Path:
    return Path.home() / ".config" / "munazzim"


def _default_template_dir() -> Path:
    return _default_config_root() / "alqawalib"


def _maybe_parse_time(value: str | None) -> time | None:
    if not value:
        return None
    return parse_hhmm(value)


@dataclass(slots=True)
class LocationSettings:
    city: str = ""
    country: str = ""
    state: str = ""
    district: str = ""
    district_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    timezone: str | None = None
    use_geolocation: bool = True
    persist_geolocation: bool = False


@dataclass(slots=True)
class PrayerSchedule:
    fajr: time
    dhuhr: time
    asr: time
    maghrib: time
    isha: time
    sunrise: time | None = None

    @classmethod
    def from_dict(cls, values: dict[str, str]) -> "PrayerSchedule":
        return cls(
            fajr=parse_hhmm(values.get("fajr", "05:00")),
            dhuhr=parse_hhmm(values.get("dhuhr", "12:30")),
            asr=parse_hhmm(values.get("asr", "15:30")),
            maghrib=parse_hhmm(values.get("maghrib", "18:05")),
            isha=parse_hhmm(values.get("isha", "19:45")),
            sunrise=_maybe_parse_time(values.get("sunrise")),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "fajr": self.fajr.strftime("%H:%M"),
            "dhuhr": self.dhuhr.strftime("%H:%M"),
            "asr": self.asr.strftime("%H:%M"),
            "maghrib": self.maghrib.strftime("%H:%M"),
            "isha": self.isha.strftime("%H:%M"),
            "sunrise": self.sunrise.strftime("%H:%M") if self.sunrise else "",
        }


@dataclass(slots=True)
class PlannerPreferences:
    default_template: str = ""
    day_start: time = field(default_factory=lambda: parse_hhmm("05:00"))
    template_dir: Path = field(default_factory=_default_template_dir)
    week_templates: dict[str, str] = field(default_factory=dict)
    google_task_list: str = ""
    google_calendar_always_sync: bool = False
    google_calendar: str = ""


@dataclass(slots=True)
class PrayerDurations:
    fajr: timedelta = field(default_factory=lambda: timedelta(minutes=20))
    dhuhr: timedelta = field(default_factory=lambda: timedelta(minutes=15))
    asr: timedelta = field(default_factory=lambda: timedelta(minutes=15))
    maghrib: timedelta = field(default_factory=lambda: timedelta(minutes=20))
    isha: timedelta = field(default_factory=lambda: timedelta(minutes=20))

    @classmethod
    def from_dict(cls, values: dict[str, str]) -> "PrayerDurations":
        def read(key: str, default: str) -> timedelta:
            return parse_duration(values.get(key, default))

        return cls(
            fajr=read("fajr", "0:20"),
            dhuhr=read("dhuhr", "0:15"),
            asr=read("asr", "0:15"),
            maghrib=read("maghrib", "0:20"),
            isha=read("isha", "0:20"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "fajr": format_duration(self.fajr),
            "dhuhr": format_duration(self.dhuhr),
            "asr": format_duration(self.asr),
            "maghrib": format_duration(self.maghrib),
            "isha": format_duration(self.isha),
        }


@dataclass(slots=True)
class PrayerSettings:
    provider: str = "aladhan"
    calculation_method: str = "Diyanet"
    madhab: str = "Shafi"
    cache_days: int = 90
    durations: PrayerDurations = field(default_factory=PrayerDurations)


@dataclass(slots=True)
class PrayerOverrides:
    fajr: time | None = None
    dhuhr: time | None = None
    asr: time | None = None
    maghrib: time | None = None
    isha: time | None = None
    # note: overrides can be either an absolute time (HH:MM) or a relative expression like "sunrise - 25"

    @dataclass(slots=True)
    class Relative:
        base: str
        minutes: int

    @classmethod
    def from_dict(cls, values: dict[str, str]) -> "PrayerOverrides":
        def _parse(value: str | None):
            if not value:
                return None
            value = value.strip()
            # absolute time
            try:
                return _maybe_parse_time(value)
            except Exception:
                pass
            # relative values like 'sunrise - 25' or 'dhuhr + 2'
            m = re.match(r"^([a-zA-Z_]+)\s*([+-])\s*(\d+)$", value)
            if not m:
                return None
            base = m.group(1).strip().lower()
            sign = m.group(2)
            mins = int(m.group(3))
            if sign == "-":
                mins = -mins
            return cls.Relative(base=base, minutes=mins)

        return cls(
            fajr=_parse(values.get("fajr")),
            dhuhr=_parse(values.get("dhuhr")),
            asr=_parse(values.get("asr")),
            maghrib=_parse(values.get("maghrib")),
            isha=_parse(values.get("isha")),
        )

    def to_dict(self) -> dict[str, str]:
        def fmt(value: time | None) -> str:
            return value.strftime("%H:%M") if value else ""
        def fmt_rel(value: object | None) -> str:
            if value is None:
                return ""
            if isinstance(value, time):
                return value.strftime("%H:%M")
            if isinstance(value, PrayerOverrides.Relative):
                return f"{value.base} {'+' if value.minutes >= 0 else '-'} {abs(value.minutes)}"
            return ""
        return {
            "fajr": fmt_rel(self.fajr),
            "dhuhr": fmt_rel(self.dhuhr),
            "asr": fmt_rel(self.asr),
            "maghrib": fmt_rel(self.maghrib),
            "isha": fmt_rel(self.isha),
        }

    def is_empty(self) -> bool:
        return not any([
            self.fajr,
            self.dhuhr,
            self.asr,
            self.maghrib,
            self.isha,
        ])


@dataclass(slots=True)
class MunazzimConfig:
    location: LocationSettings
    prayers: PrayerSchedule
    planner: PlannerPreferences
    prayer_settings: PrayerSettings
    prayer_overrides: PrayerOverrides

    @classmethod
    def default(cls) -> "MunazzimConfig":
        return cls(
            location=LocationSettings(),
            prayers=PrayerSchedule.from_dict({}),
            planner=PlannerPreferences(template_dir=_default_template_dir()),
            prayer_settings=PrayerSettings(),
            prayer_overrides=PrayerOverrides(),
        )

    def to_dict(self) -> dict:
        return {
            "location": {
                "city": self.location.city,
                "country": self.location.country,
                "state": self.location.state,
                "district": self.location.district,
                "district_id": self.location.district_id,
                "latitude": self.location.latitude,
                "longitude": self.location.longitude,
                "timezone": self.location.timezone,
                "use_geolocation": self.location.use_geolocation,
                "persist_geolocation": self.location.persist_geolocation,
            },
            "planner": {
                "default_template": self.planner.default_template,
                "day_start": self.planner.day_start.strftime("%H:%M"),
                "template_dir": str(self.planner.template_dir) if self.planner.template_dir else "",
                "week_templates": self.planner.week_templates,
                "google_task_list": self.planner.google_task_list,
                "google_calendar_always_sync": self.planner.google_calendar_always_sync,
                "google_calendar": self.planner.google_calendar,
            },
            "prayer_settings": {
                "provider": self.prayer_settings.provider,
                "calculation_method": self.prayer_settings.calculation_method,
                "madhab": self.prayer_settings.madhab,
                "cache_days": self.prayer_settings.cache_days,
            },
            "prayer_durations": self.prayer_settings.durations.to_dict(),
            "prayer_overrides": self.prayer_overrides.to_dict(),
        }


class ConfigManager:
    """Simple TOML configuration loader."""

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or (Path.home() / ".config" / "munazzim" / "config.toml")
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._errors: list[str] = []

    def errors(self) -> list[str]:
        return list(self._errors)

    def load(self) -> MunazzimConfig:
        self._errors.clear()
        if not self.config_path.exists():
            config = MunazzimConfig.default()
            self._write(config)
            return config

        with self.config_path.open("rb") as handle:
            raw = tomllib.load(handle)

        location_cfg = raw.get("location", {})
        planner_cfg = raw.get("planner", {})
        week_cfg = planner_cfg.get("week_templates", {})
        
        def _float_or_none(value: float | str | None) -> float | None:
            if value in (None, "", "nan"):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                return None

        def _normalize_day(key: str) -> str:
            return key.strip().lower()

        def _string_or_none(value: object | None) -> str | None:
            if value in (None, "", "null"):
                return None
            return str(value)

        template_dir_value = planner_cfg.get("template_dir") or str(_default_template_dir())
        template_dir = Path(template_dir_value).expanduser()
        template_dir.mkdir(parents=True, exist_ok=True)

        try:
            prayers = PrayerSchedule.from_dict(raw.get("prayers", {}))
        except Exception as exc:
            self._errors.append(f"Invalid prayer time in config: {exc}")
            prayers = PrayerSchedule.from_dict({})

        try:
            day_start = parse_hhmm(planner_cfg.get("day_start", "05:00"))
        except Exception as exc:
            self._errors.append(f"Invalid planner.day_start: {exc}")
            day_start = parse_hhmm("05:00")

        try:
            durations = PrayerDurations.from_dict(raw.get("prayer_durations", {}))
        except Exception as exc:
            self._errors.append(f"Invalid prayer_durations in config: {exc}")
            durations = PrayerDurations()

        config = MunazzimConfig(
            location=LocationSettings(
                city=location_cfg.get("city", ""),
                country=location_cfg.get("country", ""),
                state=location_cfg.get("state", ""),
                district=location_cfg.get("district", ""),
                district_id=_string_or_none(location_cfg.get("district_id")),
                latitude=_float_or_none(location_cfg.get("latitude")),
                longitude=_float_or_none(location_cfg.get("longitude")),
                timezone=location_cfg.get("timezone"),
                use_geolocation=location_cfg.get("use_geolocation", True),
                persist_geolocation=location_cfg.get("persist_geolocation", False),
            ),
            prayers=prayers,
            planner=PlannerPreferences(
                default_template=planner_cfg.get("default_template", ""),
                day_start=day_start,
                template_dir=template_dir,
                week_templates={_normalize_day(k): v for k, v in week_cfg.items() if v},
                google_task_list=planner_cfg.get("google_task_list", ""),
                google_calendar_always_sync=planner_cfg.get("google_calendar_always_sync", False),
                google_calendar=planner_cfg.get("google_calendar", ""),
            ),
            prayer_settings=PrayerSettings(
                provider=raw.get("prayer_settings", {}).get("provider", "aladhan"),
                calculation_method=raw.get("prayer_settings", {}).get("calculation_method", "Diyanet"),
                madhab=raw.get("prayer_settings", {}).get("madhab", "Shafi"),
                cache_days=int(raw.get("prayer_settings", {}).get("cache_days", 90)),
                durations=durations,
            ),
            prayer_overrides=PrayerOverrides.from_dict(raw.get("prayer_overrides", {})),
        )
        return config

    def _write(self, config: MunazzimConfig) -> None:
        data = config.to_dict()
        lines = ["[location]"]
        lines.append(f"city = \"{data['location']['city']}\"")
        lines.append(f"country = \"{data['location']['country']}\"")
        lines.append(f"state = \"{data['location']['state']}\"")
        lines.append(f"district = \"{data['location']['district']}\"")
        if data["location"]["district_id"]:
            lines.append(f"district_id = \"{data['location']['district_id']}\"")
        if data["location"]["latitude"] is not None:
            lines.append(f"latitude = {data['location']['latitude']}")
        if data["location"]["longitude"] is not None:
            lines.append(f"longitude = {data['location']['longitude']}")
        lines.append(f"timezone = \"{data['location']['timezone'] or ''}\"")
        lines.append(f"use_geolocation = {str(data['location']['use_geolocation']).lower()}")
        lines.append(f"persist_geolocation = {str(data['location']['persist_geolocation']).lower()}")
        lines.extend([
            "",
            "[planner]",
            f"default_template = \"{data['planner']['default_template']}\"",
            f"day_start = \"{data['planner']['day_start']}\"",
            f"template_dir = \"{data['planner']['template_dir']}\"",
            f"google_task_list = \"{data['planner']['google_task_list']}\"",
            f"google_calendar = \"{data['planner']['google_calendar']}\"",
        ])
        lines.extend([
            "",
            "[planner.week_templates]",
        ])
        if data["planner"]["week_templates"]:
            for day, template in sorted(data["planner"]["week_templates"].items()):
                lines.append(f"{day} = \"{template}\"")
        lines.extend([
            "",
            "[prayer_settings]",
            f"provider = \"{data['prayer_settings']['provider']}\"",
            f"calculation_method = \"{data['prayer_settings']['calculation_method']}\"",
            f"madhab = \"{data['prayer_settings']['madhab']}\"",
            f"cache_days = {data['prayer_settings']['cache_days']}",
            "",
            "[prayer_durations]",
        ])
        for prayer, value in data["prayer_durations"].items():
            lines.append(f"{prayer} = \"{value}\"")
        lines.extend([
            "",
            "[prayer_overrides]",
        ])
        for prayer, value in data["prayer_overrides"].items():
            lines.append(f"{prayer} = \"{value}\"")
        self.config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def save(self, config: MunazzimConfig) -> None:
        self._write(config)
