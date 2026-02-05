from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
import ast
import re
from typing import Iterable, Sequence

from .models import DayTemplate, Event, FixedEvent, PrayerEvent, PrayerBoundEvent, Task
from .timeutils import format_duration, format_hhmm, parse_hhmm


class QalibParseError(RuntimeError):
    """Raised when a qalib template cannot be parsed."""


_DURATION_TOKEN = re.compile(r"^(?:\d+(?::\d{2})?|\d+\.\d{1,2}|\.\d{1,2})$")
_TIME_TOKEN = re.compile(r"^\d{1,2}[:.]\d{2}$")
_PRAYER_OFFSET_TOKEN = re.compile(r"^(?P<prayer>[A-Za-z]+)(?P<sign>[+-])(?P<minutes>\d{1,3})$")
_PRAYER_ALIASES = {
    "fajr": "fajr",
    "dhuhr": "dhuhr",
    "duhr": "dhuhr",
    "asr": "asr",
    "maghrib": "maghrib",
    "isha": "isha",
}


def _is_time_token(token: str) -> bool:
    return bool(_TIME_TOKEN.match(token.strip()))


def _is_prayer_token(token: str) -> bool:
    return token.strip().lower() in _PRAYER_ALIASES


def _normalize_prayer_token(token: str) -> str:
    normalized = _PRAYER_ALIASES.get(token.strip().lower(), token.strip().lower())
    return normalized.title()


def _parse_time_or_prayer(token: str) -> time | str | None:
    cleaned = token.strip()
    if not cleaned:
        return None
    if _is_time_token(cleaned):
        return parse_hhmm(cleaned)
    offset_match = _PRAYER_OFFSET_TOKEN.match(cleaned)
    if offset_match:
        prayer = _normalize_prayer_token(offset_match.group("prayer"))
        sign = offset_match.group("sign")
        minutes = offset_match.group("minutes")
        return f"{prayer}{sign}{minutes}"
    if _is_prayer_token(cleaned):
        return _normalize_prayer_token(cleaned)
    raise QalibParseError(f"Unsupported time/prayer token '{token}'")


def _strip_inline_comment(value: str) -> str:
    if "#" not in value:
        return value
    hash_index = value.find("#")
    return value[:hash_index].rstrip()


def _duration_between(start: time, end: time) -> timedelta:
    anchor = date.today()
    start_dt = datetime.combine(anchor, start)
    end_dt = datetime.combine(anchor, end)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return end_dt - start_dt


def _duration_token_to_timedelta(token: str) -> timedelta:
    cleaned = token.strip()
    if not cleaned:
        raise QalibParseError("Empty duration token")
    if cleaned.startswith("."):
        minutes = int(cleaned[1:] or "0")
        return timedelta(minutes=minutes)
    if ":" in cleaned:
        hours, minutes = cleaned.split(":", 1)
        return timedelta(hours=int(hours or 0), minutes=int(minutes or 0))
    if "." in cleaned:
        hours, minutes = cleaned.split(".", 1)
        return timedelta(hours=int(hours or 0), minutes=int(minutes or 0))
    if cleaned.isdigit():
        return timedelta(hours=int(cleaned))
    raise QalibParseError(f"Unsupported duration token '{token}'")


def _format_duration_token(value: timedelta) -> str:
    total_minutes = int(value.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}.{minutes:02d}"
    if hours and not minutes:
        return str(hours)
    return f".{minutes:02d}"


_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Div)
_ALLOWED_UNARY = (ast.UAdd, ast.USub)


