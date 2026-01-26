from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import tomllib

from .models import DayTemplate, Event, EventType, FixedEvent, Task
from .qalib import QalibParseError, parse_qalib
from .timeutils import parse_duration, parse_hhmm


class TemplateParseError(RuntimeError):
    pass


@dataclass(slots=True)
class TemplateRecord:
    template: DayTemplate
    format: Literal["toml", "qalib"]
    source: str
    path: Path | None = None


@dataclass(slots=True)
class TemplateLoadError:
    path: Path | None
    message: str


@dataclass
class TemplateRepository:
    user_directory: Path | None = None

    def __post_init__(self) -> None:
        self._templates: dict[str, TemplateRecord] = {}
        self._errors: list[TemplateLoadError] = []
        self.reload()

    def reload(self) -> None:
        self._templates.clear()
        self._errors.clear()
        if self.user_directory:
            self.user_directory.mkdir(parents=True, exist_ok=True)
            for file in sorted(self.user_directory.glob("*")):
                if file.is_dir():
                    continue
                try:
                    record = self._load_file(file)
                except TemplateParseError as exc:
                    self._errors.append(TemplateLoadError(path=file, message=str(exc)))
                    continue
                self._templates[record.template.name] = record

    def template_names(self) -> list[str]:
        return sorted(self._templates.keys())

    def get(self, name: str) -> DayTemplate:
        record = self._templates.get(name)
        if not record:
            raise KeyError(f"Template '{name}' not found")
        return record.template

    def record(self, name: str) -> TemplateRecord:
        record = self._templates.get(name)
        if not record:
            raise KeyError(f"Template '{name}' not found")
        return record

    def errors(self) -> list[TemplateLoadError]:
        return list(self._errors)

    def ensure_user_directory(self) -> Path:
        if not self.user_directory:
            raise TemplateParseError("No template directory configured in planner settings")
        self.user_directory.mkdir(parents=True, exist_ok=True)
        return self.user_directory

    def _load_file(self, path: Path) -> TemplateRecord:
        data = path.read_bytes()
        fmt = self._format_from_suffix(path.name)
        template = self._parse_bytes(data, fmt=fmt, default_name=path.stem)
        return TemplateRecord(
            template=template,
            format=fmt,
            source=str(path),
            path=path,
        )

    @staticmethod
    def _format_from_suffix(name: str) -> Literal["toml", "qalib"]:
        lowered = name.lower()
        if lowered.endswith(".toml"):
            return "toml"
        if lowered.endswith(".qalib") or lowered.endswith(".plan"):
            return "qalib"
        return "qalib"

    def _parse_bytes(self, raw: bytes, *, fmt: Literal["toml", "qalib"], default_name: str) -> DayTemplate:
        if fmt == "toml":
            return self._load_toml(raw, default_name=default_name)
        try:
            return parse_qalib(raw.decode("utf-8"), default_name=default_name)
        except QalibParseError as exc:  # pragma: no cover - delegated detail
            raise TemplateParseError(str(exc)) from exc

    def _load_toml(self, raw: bytes, *, default_name: str) -> DayTemplate:
        data = tomllib.loads(raw.decode("utf-8"))
        if "start_time" not in data:
            raise TemplateParseError("Template missing required fields")
        events_data = data.get("events", [])
        events: list[Event] = []
        for event_data in events_data:
            events.append(self._parse_event(event_data))
        return DayTemplate(
            name=data.get("name", default_name),
            start_time=parse_hhmm(data["start_time"]),
            description=data.get("description", ""),
            events=events,
        )

    def _parse_event(self, data: dict) -> Event:
        type_value = data.get("type", "relative").lower()
        if data.get("thabbat"):
            type_value = "fixed"
        event_type = EventType(type_value)
        duration = parse_duration(str(data.get("duration", "0.30")))
        base_kwargs = {
            "name": data.get("name", "Unnamed"),
            "duration": duration,
            "flexible": data.get("flexible", True),
            "tasks": [
                Task(
                    label=task.get("label", "Task"),
                    note=task.get("note"),
                    total_occurrences=task.get("occurrences"),
                    remaining_occurrences=task.get("occurrences"),
                )
                for task in data.get("tasks", [])
            ],
        }
        if event_type is EventType.FIXED:
            if "time" not in data:
                raise TemplateParseError("Fixed event requires 'time'")
            return FixedEvent(anchor=parse_hhmm(data["time"]), **base_kwargs)
        if event_type is EventType.PRAYER or base_kwargs["name"].strip().lower().split()[0] in {"fajr","dhuhr","asr","maghrib","isha"}:
            from .models import PrayerEvent  # lazy import to avoid cycle
            prayer_name = data.get("prayer", base_kwargs["name"])
            # Use only the first token of the name as the prayer label
            prayer_label = prayer_name.strip().split()[0]
            # If it is a fixed type (time specified) use that anchor on the PrayerEvent
            anchor = None
            if "time" in data:
                anchor = parse_hhmm(data["time"]) if data.get("time") else None
            if anchor is not None:
                return PrayerEvent(prayer=prayer_label, anchor=anchor, **base_kwargs)
            return PrayerEvent(prayer=prayer_label, **base_kwargs)
        return Event(**base_kwargs)