def _eval_occurrence_expression(raw: str) -> int:
    try:
        tree = ast.parse(raw, mode="eval")
    except SyntaxError as exc:  # pragma: no cover - user input
        raise QalibParseError(f"Invalid occurrences expression '{raw}'") from exc

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, _ALLOWED_UNARY):
            value = _eval(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
            left = _eval(node.left)
            right = _eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
        raise QalibParseError(f"Unsupported expression '{raw}'")

    result = _eval(tree)
    if result <= 0:
        raise QalibParseError("Occurrences must be positive")
    return int(result)


@dataclass
class _EditableEvent:
    index: int
    event: Event


class _TemplateBuilder:
    def __init__(self, default_name: str) -> None:
        self.name = default_name
        self.description = ""
        self.start_time: time | None = None
        self.events: list[Event] = []
        self.prayer_durations: dict[str, str] = {}
        self.prayer_overrides: dict[str, str] = {}
        self._current_event: Event | None = None
        self._block_counter = 1

    def consume(self, line: str, lineno: int) -> None:
        stripped = line.strip()
        if not stripped:
            return
        header = stripped
        if header.startswith("#"):
            header = header[1:].strip()
        lower = header.lower()
        if lower.startswith("name:"):
            self.name = header.split(":", 1)[1].strip()
            return
        if lower.startswith("description:"):
            detail = header.split(":", 1)[1].strip()
            if self.description:
                self.description += " "
            self.description += detail
            return
        if lower.startswith("prayer_durations."):
            self._consume_prayer_setting(
                header, lineno, target="durations"
            )
            return
        if lower.startswith("prayer_overrides."):
            self._consume_prayer_setting(
                header, lineno, target="overrides"
            )
            return
        if stripped.startswith("#"):
            return

        content = _strip_inline_comment(stripped)
        if not content:
            return
        if content.startswith("-"):
            self._add_task(content, lineno)
            return

        tokens = content.split()
        if len(tokens) >= 2 and _is_time_token(tokens[0]) and _is_time_token(tokens[1]):
            self._add_fixed_event(tokens, lineno)
            return
        if len(tokens) >= 2 and _is_time_token(tokens[0]) and tokens[1].startswith("+"):
            self._add_fixed_duration_event(tokens, lineno)
            return
        if len(tokens) >= 2 and _is_prayer_token(tokens[0]) and tokens[1].startswith("+"):
            self._add_prayer_duration_event(tokens, lineno)
            return
        if tokens and ".." in tokens[0]:
            self._add_prayer_range_event(tokens, lineno)
            return
        if self.start_time is None and len(tokens) == 1 and _is_time_token(tokens[0]):
            self.start_time = parse_hhmm(tokens[0])
            return
        self._add_relative_event(tokens, content, lineno)

    def build(self) -> DayTemplate:
        if self.start_time is None:
            raise QalibParseError("Template missing wake-up start time")
        return DayTemplate(
            name=self.name or "Unnamed Template",
            start_time=self.start_time,
            events=self.events,
            description=self.description.strip(),
            prayer_durations=self.prayer_durations,
            prayer_overrides=self.prayer_overrides,
        )

    def _consume_prayer_setting(self, header: str, lineno: int, *, target: str) -> None:
        if ":" not in header:
            raise QalibParseError(f"Line {lineno}: missing ':' in '{header}'")
        key_part, value = header.split(":", 1)
        value = value.strip()
        _, prayer_key = key_part.split(".", 1)
        prayer_key = prayer_key.strip().lower()
        prayer_key = _PRAYER_ALIASES.get(prayer_key, prayer_key)
        if prayer_key not in {"fajr", "dhuhr", "asr", "maghrib", "isha"}:
            raise QalibParseError(f"Line {lineno}: unsupported prayer '{prayer_key}'")
        if not value:
            return
        if target == "durations":
            self.prayer_durations[prayer_key] = value
        else:
            self.prayer_overrides[prayer_key] = value

    def _add_relative_event(self, tokens: Sequence[str], content: str, lineno: int) -> None:
        if not tokens:
            raise QalibParseError(f"Line {lineno}: missing duration token")
        duration_token = tokens[0]
        if not _DURATION_TOKEN.match(duration_token):
            raise QalibParseError(f"Line {lineno}: '{duration_token}' is not a duration")
        duration = _duration_token_to_timedelta(duration_token)
        name = content[len(duration_token) :].strip() or f"Block {self._block_counter}"
        self._block_counter += 1
        # If the name is a prayer (Fajr/Dhuhr/Asr/Maghrib/Isha), create a PrayerEvent
        lower_name = name.strip().lower()
        first_word = lower_name.split()[0] if lower_name else ""
        if _is_prayer_token(first_word):
            event = PrayerEvent(
                name=name,
                prayer=_normalize_prayer_token(first_word),
                duration=duration,
                flexible=False,
                anchor=None,
            )
        else:
            event = Event(name=name, duration=duration)
        self.events.append(event)
        self._current_event = event

    def _add_fixed_event(self, tokens: Sequence[str], lineno: int) -> None:
        start = parse_hhmm(tokens[0])
        end = parse_hhmm(tokens[1])
        remaining = " ".join(tokens[2:]).strip()
        name = remaining or f"Thabbat {self._block_counter}"
        duration = _duration_between(start, end)
        # If the name is a prayer, create a PrayerEvent instead so the
        # scheduler uses the prayer schedule. Duration is preserved.
        lower_name = name.strip().lower()
        first_word = lower_name.split()[0] if lower_name else ""
        if _is_prayer_token(first_word):
            # Create a PrayerEvent with the parsed duration and anchor
            event = PrayerEvent(
                name=name,
                prayer=_normalize_prayer_token(first_word),
                duration=duration,
                flexible=False,
                anchor=start,
            )
        else:
            event = FixedEvent(name=name, duration=duration, anchor=start, flexible=False)
        self.events.append(event)
        self._current_event = event
        self._block_counter += 1

    def _add_prayer_duration_event(self, tokens: Sequence[str], lineno: int) -> None:
        prayer_token = tokens[0]
        duration_token = tokens[1][1:].strip()
        if not duration_token or not _DURATION_TOKEN.match(duration_token):
            raise QalibParseError(
                f"Line {lineno}: '{tokens[1]}' is not a duration"
            )
        duration = _duration_token_to_timedelta(duration_token)
        remaining = " ".join(tokens[2:]).strip()
        name = remaining or f"Thabbat {self._block_counter}"
        event = PrayerBoundEvent(
            name=name,
            duration=duration,
            flexible=False,
            start_ref=_normalize_prayer_token(prayer_token),
            end_ref=None,
        )
        self.events.append(event)
        self._current_event = event
        self._block_counter += 1

    def _add_prayer_range_event(self, tokens: Sequence[str], lineno: int) -> None:
        range_token = tokens[0]
        start_token, end_token = range_token.split("..", 1)
        start_ref = _parse_time_or_prayer(start_token)
        end_ref = _parse_time_or_prayer(end_token)
        if end_ref is None:
            raise QalibParseError(f"Line {lineno}: missing end bound in '{range_token}'")
        remaining = " ".join(tokens[1:]).strip()
        name = remaining or f"Thabbat {self._block_counter}"
        event = PrayerBoundEvent(
            name=name,
            duration=timedelta(0),
            flexible=False,
            start_ref=start_ref,
            end_ref=end_ref,
        )
        self.events.append(event)
        self._current_event = event
        self._block_counter += 1

    def _add_fixed_duration_event(self, tokens: Sequence[str], lineno: int) -> None:
        start = parse_hhmm(tokens[0])
        duration_token = tokens[1][1:].strip()
        if not duration_token or not _DURATION_TOKEN.match(duration_token):
            raise QalibParseError(
                f"Line {lineno}: '{tokens[1]}' is not a duration"
            )
        duration = _duration_token_to_timedelta(duration_token)
        remaining = " ".join(tokens[2:]).strip()
        name = remaining or f"Thabbat {self._block_counter}"
        lower_name = name.strip().lower()
        first_word = lower_name.split()[0] if lower_name else ""
        if first_word in {"fajr", "dhuhr", "asr", "maghrib", "isha"}:
            event = PrayerEvent(
                name=name,
                prayer=first_word.title(),
                duration=duration,
                flexible=False,
                anchor=start,
            )
        else:
            event = FixedEvent(name=name, duration=duration, anchor=start, flexible=False)
        self.events.append(event)
        self._current_event = event
        self._block_counter += 1

    def _add_task(self, content: str, lineno: int) -> None:
        if self._current_event is None:
            raise QalibParseError(f"Line {lineno}: task specified before any event")
        body = content[1:].strip()
        occurrences: int | None = None
        if body.startswith("["):
            closing = body.find("]")
            if closing == -1:
                raise QalibParseError(f"Line {lineno}: malformed occurrences block")
            expr = body[1:closing].strip()
            if expr:
                occurrences = _eval_occurrence_expression(expr)
            body = body[closing + 1 :].strip()
        label, note = _split_task_label_and_note(body)
        self._current_event.tasks.append(
            Task(
                label=label or "Task",
                note=note,
                total_occurrences=occurrences,
                remaining_occurrences=occurrences,
            )
        )


def _split_task_label_and_note(text: str) -> tuple[str, str | None]:
    if "::" not in text:
        return text.strip(), None
    label, note = text.split("::", 1)
    return label.strip(), note.strip() or None


class QalibParser:
    """Parses plaintext qalib templates into structured DayTemplate objects."""

    def parse(self, raw: str, *, default_name: str) -> DayTemplate:
        builder = _TemplateBuilder(default_name)
        for idx, line in enumerate(raw.splitlines(), start=1):
            builder.consume(line, idx)
        return builder.build()


class QalibSerializer:
    """Serializes DayTemplate instances back to qalib plaintext format."""

    def render(self, template: DayTemplate) -> str:
        lines: list[str] = [f"# name: {template.name}"]
        if template.description:
            lines.append(f"# description: {template.description}")
        if template.prayer_durations:
            for prayer in ["fajr", "dhuhr", "asr", "maghrib", "isha"]:
                value = template.prayer_durations.get(prayer)
                if value:
                    lines.append(f"# prayer_durations.{prayer}: {value}")
        if template.prayer_overrides:
            for prayer in ["fajr", "dhuhr", "asr", "maghrib", "isha"]:
                value = template.prayer_overrides.get(prayer)
                if value:
                    lines.append(f"# prayer_overrides.{prayer}: {value}")
        lines.append(format_hhmm(template.start_time))
        for event in template.events:
            if isinstance(event, FixedEvent):
                end_time = self._fixed_end_time(event)
                lines.append(
                    f"{format_hhmm(event.anchor)} {format_hhmm(end_time)} {event.name}".rstrip()
                )
            elif isinstance(event, PrayerBoundEvent):
                lines.append(self._format_prayer_bound_event(event).rstrip())
            else:
                lines.append(f"{_format_duration_token(event.duration)} {event.name}".rstrip())
            for task in event.tasks:
                lines.append(self._format_task(task))
        return "\n".join(lines).strip() + "\n"

    def _fixed_end_time(self, event: FixedEvent) -> time:
        anchor = date.today()
        start_dt = datetime.combine(anchor, event.anchor)
        end_dt = start_dt + event.duration
        return (end_dt.time())

    def _format_task(self, task: Task) -> str:
        prefix = "-"
        occurrence_value = task.total_occurrences
        if occurrence_value is None:
            occurrence_value = task.remaining_occurrences
        if occurrence_value is not None:
            prefix += f" [{occurrence_value}]"
        else:
            prefix += " []"
        body = task.label
        if task.note:
            body = f"{body} :: {task.note}"
        return f"{prefix} {body}".rstrip()

    def _format_prayer_bound_event(self, event: PrayerBoundEvent) -> str:
        def _fmt_ref(value: time | str | None) -> str:
            if value is None:
                return ""
            if isinstance(value, time):
                return format_hhmm(value)
            return str(value)

        if event.end_ref is not None:
            start_text = _fmt_ref(event.start_ref)
            end_text = _fmt_ref(event.end_ref)
            return f"{start_text}..{end_text} {event.name}".strip()
        start_text = _fmt_ref(event.start_ref)
        return f"{start_text} +{_format_duration_token(event.duration)} {event.name}".strip()


def render_template(template: DayTemplate) -> str:
    return QalibSerializer().render(template)


def parse_qalib(raw: str, *, default_name: str) -> DayTemplate:
    return QalibParser().parse(raw, default_name=default_name)
