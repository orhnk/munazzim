from __future__ import annotations

import os
import asyncio
import threading
import hashlib
import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from textual.app import App, ComposeResult  # type: ignore[import]
from textual.binding import Binding  # type: ignore[import]
from textual.containers import Horizontal, Vertical  # type: ignore[import]
from textual.widgets import DataTable, Footer, Header, Static  # type: ignore[import]
from textual import events  # type: ignore[import]
from textual.timer import Timer  # type: ignore[import]
from textual.widget import Widget  # type: ignore[import]

from ..config import ConfigManager
from ..models import DayPlan
from ..tasks import TaskAssignmentEngine, TaskStore
from ..services.prayer import PrayerService
try:
    from ..services.google_tasks import GoogleTasksService
except Exception:  # pragma: no cover - optional dependency
    GoogleTasksService = None
try:
    from ..services.google_calendar import GoogleCalendarService
except Exception:  # pragma: no cover - optional dependency
    GoogleCalendarService = None
from ..scheduler import Scheduler
from ..templates import TemplateParseError, TemplateRepository
from ..timeutils import format_duration
from ..validation import TemplateValidationError, TemplateValidator
from .screens import (
    TemplateChoice,
    TemplatePickerScreen,
    TemplateErrorScreen,
    ErrorScreen,
    TaskListChoice,
    TaskListPickerScreen,
    TextEntryScreen,
)


@dataclass
class PlanContext:
    template_name: str
    location: str
    provider: str
    plan_capacity: str
    day_label: str


WEEKDAY_ORDER = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


class StatusLine(Static):
    def show(self, context: PlanContext) -> None:
        template_label = context.template_name or "None"
        self.update(
            " • ".join(
                [
                    f"Template: {template_label}",
                    f"Provider: {context.provider}",
                    f"Location: {context.location or 'Unknown'}",
                    f"TO PLAN: {context.plan_capacity}",
                    context.day_label,
                ]
            )
        )


@dataclass(slots=True)
class TodoDisplay:
    task: str
    event: str
    note: str | None = None
    due: str | None = None
    task_id: str | None = None
    assignment_id: str | None = None
    total: int | None = None
    ordinal: int | None = None
    checked: bool = False
    toggleable: bool = False
    last_completed: date | None = None
    provider: str | None = None


class TaskTableView(DataTable):
    BINDINGS = [
        Binding("enter", "complete_selected", "Toggle Task"),
        Binding("space", "complete_selected", "Toggle Task", show=False),
        Binding("s", "select_task_list", "Select Task List"),
    Binding("a", "add_task", "Add Task"),
        Binding("e", "edit_task", "Edit Task"),
    Binding("x", "delete_task", "Delete Task", show=False),
    Binding("d", "delete_task", "Delete Task", show=False),
        Binding("delete", "delete_task", "Delete Task"),
    ]

    def __init__(
        self,
        *,
        on_toggle: Callable[[str, bool], None],
        on_log: Callable[[str], None],
        on_unlog: Callable[[str], None] | None = None,
        on_external_toggle: Callable[[str, bool], None] | None = None,
    ) -> None:
        super().__init__(zebra_stripes=True, id="task-table")
        self.cursor_type = "row"
        self._on_toggle = on_toggle
        self._on_log = on_log
        self._on_unlog = on_unlog
        self._on_external_toggle = on_external_toggle
        self._row_metadata: list[TodoDisplay | None] = []
        self.visible = False
        self._ready = False
        self._pending_items: list[TodoDisplay] | None = None

    def action_select_task_list(self) -> None:
        """Invoke the app-level task-list selection flow (s key)."""
        try:
            # Prefer app-level action; use try/except because self.app may
            # not be available in headless/test environments.
            if hasattr(self.app, "action_select_task_list"):
                self.app.action_select_task_list()
        except Exception:
            # Headless or missing app - nothing to do
            return

    def action_add_task(self) -> None:
        """Invoke the app-level add task flow (when 'a' pressed in todo widget)."""
        try:
            if hasattr(self.app, "action_add_task"):
                self.app.action_add_task()
        except Exception:
            return

    def action_edit_task(self) -> None:
        """Invoke app-level edit task for the currently selected todo row."""
        try:
            if hasattr(self.app, "action_edit_task"):
                self.app.action_edit_task()
        except Exception:
            return

    def action_delete_task(self) -> None:
        """Invoke app-level delete for the currently selected todo row."""
        try:
            if hasattr(self.app, "action_delete_task"):
                self.app.action_delete_task()
        except Exception:
            return

    def on_mount(self) -> None:  # type: ignore[override]
        self._ensure_columns()
        self._ready = True
        if self._pending_items is not None:
            items = self._pending_items
            self._pending_items = None
            self._populate(items)

    def update_tasks(self, items: list[TodoDisplay]) -> None:
        if not self._ready:
            self._pending_items = items
            return
        self._populate(items)

    def _populate(self, items: list[TodoDisplay]) -> None:
        self.clear()
        self._row_metadata.clear()
        if not items:
            # Keep columns in sync with _ensure_columns: Done, Task, Progress, Event, Due, Note
            # Provide empty cells for each column
            self.add_row("No tasks attached", "", "", "", "", "")
            self.show_cursor = False
            return
        self.show_cursor = True
        # prefer explicit preservation set by action_complete_selected when present
        preserved_row = getattr(self, "_preserve_cursor_row", None)
        preserved_col = getattr(self, "_preserve_cursor_col", None)
        preserved_assignment = getattr(self, "_preserve_assignment_id", None)
        preserved_task = getattr(self, "_preserve_task_id", None)
        if preserved_row is None:
            preserved_row = getattr(self, "cursor_row", None)
            preserved_col = getattr(self, "cursor_column", None)
        for todo in items:
            progress = self._format_progress(todo)
            note = todo.note or ""
            label = self._format_task_label(todo)
            # 'Done' column shows the checkbox; make checked state stand out
            # with bold green, keep an empty cell for non-toggleable items.
            if todo.toggleable:
                # Use a more visible check mark for completed items - big emoji
                done = "[bold white on green] ✅ [/bold white on green]" if todo.checked else ""
            else:
                done = ""
            # Format due column
            due_display = self._format_due(getattr(todo, "due", None))
            # Done placed before Task to show prominently
            self.add_row(done, label, progress, todo.event, due_display, note)
            self._row_metadata.append(todo)
        if self.row_count:
            # Restore prior row index if possible, else default to 0
            # If there is a preserved assignment or task id, prefer matching that
            if preserved_assignment is not None:
                target_index = next(
                    (i for i, row in enumerate(self._row_metadata) if row and row.assignment_id == preserved_assignment),
                    None,
                )
                if target_index is not None:
                    col = preserved_col if preserved_col is not None else 0
                    self.cursor_coordinate = (target_index, col)
            elif preserved_task is not None:
                target_index = next(
                    (i for i, row in enumerate(self._row_metadata) if row and row.task_id == preserved_task),
                    None,
                )
                if target_index is not None:
                    col = preserved_col if preserved_col is not None else 0
                    self.cursor_coordinate = (target_index, col)
            elif preserved_row is not None and preserved_row < self.row_count:
                col = preserved_col if preserved_col is not None else 0
                # Clamp column to available columns (DataTable doesn't expose
                # column_count; ensure col is >= 0)
                if col < 0:
                    col = 0
                self.cursor_coordinate = (preserved_row, col)
            else:
                self.cursor_coordinate = (0, 0)
        # Clear any transient preservation marker after we've applied it
        if hasattr(self, "_preserve_cursor_row"):
            self._preserve_cursor_row = None
            self._preserve_cursor_col = None
        if hasattr(self, "_preserve_assignment_id"):
            self._preserve_assignment_id = None
        if hasattr(self, "_preserve_task_id"):
            self._preserve_task_id = None

    @property
    def has_tasks(self) -> bool:
        return any(row is not None for row in self._row_metadata)

    def _ensure_columns(self) -> None:
        if not self.columns:
            # Add a dedicated Done column first to keep the task label clean
            self.add_columns("Done", "Task", "Progress", "Event", "Due", "Note")
        return None

    def action_complete_selected(self) -> None:
        if self.cursor_row is None:
            return
        if self.cursor_row >= len(self._row_metadata):
            return
        todo = self._row_metadata[self.cursor_row]
        if todo is None:
            return
        # Remember cursor position so updates don't move focus.
        self._preserve_cursor_row = self.cursor_row
        self._preserve_cursor_col = getattr(self, "cursor_column", None)
        # Also preserve the logical ids of the item we toggled so we can
        # re-find it in the view after the data updates (safer than row index).
        if todo.assignment_id:
            self._preserve_assignment_id = todo.assignment_id
            self._preserve_task_id = None
        else:
            self._preserve_assignment_id = None
            self._preserve_task_id = todo.task_id

        if todo.toggleable:
            # If the todo comes from an external provider, route to external handler
            if todo.provider and self._on_external_toggle:
                self._on_external_toggle(todo.task_id or "", not todo.checked)
                return
            if todo.assignment_id:
                self._on_toggle(todo.assignment_id, not todo.checked)
                return
            if todo.task_id:
                # Non-counted (open) tasks are toggled via logging (mark done) or
                # unlogging (clear last_completed). Use on_unlog when available.
                if todo.checked:
                    if self._on_unlog:
                        self._on_unlog(todo.task_id)
                else:
                    self._on_log(todo.task_id)
                return
        if todo.task_id:
            self._on_log(todo.task_id)

    def on_key(self, event: events.Key) -> None:  # type: ignore[override]
        if event.key == "f" and hasattr(self.app, "action_focus_plan"):
            event.stop()
            self.app.action_focus_plan()
            return
        handler = getattr(super(), "on_key", None)
        if handler:
            handler(event)

    def _format_progress(self, todo: TodoDisplay) -> str:
        if todo.total is not None and todo.ordinal is not None:
            suffix = " ✓" if todo.checked else ""
            return f"{todo.ordinal}/{todo.total}{suffix}"
        last = todo.last_completed
        if last is None:
            return "—"
        today = date.today()
        if last == today:
            return "✓ Today"
        delta = (today - last).days
        if delta == 1:
            return "✓ Yesterday"
        if delta < 7:
            return f"✓ {delta}d ago"
        return f"✓ {last.isoformat()}"

    def _format_task_label(self, todo: TodoDisplay) -> str:
        # The Done column contains any checkbox/tick; keep label text clean.
        return todo.task

    def _format_due(self, due: str | None) -> str:
        """Format an RFC3339-ish due string into a local short datetime.

        Accepts strings like '2025-12-31T10:00:00Z' or ISO offsets. Falls back
        to returning the original string if parsing fails.
        """
        if not due:
            return ""
        try:
            # Accept datetime or string; normalize Z timezone
            if isinstance(due, str):
                s = due
                if s.endswith("Z"):
                    s = s.replace("Z", "+00:00")
                dt = datetime.fromisoformat(s)
            elif isinstance(due, datetime):
                dt = due
            else:
                return str(due)
            # Convert to local timezone and show date+time
            local = dt.astimezone()
            return local.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(due)


class WeekPlannerWidget(Widget):
    def __init__(
        self,
        on_assignments_changed: Callable[[dict[str, str], bool], None],
        on_toggle: Callable[[str, bool], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_unlog: Callable[[str], None] | None = None,
        on_external_toggle: Callable[[str, bool], None] | None = None,
    ) -> None:
        super().__init__(classes="panel week-panel")
        self._on_assignments_changed = on_assignments_changed
        self.table = WeekPlannerTable(self._notify_change)
        self._help = Static(
            "[dim]h/l change • j/k move • gg/G jump • delete clear • w focus[/dim]",
            classes="panel-help",
        )
        self.todo_table: TaskTableView | None = None
        if on_toggle is not None and on_log is not None:
            self.todo_table = TaskTableView(
                on_toggle=on_toggle,
                on_log=on_log,
                on_unlog=on_unlog,
                on_external_toggle=on_external_toggle,
            )
            # ensure the todo box is visible in the side panel
            self.todo_table.visible = True

    def compose(self) -> ComposeResult:
        yield Static("Week Templates", classes="panel-title")
        yield self.table
        yield self._help
        if self.todo_table is not None:
            yield Static("To-dos", classes="panel-title")
            yield self.todo_table

    def set_data(self, assignments: dict[str, str], template_names: list[str], current_day: str | None) -> None:
        self.table.set_data(assignments, template_names, current_day)

    def focus_table(self) -> None:
        self.table.focus()

    def focus_todos(self) -> None:
        if self.todo_table:
            try:
                self.todo_table.focus()
            except Exception:
                # In non-Textual contexts (tests/headless), focus can raise
                # NoActiveAppError; treat as a benign no-op.
                pass

    def set_todos(self, items: list[TodoDisplay]) -> None:
        if self.todo_table:
            self.todo_table.update_tasks(items)
            # show / hide todo list based on items
            self.todo_table.visible = bool(items)

    def jump_top(self) -> None:
        self.table.jump_top()

    def jump_bottom(self) -> None:
        self.table.jump_bottom()

    def commit_if_dirty(self) -> None:
        self.table.commit_if_dirty()

    def _notify_change(self, assignments: dict[str, str], *, persist: bool) -> None:
        self._on_assignments_changed(assignments, persist)

    def has_todos(self) -> bool:
        if not self.todo_table:
            return False
        if self.todo_table._pending_items is not None:
            return True
        return self.todo_table.has_tasks


# Previously we used a VimGJumpMixin to support 'gg' (double-g). Jenkins-style
# Textual bindings replaced that; the jump-to-top hook is implemented by the
# table classes themselves and 'g' is handled through Binding.


class PlanTable(DataTable):
    # Use Textual's bindings to enable hjkl and arrow navigation within the
    # plan table. We still keep a small set of blocked keys if needed by
    # the application, but prefer to let the table handle cursor actions.
    BINDINGS = [
        Binding("j", "cursor_down", "Next Event"),
        Binding("k", "cursor_up", "Previous Event"),
        Binding("g", "cursor_top", "First Event", show=False),
        Binding("G", "cursor_bottom", "Last Event", show=False),
        Binding("up", "cursor_up", "Previous Event"),
        Binding("down", "cursor_down", "Next Event"),
        Binding("home", "cursor_top", "First Event", show=False),
        Binding("end", "cursor_bottom", "Last Event", show=False),
        Binding("h", "cursor_left", "Left Col", show=False),
        Binding("l", "cursor_right", "Right Col", show=False),
    ]
    BLOCKED_KEYS: set[str] = set()

    def __init__(self) -> None:
        super().__init__(zebra_stripes=True)
        self.cursor_type = "row"
        self.show_cursor = True
        self.id = "plan-table"
        # Capture scheduled events per-row so UI actions can open associated
        # event files. The list is kept in lock-step with the table rows
        # created by refresh_plan; entries may be None for non-event rows.
        self._row_metadata: list[object | None] = []

    def _vim_jump_top(self) -> None:
        self.jump_to_row(0)

    def jump_to_row(self, target_row: int) -> None:
        if self.row_count == 0:
            return
        row = max(0, min(target_row, self.row_count - 1))
        col = self.cursor_column or 0
        self.cursor_coordinate = (row, col)
        try:
            self.scroll_to_row(row)
        except Exception:
            pass
        # Notify the app that the focused row changed so it can update
        # ancillary UI (like the todo side panel) if desired.
        try:
            if hasattr(self.app, "on_plan_cursor_changed"):
                # Pass the computed row index rather than relying on the
                # DataTable's cursor property which may not be available in
                # headless or test contexts.
                self.app.on_plan_cursor_changed(row)
        except Exception:
            # Defensive: tests/headless contexts may not provide app or full
            # Textual support; ignore failures here.
            pass

    def clear(self) -> None:  # type: ignore[override]
        """Clear the table and any per-row metadata we store."""
        try:
            self._row_metadata.clear()
        except Exception:
            pass
        return super().clear()

    def move_rows(self, delta: int) -> None:
        if self.row_count == 0:
            return
        # Notify app that this is a user-initiated navigation so we can
        # suppress auto-highlighting for a short period.
        try:
            if hasattr(self.app, "on_user_navigated"):
                self.app.on_user_navigated()
        except Exception:
            pass
        current_row = self.cursor_row or 0
        self.jump_to_row(current_row + delta)

    def action_cursor_down(self) -> None:
        self.move_rows(1)
        try:
            if hasattr(self.app, "on_user_navigated"):
                self.app.on_user_navigated()
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        self.move_rows(-1)
        try:
            if hasattr(self.app, "on_user_navigated"):
                self.app.on_user_navigated()
        except Exception:
            pass

    def action_cursor_bottom(self) -> None:
        self.jump_to_row(self.row_count - 1)
        try:
            if hasattr(self.app, "on_user_navigated"):
                self.app.on_user_navigated()
        except Exception:
            pass

    def action_cursor_left(self) -> None:
        """Move focus horizontally to the left column if available."""
        try:
            col = getattr(self, "cursor_column", 0) or 0
            col = max(0, col - 1)
            row = getattr(self, "cursor_row", 0) or 0
            self.cursor_coordinate = (row, col)
        except Exception:
            return
        try:
            if hasattr(self.app, "on_user_navigated"):
                self.app.on_user_navigated()
        except Exception:
            pass

    def action_cursor_right(self) -> None:
        """Move focus horizontally to the right column if available."""
        try:
            col = getattr(self, "cursor_column", 0) or 0
            col = col + 1
            row = getattr(self, "cursor_row", 0) or 0
            self.cursor_coordinate = (row, col)
        except Exception:
            return
        try:
            if hasattr(self.app, "on_user_navigated"):
                self.app.on_user_navigated()
        except Exception:
            pass

    def on_key(self, event: events.Key) -> None:  # type: ignore[override]
        if event.key == "f":
            event.stop()
            if hasattr(self.app, "action_focus_tasks"):
                self.app.action_focus_tasks()
            return
        if event.key in self.BLOCKED_KEYS:
            event.stop()
            return
        handler = getattr(super(), "on_key", None)
        if handler:
            handler(event)
        # 'd' opens the associated event task file if present
        if event.key == "d":
            try:
                if hasattr(self.app, "action_open_event_tasks"):
                    event.stop()
                    self.app.action_open_event_tasks()
            except Exception:
                pass

    def on_mouse_move(self, event: events.MouseMove) -> None:  # type: ignore[override]
        event.stop()

    def on_mouse_down(self, event: events.MouseDown) -> None:  # type: ignore[override]
        self.focus()
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:  # type: ignore[override]
        event.stop()

    def on_click(self, event: events.Click) -> None:  # type: ignore[override]
        self.focus()
        event.stop()

    def on_double_click(self, event: events.DoubleClick) -> None:  # type: ignore[override]
        self.focus()
        event.stop()


class WeekPlannerTable(DataTable):
    BINDINGS = [
    Binding("g", "cursor_top", "First Day", show=False),
        Binding("right", "next_template", "Next Template"),
        Binding("left", "previous_template", "Previous Template"),
        Binding("delete", "clear", "Clear Day"),
        Binding("h", "previous_template", "Previous Template", show=False),
        Binding("l", "next_template", "Next Template", show=False),
        Binding("j", "cursor_down", "Next Day", show=False),
        Binding("k", "cursor_up", "Previous Day", show=False),
    Binding("G", "cursor_bottom", "Last Day", show=False),
    ]

    def __init__(self, notifier: Callable[[dict[str, str], bool], None]) -> None:
        super().__init__(zebra_stripes=True)
        self.assignments: dict[str, str] = {}
        self.template_names: list[str] = []
        self.day_order = list(WEEKDAY_ORDER)
        self._day_row_keys: dict[str, object] = {}
        self._template_column_key: object | None = None
        self._current_day: str | None = None
        self._notifier = notifier
        self._dirty = False
        self.cursor_type = "row"
        self.id = "week-table"

    def set_data(self, assignments: dict[str, str], template_names: list[str], current_day: str | None) -> None:
        self.assignments = dict(assignments)
        self.template_names = list(template_names)
        self._current_day = current_day
        self._dirty = False
        self._rebuild_table()

    def _rebuild_table(self) -> None:
        self.clear(columns=True)
        self._day_row_keys.clear()
        column_keys = self.add_columns("Day", "Template")
        if len(column_keys) >= 2:
            self._template_column_key = column_keys[1]
        else:
            self._template_column_key = None
        for day in self.day_order:
            pointer = "▶" if day == self._current_day else " "
            display = f"{pointer} {day.capitalize()}"
            template = self.assignments.get(day, "")
            row_key = self.add_row(display, template)
            self._day_row_keys[day] = row_key
        if self.row_count:
            self.cursor_coordinate = (0, 0)

    def _current_day_key(self) -> str | None:
        if self.cursor_row is None:
            return None
        if self.cursor_row < 0 or self.cursor_row >= len(self.day_order):
            return None
        return self.day_order[self.cursor_row]

    def _cycle_template(self, delta: int) -> None:
        day_key = self._current_day_key()
        if day_key is None or not self.template_names:
            return
        current = self.assignments.get(day_key)
        try:
            current_index = self.template_names.index(current) if current else -1
        except ValueError:
            current_index = -1
        new_index = (current_index + delta) % len(self.template_names)
        chosen = self.template_names[new_index]
        self.assignments[day_key] = chosen
        self._update_row_cell(day_key, chosen)
        self._mark_dirty()

    def _update_row_cell(self, day_key: str, value: str) -> None:
        row_key = self._day_row_keys.get(day_key)
        column_key = self._template_column_key
        if row_key is None or column_key is None:
            if self.cursor_row is not None:
                self.update_cell_at(self.cursor_row, 1, value)
            return
        try:
            self.update_cell(row_key, column_key, value)
        except Exception:
            if self.cursor_row is not None:
                self.update_cell_at(self.cursor_row, 1, value)

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._notifier(dict(self.assignments), persist=False)

    def commit_if_dirty(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        self._notifier(dict(self.assignments), persist=True)

    def on_blur(self, event) -> None:  # type: ignore[override]
        self.commit_if_dirty()
        if hasattr(super(), "on_blur"):
            return super().on_blur(event)
        return None

    def on_key(self, event: events.Key) -> None:  # type: ignore[override]
        if event.key == "f":
            event.stop()
            if hasattr(self.app, "action_focus_tasks"):
                self.app.action_focus_tasks()
            return
        handler = getattr(super(), "on_key", None)
        if handler:
            handler(event)

    def action_next_template(self) -> None:
        self._cycle_template(1)

    def action_previous_template(self) -> None:
        self._cycle_template(-1)

    def action_clear(self) -> None:
        day_key = self._current_day_key()
        if day_key is None:
            return
        self.assignments.pop(day_key, None)
        self._update_row_cell(day_key, "")
        self._mark_dirty()

    def action_cursor_down(self) -> None:
        self._move_cursor(1)

    def action_cursor_up(self) -> None:
        self._move_cursor(-1)

    def action_cursor_bottom(self) -> None:
        self.jump_bottom()

    def jump_top(self) -> None:
        if self.row_count:
            self.cursor_coordinate = (0, self.cursor_column or 0)

    def jump_bottom(self) -> None:
        if self.row_count:
            last_row = self.row_count - 1
            self.cursor_coordinate = (last_row, self.cursor_column or 0)

    def _vim_jump_top(self) -> None:
        self.jump_top()

    def _move_cursor(self, delta: int) -> None:
        if self.row_count == 0:
            return
        current_row = self.cursor_row or 0
        target_row = max(0, min(current_row + delta, self.row_count - 1))
        current_col = self.cursor_column or 0
        self.cursor_coordinate = (target_row, current_col)


class MunazzimApp(App):
    CSS = """
    Screen {
        background: $surface;
    }

    #main-layout {
        layout: horizontal;
        height: 1fr;
        padding: 1;
    }

    #plan-panel {
        # width: 1fr;
        width: auto;
        layout: vertical;
        padding-right: 1;
    }

    #side-panel {
        # width: 1fr;
        width: auto;
        layout: vertical;
        padding-left: 1;
    }

    #side-panel .panel {
        # margin-bottom: 1;
    }

    .panel {
        border: round $accent;
        padding: 1 2;
        background: $boost;
        layout: vertical;
    }

    .panel-title {
        text-style: bold;
        color: $text;
        margin-bottom: 1;
    }

    .panel-help {
        color: $text-muted;
        margin-top: 1;
    }

    #plan-table, #week-table, #task-table {
        height: 1fr;
    }
    """
    TITLE = "Munazzim Daily Planner"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Reload"),
        Binding("n", "next_template", "Next Template"),
        Binding("p", "previous_template", "Previous Template"),
        Binding("t", "pick_template", "Choose Template"),
        Binding("w", "focus_week_planner", "Week Templates"),
        Binding("f", "focus_tasks", "Tasks"),
    Binding("e", "edit_plan", "Open Template"),
    Binding("C", "sync_google_calendar_week", "Sync Calendar (Week)"),
    Binding("Y", "force_sync_google_calendar_today", "Force Sync Calendar (Today)"),
        # Resize layout: ctrl+h/l adjust vertical split (plan/side); ctrl+j/k adjust
        # horizontal split (plan-table / week-table) inside the columns.
        Binding("ctrl+h", "resize_left", "Decrease Plan Width"),
        Binding("ctrl+l", "resize_right", "Increase Plan Width"),
    Binding("ctrl+j", "resize_up", "Decrease Plan Table Height"),
    Binding("ctrl+k", "resize_down", "Increase Plan Table Height"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config_manager = ConfigManager()
        self.config = self.config_manager.load()
        self.templates = TemplateRepository(self.config.planner.template_dir)
        names = self.templates.template_names()
        self.week_assignments = dict(self.config.planner.week_templates)
        self.current_date = date.today()
        self.active_template_name = self._resolve_template_name(self.current_date, names)
        self.prayer_service = PrayerService(self.config, config_manager=self.config_manager)
        self.scheduler = Scheduler(self.config, prayer_service=self.prayer_service)
        self.task_store = TaskStore()
        self.plan_table: PlanTable | None = None
        self.plan_header: Static | None = None
        self.status_line = StatusLine()
        # TaskTable is now shown under the week panel; create only there.
        self.task_table = None
        # Optional Google Tasks service
        if GoogleTasksService is not None:
            try:
                self.google_tasks_service = GoogleTasksService()
            except Exception:
                self.google_tasks_service = None
        else:
            self.google_tasks_service = None
    # Optional Google Calendar service
        if GoogleCalendarService is not None:
            try:
                self.google_calendar_service = GoogleCalendarService()
            except Exception:
                self.google_calendar_service = None
        else:
            self.google_calendar_service = None

        # Create the TaskAssignmentEngine now that google_tasks_service is ready
        self.task_engine = TaskAssignmentEngine(self.templates, self.task_store, external_task_provider=self.google_tasks_service)

        self.selected_google_tasklist = self.config.planner.google_task_list or ""
        # Cache Google Tasks list items to avoid repetitive network calls
        self._google_tasks_cache: dict[str, tuple[float, list]] = {}
        self._google_tasks_cache_ttl = 30.0  # seconds
        # Used to prevent the refresh auto-fallback from overriding a list
        # the user explicitly just selected in this session. This flag is
        # cleared after we attempt to use the selection once.
        self._selected_list_locked: str | None = None

        self.week_panel = WeekPlannerWidget(
            self._on_week_assignments_changed,
            on_toggle=self._on_assignment_toggled,
            on_log=self._on_task_logged,
            on_unlog=self._on_task_unlogged,
            on_external_toggle=self._on_google_task_toggled,
        )
        self._is_refreshing = False
        self._todo_view_active = False
        # Track last time the user navigated the plan manually so auto-highlighting
        # won't steal focus while they're actively moving through the plan.
        self._last_user_navigation: float | None = None
        self._auto_highlight_suppress_secs = 8.0
        # layout ratios: plan & side columns; store initial CSS-fr values
        # default to equal columns (right side half of screen)
        self._plan_column_fr = 1.0
        self._side_column_fr = 1.0
        self._column_total_fr = self._plan_column_fr + self._side_column_fr
        # vertical/fr ratios for main content areas (controls height of plan and week tables)
        self._plan_table_fr = 1.0
        self._week_table_fr = 1.0

    def action_sync_google_calendar_week(self) -> None:
        """Sync the currently assigned week into Google Calendar.

        This creates (or updates) recurring weekly events in the configured
        calendar for each scheduled event within the current week. Recurrences
        are intentionally left open-ended (no UNTIL) so they continue forever.
        """
        # Guard: if calendar service isn't available, show an informative message
        if not getattr(self, "google_calendar_service", None):
            self._display_error("Google Calendar", "Google Calendar service not configured or missing dependencies")
            return

        # Run sync in a background thread so UI remains responsive.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (tests or non-async contexts): run sync inline
            try:
                created_count = self._sync_week_to_google_calendar()
                try:
                    if isinstance(created_count, int):
                        if created_count > 0:
                            self.status_line.update(f"Synced {created_count} events to Google Calendar")
                        else:
                            self.status_line.update("No events created for current week")
                    else:
                        self.status_line.update("Google Calendar sync completed.")
                except Exception:
                    pass
            except Exception as exc:
                self._display_error("Google Calendar", str(exc))
            return

        def _worker() -> None:
            created_count = 0
            try:
                created_count = self._sync_week_to_google_calendar()
            except Exception as exc:  # capture exceptions and schedule UI notification
                loop.call_soon_threadsafe(lambda: self._calendar_sync_finished(False, str(exc)))
            else:
                loop.call_soon_threadsafe(lambda: self._calendar_sync_finished(True, str(created_count)))

        # update status immediately and spawn worker
        try:
            self.status_line.update("Syncing to Google Calendar…")
        except Exception:
            pass
        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def action_force_sync_google_calendar_today(self) -> None:
        """Force sync today's scheduled events to Google Calendar.

        This deletes any Munazzim-created events for the current day and re-creates
        them based on the current local plan. This is a destructive operation for
        Munazzim-created events only; it will not delete unrelated calendar events.
        """
        # Guard: if calendar service isn't available, show an informative message
        if not getattr(self, "google_calendar_service", None):
            self._display_error("Google Calendar", "Google Calendar service not configured or missing dependencies")
            return

        # Run the sync in a background thread like the weekly sync
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop (tests or non-async contexts): run inline
            try:
                created_count = self._sync_today_to_google_calendar(delete_all=True)
                try:
                    if isinstance(created_count, int):
                        if created_count > 0:
                            self.status_line.update(f"Synced {created_count} events to Google Calendar")
                        else:
                            self.status_line.update("No events created for today")
                    else:
                        self.status_line.update("Google Calendar sync completed.")
                except Exception:
                    pass
            except Exception as exc:
                self._display_error("Google Calendar", str(exc))
            return

        def _worker2() -> None:
            created_count = 0
            try:
                created_count = self._sync_today_to_google_calendar(delete_all=True)
            except Exception as exc:  # capture exceptions and schedule UI notification
                loop.call_soon_threadsafe(lambda: self._calendar_sync_finished(False, str(exc)))
            else:
                loop.call_soon_threadsafe(lambda: self._calendar_sync_finished(True, str(created_count)))

        try:
            self.status_line.update("Force syncing today to Google Calendar…")
        except Exception:
            pass
        t = threading.Thread(target=_worker2, daemon=True)
        t.start()

    def _calendar_sync_finished(self, success: bool, message: str | None) -> None:
        """Called on the main loop when background calendar sync finishes."""
        try:
            if success:
                # When message is an integer string, use it as the number of created events
                try:
                    if message is not None and message.isdigit():
                        count = int(message)
                        if count > 0:
                            self.status_line.update(f"Synced {count} events to Google Calendar")
                        else:
                            self.status_line.update("No events created for current week")
                    else:
                        self.status_line.update("Google Calendar sync completed.")
                except Exception:
                    self.status_line.update("Google Calendar sync completed.")
            else:
                self._display_error("Google Calendar", message or "Unknown error during sync")
        except Exception:
            # best-effort only
            pass

    async def _sync_week_to_google_calendar_background(self) -> None:
        """Run the blocking sync function in an executor and update UI after.

        This method is async and runs the blocking function in a threadpool.
        It updates the status_line with progress and shows a modal on error.
        """
        old_status = None
        try:
            try:
                old_status = self.status_line.renderable
            except Exception:
                old_status = None
            # Show inline status while running
            try:
                self.status_line.update("Syncing calendar…")
            except Exception:
                pass
            loop = asyncio.get_running_loop()
            # Run in the default thread pool so we don't block the event loop
            created_count = await loop.run_in_executor(None, self._sync_week_to_google_calendar)
            # Show summary
            try:
                if isinstance(created_count, int):
                    self.status_line.update(f"Synced {created_count} events to Google Calendar")
                else:
                    self.status_line.update("Synced calendar")
            except Exception:
                pass
        except Exception as exc:
            try:
                self._display_error("Google Calendar", str(exc))
            except Exception:
                pass
        finally:
            # restore old status after a short delay to let user see the message
            try:
                await asyncio.sleep(1.5)
                if old_status is not None:
                    try:
                        # status_line.renderable may be complex; rebuild PlanContext if possible
                        self.refresh_plan()
                    except Exception:
                        pass
            except Exception:
                pass

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        self.plan_table = PlanTable()
        self.plan_header = Static("Today's Plan", classes="panel-title")
        plan_panel = Vertical(
            self.plan_header,
            self.status_line,
            self.plan_table,
            id="plan-panel",
            classes="panel",
        )
        # The week panel now hosts the todo box — nothing to hide here.
        side_panel = Vertical(self.week_panel, id="side-panel")
        yield Horizontal(plan_panel, side_panel, id="main-layout")
        yield Footer()

    async def on_mount(self) -> None:
        self.plan_table.add_columns("Start", "End", "Event", "Duration")
        self.plan_table.focus()
        self._show_plan_view()
        # Apply initial layout ratios to make widths/heights relative
        self._apply_layout_ratios()
        self.refresh_plan()

    def refresh_plan(self) -> None:
        if self._is_refreshing:
            return
        self._is_refreshing = True
        try:
            self.week_panel.commit_if_dirty()
            self.current_date = date.today()
            current_day_key = self._weekday_key(self.current_date)
            if self._show_template_error_if_any():
                self.week_panel.set_data(self.week_assignments, self.templates.template_names(), current_day_key)
                return
            names = self.templates.template_names()
            self.week_panel.set_data(self.week_assignments, names, current_day_key)
            if not names:
                self._show_template_setup_hint()
                return
            if self.active_template_name not in names:
                self.active_template_name = self._resolve_template_name(self.current_date, names)
            template = self.templates.get(self.active_template_name)
            template = self.task_engine.annotate_template(template)
            today = self.current_date
            try:
                prayer_schedule = self.prayer_service.get_schedule(today)
                TemplateValidator.validate(template, prayer_schedule)
                plan = self.scheduler.build_plan(
                    template,
                    plan_date=today,
                    prayer_schedule=prayer_schedule,
                )
            except TemplateValidationError as exc:
                self._display_error("Template validation failed", str(exc))
                return
            except Exception as exc:  # pragma: no cover - UI safeguard
                self._display_error("Failed to refresh plan", str(exc))
                return

            assert self.plan_table is not None
            try:
                self.plan_table.clear()
            except Exception:
                # Headless / test contexts may not have DataTable internals
                # initialised. Fail-safe: ignore clear errors and continue.
                pass
            # Ensure plan metadata is cleared
            try:
                self.plan_table._row_metadata = []
            except Exception:
                pass
            # Compute a 'now' context so we can mark the currently-to-do event.
            now = datetime.combine(plan.generated_for, datetime.now().time())
            # Collect any prayer duration overrides found while building the plan
            overrides_found: list[str] = []
            for scheduled in plan.items:
                start = scheduled.start.strftime("%H:%M")
                end = scheduled.end.strftime("%H:%M")
                # Mark the currently ongoing event with a visible pointer
                pointer = "▶" if scheduled.start <= now < scheduled.end else " "
                label = f"{pointer} {scheduled.display_name}"
                # If a prayer override is present for this scheduled event,
                # append a small suffix to the label so the user sees it
                try:
                    from ..models import PrayerEvent
                    if isinstance(scheduled.event, PrayerEvent):
                        p_label = scheduled.event.prayer.strip().lower()
                        try:
                            p_default = getattr(self.config.prayer_settings.durations, p_label)
                        except Exception:
                            p_default = None
                        if p_default is not None and scheduled.event.duration != p_default:
                            label = f"{label} (overridden {format_duration(scheduled.event.duration)})"
                except Exception:
                    pass
                duration = format_duration(scheduled.event.duration)
                try:
                    self.plan_table.add_row(start, end, label, duration)
                except Exception:
                    # DataTable may not have columns in headless contexts; skip UI row add.
                    pass
                # Keep a cheap parallel structure so plan rows map back to
                # the scheduled event object for UI actions (like opening
                # the per-event task file).
                try:
                    self.plan_table._row_metadata.append(scheduled)
                except Exception:
                    pass
                try:
                    from ..models import PrayerEvent
                    if isinstance(scheduled.event, PrayerEvent):
                        p_label = scheduled.event.prayer.strip().lower()
                        try:
                            p_default = getattr(self.config.prayer_settings.durations, p_label)
                        except Exception:
                            p_default = None
                        if p_default is not None and scheduled.event.duration != p_default:
                            overrides_found.append(f"{scheduled.event.prayer}: {format_duration(scheduled.event.duration)}")
                except Exception:
                    pass
            plan_occurrences = self.task_engine.plan_occurrences(plan)
            todos = [
                TodoDisplay(
                    task=occ.label,
                    event=occ.event_label,
                    note=occ.note,
                    task_id=occ.task_id,
                    assignment_id=occ.assignment_id,
                    total=occ.total_occurrences,
                    ordinal=occ.ordinal,
                    checked=occ.checked,
                    # Show checkbox for both recurring and non-recurring tasks
                    toggleable=(occ.assignment_id is not None or occ.total_occurrences is None),
                    last_completed=occ.last_completed,
                )
                for occ in plan_occurrences
            ]
            # If Google Tasks is enabled and a list is selected, show remote tasks
            if self.google_tasks_service:
                list_id = self.selected_google_tasklist
                # Default human-friendly list title; ensure it's defined
                # so later exception/recovery branches can reference it.
                list_title = "Google Tasks"
                # default to first list when not configured
                try:
                    if not list_id:
                        lists = self.google_tasks_service.list_tasklists()
                        if lists:
                            list_id = lists[0].id
                            self.selected_google_tasklist = list_id
                            self.config.planner.google_task_list = list_id
                            self.config_manager.save(self.config)
                except Exception:
                    list_id = None
                if list_id:
                    try:
                        # Use cached results if available to make navigation snappier
                        g_tasks = self._get_cached_tasks(list_id)
                        if g_tasks is None:
                            # No valid cache - fetch synchronously so behavior
                            # remains unchanged for callers relying on immediate
                            # results.
                            g_tasks = self.google_tasks_service.list_tasks(list_id)
                            self._set_cached_tasks(list_id, g_tasks)
                        else:
                            # Kick off an async refresh in background to update
                            # cached entries without blocking UI.
                            try:
                                self._schedule_tasklist_refresh(list_id, event_start_map)
                            except Exception:
                                pass
                        # Find a human-friendly name for the list (title) so we can
                        # show the related event in the 'Event' column instead of
                        # a generic 'Google Tasks'. If the list title is unknown,
                        # fall back to the generic provider label.
                        try:
                            lists = self.google_tasks_service.list_tasklists()
                            list_title = next((l.title for l in lists if l.id == list_id), "Google Tasks")
                        except Exception:
                            list_title = "Google Tasks"
                        # Map event name to scheduled start within current plan
                        event_start_map: dict[str, datetime] = {}
                        for scheduled in plan.items:
                            try:
                                key = scheduled.event.name if hasattr(scheduled.event, "name") else None
                            except Exception:
                                key = None
                            if key:
                                event_start_map.setdefault(key, scheduled.start)

                        g_todos = []
                        for t in g_tasks:
                            # If the task belongs to a list equal to a scheduled
                            # event name for this plan, prefer that event's start
                            # time for the due display (the Tasks API discards
                            # time information, so we reconstruct it from the
                            # plan).
                            due_val = t.due
                            if list_title in event_start_map:
                                # Convert start to RFC3339 UTC string so downstream
                                # behavior is consistent (this string will be parsed
                                # and displayed as local time by the UI formatter).
                                from zoneinfo import ZoneInfo
                                from datetime import timezone as _tz, datetime as _dt

                                start_dt = event_start_map[list_title]
                                tzname = self.config.location.timezone if getattr(self.config, 'location', None) and getattr(self.config.location, 'timezone', None) else None
                                try:
                                    tz = ZoneInfo(tzname) if tzname else _dt.now().astimezone().tzinfo
                                except Exception:
                                    tz = _dt.now().astimezone().tzinfo
                                if start_dt.tzinfo is None:
                                    start_aware = start_dt.replace(tzinfo=tz)
                                else:
                                    start_aware = start_dt
                                due_val = start_aware.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")

                            g_todos.append(
                                TodoDisplay(
                                    task=t.title,
                                    event=list_title,
                                    note=t.notes,
                                    due=due_val,
                                    task_id=t.id,
                                    assignment_id=None,
                                    total=None,
                                    ordinal=None,
                                    checked=(t.status == "completed"),
                                    toggleable=True,
                                    last_completed=None,
                                    provider="google",
                                )
                            )
                        todos = g_todos
                    except Exception as exc:  # pragma: no cover - network faults
                        # If the user explicitly selected this list during this
                        # session, don't override it just because the API reports
                        # an invalid id — notify them and keep the selection.
                        if self._selected_list_locked and self._selected_list_locked == list_id:
                            self._selected_list_locked = None
                            self._display_error("Google Tasks", str(exc))
                            lists = []
                        else:
                            # try to recover by looking up lists again (maybe the id
                            # was removed/renamed). If we can find a new first list
                            # then use it and retry listing tasks; otherwise show an error.
                            try:
                                lists = self.google_tasks_service.list_tasklists()
                            except Exception:
                                self._display_error("Google Tasks", str(exc))
                                lists = []
                        if lists:
                            new_id = lists[0].id
                            if new_id != list_id:
                                self.selected_google_tasklist = new_id
                                self.config.planner.google_task_list = new_id
                                self.config_manager.save(self.config)
                                try:
                                    g_tasks = self.google_tasks_service.list_tasks(new_id)
                                    g_todos = [
                                        TodoDisplay(
                                            task=t.title,
                                            event=list_title,
                                            note=t.notes,
                                            due=t.due,
                                            task_id=t.id,
                                            assignment_id=None,
                                            total=None,
                                            ordinal=None,
                                            checked=(t.status == "completed"),
                                            toggleable=True,
                                            last_completed=None,
                                            provider="google",
                                        )
                                        for t in g_tasks
                                    ]
                                    todos = g_todos
                                except Exception as exc2:
                                    self._display_error("Google Tasks", str(exc2))
                        else:
                            self._display_error("Google Tasks", str(exc))
            # Attempt to highlight current event. In headless/test contexts
            # DataTable.row_count may be zero or unavailable; still call the
            # highlight helper which will delegate to the plan_table.jump_to_row
            # (tests often stub that method).
            if self.plan_table is not None:
                self._highlight_current_event(plan)
            self.status_line.show(self._make_status_context())
            # Send todos to the week planner's Todo box rather than replacing the plan
            self.week_panel.set_todos(todos)
            if overrides_found and self.status_line:
                try:
                    self.status_line.update(f"Prayer overrides applied: {', '.join(overrides_found)}")
                except Exception:
                    pass
            # If the user had focus on the todo view but it now has no items,
            # return focus back to the plan.
            if self._todo_view_active and (self.week_panel.todo_table is None or not self.week_panel.todo_table.has_tasks):
                self._show_plan_view()
        finally:
            self._is_refreshing = False

    def action_refresh(self) -> None:
        self.config = self.config_manager.load()
        self.week_assignments = dict(self.config.planner.week_templates)
        self.templates.reload()
        self.task_engine.refresh()
        self.prayer_service = PrayerService(self.config, config_manager=self.config_manager)
        self.active_template_name = self._resolve_template_name(self.current_date, self.templates.template_names())
        self.refresh_plan()

    def _plan_capacity_text(self) -> str:
        durations = self.config.prayer_settings.durations
        total = durations.fajr + durations.dhuhr + durations.asr + durations.maghrib + durations.isha
        available = timedelta(hours=24) - total
        if available.total_seconds() < 0:
            available = timedelta()
        return format_duration(available)

    def _make_status_context(self) -> PlanContext:
        location = f"{self.config.location.city} {self.config.location.country}".strip()
        day_label = f"Day: {self.current_date.isoformat()}"
        return PlanContext(
            template_name=self.active_template_name,
            location=location,
            provider=self.config.prayer_settings.provider.title(),
            plan_capacity=self._plan_capacity_text(),
            day_label=day_label,
        )

    def _display_error(self, prefix: str, detail: str) -> None:
        # Display error in a popup modal; this avoids writing the error into
        # the plan table itself which is confusing and collides with plan rows.
        try:
            # Use a dedicated modal screen so the user must dismiss it.
            self.push_screen(ErrorScreen(prefix, detail))
        except Exception:
            # This may run in contexts where Textual isn't active (e.g., tests);
            # fallback to plan table-only display and update the status line.
            if self.plan_table:
                self.plan_table.clear()
                self.plan_table.add_row("--", "--", detail, "--")
        # Status bar always shows a short error for visibility
        self.status_line.update(f"[red]{prefix}: {detail}[/red]")

    def _display_sync_errors(self, prefix: str, errors: list[str]) -> None:
        """Display a list of sync errors as a modal; fallback to plan area and status line.

        This shows the full errors, so the user can copy/paste or show them to support.
        """
        try:
            # Attempt to show the error modal using ErrorScreen with multiple messages
            self.push_screen(ErrorScreen(prefix, errors))
        except Exception:
            # Textual not active: fallback to plan table / status line and ensure message is visible.
            try:
                if self.plan_table:
                    self.plan_table.clear()
                    self.plan_table.add_row("--", "--", "\n\n".join(errors), "--")
            except Exception:
                pass
        try:
            self.status_line.update(f"[red]{prefix}: {len(errors)} errors (Press Enter to view)[/red]")
        except Exception:
            pass

    def _show_template_setup_hint(self) -> None:
        hint = "No templates configured. Press 'a' to create one in ~/.config/munazzim/alqawalib/."
        if self.plan_table:
            self.plan_table.clear()
            self.plan_table.add_row("--", "--", hint, "--")
        self.status_line.update(hint)

    def action_next_template(self) -> None:
        self._cycle_template(1)

    def action_previous_template(self) -> None:
        self._cycle_template(-1)

    def action_pick_template(self) -> None:
        names = self.templates.template_names()
        if not names:
            return
        choices = [
            TemplateChoice(name=name, description=self.templates.get(name).description)
            for name in names
        ]
        self.push_screen(TemplatePickerScreen(choices), self._on_template_selected)

    def action_focus_week_planner(self) -> None:
        self.week_panel.focus_table()

    def action_focus_tasks(self) -> None:
        # Toggle focus between today's plan and the Week Todo box
        if self._todo_view_active:
            self._show_plan_view()
            self._todo_view_active = False
            return
        # If the week panel has a todo table and it has items, prefer
        # a Google Tasks list that matches the currently selected plan
        # event; otherwise fall back to the currently configured list
        # or the first available list.
        if self.week_panel.has_todos():
            # Attempt to choose a list that matches the current event
            try:
                if self.google_tasks_service and getattr(self, "plan_table", None) and getattr(self.plan_table, "cursor_row", None) is not None:
                    # Fetch the scheduled event under the cursor to infer the name
                    try:
                        scheduled = list(getattr(self.plan_table, "_row_metadata", []))[self.plan_table.cursor_row]
                    except Exception:
                        scheduled = None
                    event_name = None
                    try:
                        if scheduled and hasattr(scheduled, "event"):
                            event_name = scheduled.event.name if hasattr(scheduled.event, "name") else None
                    except Exception:
                        event_name = None
                    # If we have an event name, look for a matching task list
                    if event_name:
                        try:
                            lists = self.google_tasks_service.list_tasklists()
                            match = next((l for l in lists if l.title == event_name), None)
                            if match:
                                self.selected_google_tasklist = match.id
                                self.config.planner.google_task_list = match.id
                                try:
                                    self.config_manager.save(self.config)
                                except Exception:
                                    pass
                                # Lock the selection to avoid the next refresh changing it
                                self._selected_list_locked = match.id
                        except Exception:
                            # Network or API failure: ignore and fall back
                            pass
            except Exception:
                # Be defensive — falling back to existing list if anything goes wrong
                pass
            # If there's no selected list yet, default to the first list (non-destructive)
            try:
                if not self.selected_google_tasklist and self.google_tasks_service:
                    lists = self.google_tasks_service.list_tasklists()
                    if lists:
                        self.selected_google_tasklist = lists[0].id
                        self.config.planner.google_task_list = lists[0].id
                        try:
                            self.config_manager.save(self.config)
                        except Exception:
                            pass
            except Exception:
                pass
            self._todo_view_active = True
            self.week_panel.focus_todos()
            return
        self.bell()

    def action_focus_plan(self) -> None:
        self._show_plan_view()

    def on_plan_cursor_changed(self, cursor_row: int | None) -> None:
        """Called by the PlanTable when its cursor/selection changes.

        This updates the side-panel todo widget so it reflects the tasks
        associated with the newly focused event. Prefer a Google Tasks list
        that matches the event name; otherwise fall back to the current
        configured list and refresh the whole plan as needed.
        """
        try:
            if not self.plan_table:
                return
            if cursor_row is None:
                return
            # Defensive: keep our stored plan list in sync
            try:
                scheduled = list(getattr(self.plan_table, "_row_metadata", []))[cursor_row]
            except Exception:
                scheduled = None
            if not scheduled:
                # No scheduled event under cursor: refresh the plan so todo
                # box reflects any changes
                self.refresh_plan()
                return
            # Extract event name if present
            event_name = None
            try:
                if hasattr(scheduled, "event"):
                    event_name = scheduled.event.name if hasattr(scheduled.event, "name") else None
            except Exception:
                event_name = None
            # If we have a matching Google Tasks list for this event, select it
            # If user moved recently, avoid doing heavy refreshes here.
            import time
            if self._last_user_navigation is not None and time.time() - self._last_user_navigation < self._auto_highlight_suppress_secs:
                # Show cached tasks if available and return quickly
                if event_name and self.google_tasks_service:
                    cached = self._get_cached_tasks_for_event(event_name)
                    if cached is not None:
                        self.week_panel.set_todos(cached)
                        return
                # No cached data - fall back to light refresh_plan that doesn't block
                return

            if event_name and self.google_tasks_service and not getattr(self, "_selected_list_locked", None):
                try:
                    lists = self._get_cached_tasklists() or self.google_tasks_service.list_tasklists()
                    match = next((l for l in lists if l.title == event_name), None)
                    if match:
                        self.selected_google_tasklist = match.id
                        self.config.planner.google_task_list = match.id
                        try:
                            self.config_manager.save(self.config)
                        except Exception:
                            pass
                        # Fetch tasks for this list and show them in the todo box
                        try:
                            # Use cached tasks where possible, don't block UI.
                            g_tasks = self._get_cached_tasks(match.id)
                            if g_tasks is None:
                                # schedule background refresh and return any cached todos later
                                self._schedule_tasklist_refresh(match.id, None)
                                return
                            # otherwise we have immediate results
                            # Build a map of event name -> scheduled start time
                            event_start_map: dict[str, datetime] = {}
                            for s in getattr(self.plan_table, "_row_metadata", []) or []:
                                try:
                                    key = s.event.name if hasattr(s, "event") and hasattr(s.event, "name") else None
                                except Exception:
                                    key = None
                                if key and key not in event_start_map:
                                    event_start_map[key] = s.start
                            g_todos: list[TodoDisplay] = []
                            from zoneinfo import ZoneInfo
                            from datetime import timezone as _tz, datetime as _dt

                            for t in g_tasks:
                                due_val = t.due
                                if event_name in event_start_map:
                                    tzname = self.config.location.timezone if getattr(self.config, 'location', None) and getattr(self.config.location, 'timezone', None) else None
                                    try:
                                        tz = ZoneInfo(tzname) if tzname else _dt.now().astimezone().tzinfo
                                    except Exception:
                                        tz = _dt.now().astimezone().tzinfo
                                    start_dt = event_start_map[event_name]
                                    if start_dt.tzinfo is None:
                                        start_aware = start_dt.replace(tzinfo=tz)
                                    else:
                                        start_aware = start_dt
                                    due_val = start_aware.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
                                g_todos.append(
                                    TodoDisplay(
                                        task=t.title,
                                        event=event_name,
                                        note=t.notes,
                                        due=due_val,
                                        task_id=t.id,
                                        assignment_id=None,
                                        total=None,
                                        ordinal=None,
                                        checked=(t.status == "completed"),
                                        toggleable=True,
                                        last_completed=None,
                                        provider="google",
                                    )
                                )
                            self.week_panel.set_todos(g_todos)
                            return
                        except Exception:
                            # If any network error occurs, fall through to a full
                            # refresh so the user sees the best-effort view.
                            pass
                except Exception:
                    # Ignore API errors and fallback to refresh
                    pass
            # Default behavior: refresh the plan (which will update the todo box)
            self.refresh_plan()
        except Exception:
            # Never raise from cursor movement
            return

    def action_edit_plan(self) -> None:
        if not self.active_template_name:
            return
        try:
            record = self.templates.record(self.active_template_name)
        except KeyError:
            return
        target = record.path
        try:
            directory = (
                target.parent
                if target and target.parent.exists()
                else self.templates.ensure_user_directory()
            )
        except TemplateParseError as exc:
            self._display_error("Template directory unavailable", str(exc))
            return
        launched = self._launch_editor(directory, target)
        if not launched:
            return
        self.templates.reload()
        self.task_engine.refresh()
        self.active_template_name = self._resolve_template_name(self.current_date, self.templates.template_names())
        self.refresh_plan()

    def action_open_event_tasks(self) -> None:
        """Open the per-event tasks file for the selected event in the plan.

        The file is located at ~/.config/munazzim/tasks/<EventName>. The
        $EDITOR is used to open/edit the file. Once the editor exits we
        refresh tasks and the plan.
        """
        if not self.plan_table or self.plan_table.cursor_row is None:
            self.bell()
            return
        try:
            row_index = int(self.plan_table.cursor_row)
        except Exception:
            self.bell()
            return
        try:
            scheduled = list(getattr(self.plan_table, "_row_metadata", []))[row_index]
        except Exception:
            scheduled = None
        if not scheduled:
            self.bell()
            return
        event_name = scheduled.event.name if hasattr(scheduled, "event") else None
        if not event_name:
            self.bell()
            return
        # New behavior: directly manage cloud tasks rather than editing a
        # local file middleman. Ensure the Google Tasks service is enabled.
        if not self.google_tasks_service:
            self._display_error("Google Tasks", "Google Tasks service not configured or missing dependencies")
            return
        try:
            lists = self.google_tasks_service.list_tasklists()
        except Exception as exc:
            self._display_error("Google Tasks", str(exc))
            return
        # Find an existing list matching the event name or create one
        list_id = None
        for l in lists:
            if l.title == event_name:
                list_id = l.id
                break
        if not list_id:
            try:
                created = self.google_tasks_service.create_tasklist(event_name)
                list_id = created.id
            except Exception as exc:
                self._display_error("Google Tasks", str(exc))
                return
        self.selected_google_tasklist = list_id
        self.config.planner.google_task_list = list_id
        try:
            self.config_manager.save(self.config)
        except Exception:
            pass
        # Ensure the list contains items from the template-derived definitions
        # and use the synchronous helper to attach due times based on the
        # scheduled start if present.
        try:
            self._sync_event_tasks_to_google(event_name, scheduled.start)
        except Exception as exc:
            self._display_error("Google Tasks", str(exc))
        # Focus todo view for this list and refresh
        self._selected_list_locked = list_id
        self._show_task_view()
        self.refresh_plan()

    def action_new_template(self) -> None:
        try:
            directory = self.templates.ensure_user_directory()
        except TemplateParseError as exc:
            self._display_error("Template directory unavailable", str(exc))
            return
        launched = self._launch_editor(directory)
        if not launched:
            return
        self.templates.reload()
        self.task_engine.refresh()
        self.active_template_name = self._resolve_template_name(self.current_date, self.templates.template_names())
        self.refresh_plan()

    # vim-style navigation helpers

    def action_cursor_down(self) -> None:
        if self.plan_table:
            self.plan_table.move_rows(1)

    def action_cursor_up(self) -> None:
        if self.plan_table:
            self.plan_table.move_rows(-1)

    def action_cursor_top(self) -> None:
        if self.plan_table:
            self.plan_table.jump_to_row(0)

    def action_cursor_bottom(self) -> None:
        if self.plan_table:
            self.plan_table.jump_to_row(self.plan_table.row_count - 1)

    def _highlight_current_event(self, plan: DayPlan) -> None:
        if not self.plan_table or not plan.items:
            return
        now = datetime.combine(plan.generated_for, datetime.now().time())
        target_index = 0
        for idx, scheduled in enumerate(plan.items):
            if scheduled.start <= now < scheduled.end:
                target_index = idx
                break
        # If the user has recently navigated the plan, avoid resetting their
        # cursor to the currently active event. Only auto-highight if the
        # last user navigation was long enough ago.
        import time
        if self._last_user_navigation is not None and time.time() - self._last_user_navigation < self._auto_highlight_suppress_secs:
            return
        self.plan_table.jump_to_row(target_index)

    def on_user_navigated(self) -> None:
        """Record that the user interacted with plan navigation so auto-highlighting
        doesn't steal focus immediately after a manual move.
        """
        try:
            import time

            self._last_user_navigation = time.time()
        except Exception:
            self._last_user_navigation = None

    def _get_cached_tasks(self, list_id: str):
        import time

        entry = self._google_tasks_cache.get(list_id)
        if not entry:
            return None
        ts, items = entry
        if time.time() - ts > self._google_tasks_cache_ttl:
            return None
        return items

    def _get_cached_tasklists(self):
        import time

        if not getattr(self, "_google_tasklists_cache", None):
            return None
        ts, items = self._google_tasklists_cache
        if time.time() - ts > self._google_tasks_cache_ttl:
            return None
        return items

    def _get_cached_tasks_for_event(self, event_name: str) -> list[TodoDisplay] | None:
        """Return a cached TodoDisplay list for a Google Tasks list matching event_name."""
        # Find the matching list id from cached list titles
        lists = self._get_cached_tasklists()
        if not lists:
            try:
                lists = self.google_tasks_service.list_tasklists() if self.google_tasks_service else []
                import time as _time
                self._google_tasklists_cache = (_time.time(), lists)
            except Exception:
                lists = []
        match = next((l for l in lists if getattr(l, "title", None) == event_name), None)
        if not match:
            return None
        tasks = self._get_cached_tasks(match.id)
        if tasks is None:
            return None
        # Convert to TodoDisplay objects similar to _schedule_tasklist_refresh
        from zoneinfo import ZoneInfo
        from datetime import timezone as _tz, datetime as _dt
        g_todos = []
        try:
            tzname = self.config.location.timezone if getattr(self.config, 'location', None) and getattr(self.config.location, 'timezone', None) else None
            try:
                tz = ZoneInfo(tzname) if tzname else _dt.now().astimezone().tzinfo
            except Exception:
                tz = _dt.now().astimezone().tzinfo
            for t in tasks:
                due_val = getattr(t, "due", None)
                g_todos.append(
                    TodoDisplay(
                        task=t.title,
                        event=event_name,
                        note=getattr(t, "notes", None),
                        due=due_val,
                        task_id=getattr(t, "id", None),
                        assignment_id=None,
                        total=None,
                        ordinal=None,
                        checked=(getattr(t, "status", None) == "completed"),
                        toggleable=True,
                        last_completed=None,
                        provider="google",
                    )
                )
            return g_todos
        except Exception:
            return None

    def _set_cached_tasks(self, list_id: str, items: list) -> None:
        import time

        self._google_tasks_cache[list_id] = (time.time(), items)

    def _invalidate_tasklist_cache(self, list_id: str | None = None) -> None:
        if list_id is None:
            self._google_tasks_cache.clear()
            return
        self._google_tasks_cache.pop(list_id, None)

    def _schedule_tasklist_refresh(self, list_id: str, event_start_map: dict[str, datetime] | None) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop - refresh synchronously
            try:
                tasks = self.google_tasks_service.list_tasks(list_id)
            except Exception:
                return
            self._set_cached_tasks(list_id, tasks)
            # Build TodoDisplay list
            g_todos = []
            try:
                from zoneinfo import ZoneInfo
                from datetime import timezone as _tz, datetime as _dt

                event_start_map = event_start_map or {}
                tzname = self.config.location.timezone if getattr(self.config, 'location', None) and getattr(self.config.location, 'timezone', None) else None
                try:
                    tz = ZoneInfo(tzname) if tzname else _dt.now().astimezone().tzinfo
                except Exception:
                    tz = _dt.now().astimezone().tzinfo
                for t in tasks:
                    due_val = t.due
                    # If the list maps to an event name and we have a start time, infer the due
                    if t and event_start_map:
                        # Use the first matching event if available
                        title = t.title
                        if title in event_start_map:
                            start_dt = event_start_map[title]
                            if start_dt.tzinfo is None:
                                start_aware = start_dt.replace(tzinfo=tz)
                            else:
                                start_aware = start_dt
                            due_val = start_aware.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
                    g_todos.append(
                        TodoDisplay(
                            task=t.title,
                            event=(t.title if not event_start_map else next(iter(event_start_map.keys()))),
                            note=t.notes,
                            due=due_val,
                            task_id=t.id,
                            assignment_id=None,
                            total=None,
                            ordinal=None,
                            checked=(t.status == "completed"),
                            toggleable=True,
                            last_completed=None,
                            provider="google",
                        )
                    )
                # Update the UI when we have a new result
                self.week_panel.set_todos(g_todos)
            except Exception:
                return
            return

        async def _refresh():
            try:
                tasks = await asyncio.to_thread(self.google_tasks_service.list_tasks, list_id)
            except Exception:
                return
            self._set_cached_tasks(list_id, tasks)
            # Build TodoDisplay list
            g_todos = []
            try:
                from zoneinfo import ZoneInfo
                from datetime import timezone as _tz, datetime as _dt

                event_start_map = event_start_map or {}
                tzname = self.config.location.timezone if getattr(self.config, 'location', None) and getattr(self.config.location, 'timezone', None) else None
                try:
                    tz = ZoneInfo(tzname) if tzname else _dt.now().astimezone().tzinfo
                except Exception:
                    tz = _dt.now().astimezone().tzinfo
                for t in tasks:
                    due_val = t.due
                    if t and event_start_map:
                        title = t.title
                        if title in event_start_map:
                            start_dt = event_start_map[title]
                            if start_dt.tzinfo is None:
                                start_aware = start_dt.replace(tzinfo=tz)
                            else:
                                start_aware = start_dt
                            due_val = start_aware.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
                    g_todos.append(
                        TodoDisplay(
                            task=t.title,
                            event=(t.title if not event_start_map else next(iter(event_start_map.keys()))),
                            note=t.notes,
                            due=due_val,
                            task_id=t.id,
                            assignment_id=None,
                            total=None,
                            ordinal=None,
                            checked=(t.status == "completed"),
                            toggleable=True,
                            last_completed=None,
                            provider="google",
                        )
                    )
                # Update the UI - we are in the loop so it's safe
                try:
                    self.week_panel.set_todos(g_todos)
                except Exception:
                    pass
            except Exception:
                return

        loop.create_task(_refresh())

    # helpers

    def _show_plan_view(self) -> None:
        # Ensure the plan panel is the active/focused view
        self._todo_view_active = False
        if self.plan_table:
            self.plan_table.visible = True
            self.plan_table.focus()
        if self.plan_header:
            self.plan_header.update("Today's Plan")

    def _show_task_view(self) -> None:
        self._todo_view_active = True
        if self.plan_table:
            self.plan_table.visible = True
        if self.week_panel.todo_table:
            self.week_panel.focus_todos()
        if self.plan_header:
            self.plan_header.update("Today's Tasks")

    def _weekday_key(self, day: date) -> str:
        return day.strftime("%A").lower()

    def _resolve_template_name(self, day: date, available: list[str]) -> str:
        if not available:
            return ""
        key = self._weekday_key(day)
        assigned = self.week_assignments.get(key)
        if assigned in available:
            return assigned
        if self.config.planner.default_template in available:
            return self.config.planner.default_template
        return available[0]

    def _set_active_template(self, name: str, *, persist: bool) -> None:
        if name not in self.templates.template_names():
            return
        self.active_template_name = name
        if persist:
            day_key = self._weekday_key(self.current_date)
            self.week_assignments[day_key] = name
            self.config.planner.week_templates = dict(self.week_assignments)
            self.config_manager.save(self.config)
        self.refresh_plan()

    def _cycle_template(self, delta: int) -> None:
        names = self.templates.template_names()
        if not names or self.active_template_name not in names:
            return
        idx = names.index(self.active_template_name)
        self._set_active_template(names[(idx + delta) % len(names)], persist=True)

    def _on_template_selected(self, selection: str | None) -> None:
        if selection:
            self._set_active_template(selection, persist=True)

    def _on_week_assignments_changed(self, assignments: dict[str, str], persist: bool) -> None:
        self.week_assignments = dict(assignments)
        if persist:
            self.config.planner.week_templates = dict(assignments)
            self.config_manager.save(self.config)
            self.active_template_name = self._resolve_template_name(
                self.current_date,
                self.templates.template_names(),
            )
            self.refresh_plan()

    def _on_task_logged(self, task_id: str) -> None:
        self.task_engine.complete_task(task_id)
        self.refresh_plan()

    def _on_task_unlogged(self, task_id: str) -> None:
        self.task_engine.unlog_task(task_id)
        self.refresh_plan()

    def _on_assignment_toggled(self, assignment_id: str, completed: bool) -> None:
        self.task_engine.toggle_assignment(assignment_id, completed)
        self.refresh_plan()

    # Google Tasks integration -------------------------------------------------
    def action_select_task_list(self) -> None:
        """Open a picker to select which Google Task list to display in the Todo box."""
        if not self.google_tasks_service:
            self.bell()
            return
        try:
            lists = self.google_tasks_service.list_tasklists()
        except Exception as exc:  # pragma: no cover - interactive failures
            self._display_error("Google Tasks", str(exc))
            return
        choices = [TaskListChoice(id=l.id, title=l.title) for l in lists]
        if not choices:
            self._display_error("Google Tasks", "No task lists available")
            return
        # push a selection screen and set the selected list in the callback
        self.push_screen(TaskListPickerScreen(choices), self._on_tasklist_selected)

    def _on_tasklist_selected(self, selection: str | None) -> None:
        if not selection:
            return
        self.selected_google_tasklist = selection
        # Persist to config
        self.config.planner.google_task_list = selection
        self.config_manager.save(self.config)
        # Lock the selected list for the next refresh so an immediately
        # triggered refresh won't auto-select another list on network error.
        self._selected_list_locked = selection
        self.refresh_plan()

    def _on_google_task_toggled(self, task_id: str | None, completed: bool) -> None:
        if not self.google_tasks_service:
            self.bell()
            return
        list_id = self.selected_google_tasklist
        if not list_id:
            # If user didn't select a list, default to first available
            lists = self.google_tasks_service.list_tasklists()
            if not lists:
                self.bell()
                return
            list_id = lists[0].id
            self.selected_google_tasklist = list_id
            self.config.planner.google_task_list = list_id
            self.config_manager.save(self.config)
        if not task_id:
            return
        status = "completed" if completed else "needsAction"
        try:
            self.google_tasks_service.update_task(list_id, task_id, status=status)
        except Exception as exc:
            self._display_error("Google Tasks", str(exc))
            return
        self.refresh_plan()

    def action_add_task(self) -> None:
        if not self.google_tasks_service:
            self.bell()
            return
        # Ask the user for a task title (with due date/recurrence) and create it in the selected list
        try:
            # prefer our richer TaskEditScreen when available
            from .screens import TaskEditScreen

            self.push_screen(TaskEditScreen("Create task"), self._on_new_task)
        except Exception:
            # Fallback to single-line entry
            self.push_screen(TextEntryScreen("New task title:"), self._on_new_task_title)

    def _on_new_task_title(self, title: str | None) -> None:
        if not title:
            return
        list_id = self.selected_google_tasklist
        try:
            if not list_id:
                lists = self.google_tasks_service.list_tasklists()
                if not lists:
                    self._display_error("Google Tasks", "No task lists available")
                    return
                list_id = lists[0].id
                self.selected_google_tasklist = list_id
                self.config.planner.google_task_list = list_id
                self.config_manager.save(self.config)
            due_val = self._resolve_due_for_list(list_id, None)
            self.google_tasks_service.create_task(list_id, title=title, due=due_val)
            self._invalidate_tasklist_cache(list_id)
        except Exception as exc:
            self._display_error("Google Tasks", str(exc))
            return
        self.refresh_plan()

    def _on_new_task(self, data: dict | None) -> None:
        if not data or "title" not in data or not data["title"]:
            return
        list_id = self.selected_google_tasklist
        try:
            if not list_id:
                lists = self.google_tasks_service.list_tasklists()
                if not lists:
                    self._display_error("Google Tasks", "No task lists available")
                    return
                list_id = lists[0].id
                self.selected_google_tasklist = list_id
                self.config.planner.google_task_list = list_id
                self.config_manager.save(self.config)
            # Compute due from event occurrence for the list (if available)
            due_val = self._resolve_due_for_list(list_id, None)
            self.google_tasks_service.create_task(list_id, title=data["title"], due=due_val, notes=data.get("notes"))
            self._invalidate_tasklist_cache(list_id)
        except Exception as exc:
            self._display_error("Google Tasks", str(exc))
            return
        self.refresh_plan()

    def action_delete_task(self) -> None:
        # Delete the selected google task if present
        if not self.google_tasks_service:
            self.bell()
            return
        if not self.week_panel.todo_table or self.week_panel.todo_table.cursor_row is None:
            self.bell()
            return
        todo = self.week_panel.todo_table._row_metadata[self.week_panel.todo_table.cursor_row]
        if not todo or not todo.provider or todo.provider != "google":
            self.bell()
            return
        list_id = self.selected_google_tasklist or (self.google_tasks_service.list_tasklists() or [None])[0]
        if not list_id:
            self.bell()
            return
        try:
            self.google_tasks_service.delete_task(list_id, todo.task_id or "")
            self._invalidate_tasklist_cache(list_id)
        except Exception as exc:
            self._display_error("Google Tasks", str(exc))
            return
        self.refresh_plan()

    def action_edit_task(self) -> None:
        # Edit a selected Google task's details
        if not self.google_tasks_service:
            self.bell()
            return
        if not self.week_panel.todo_table or self.week_panel.todo_table.cursor_row is None:
            self.bell()
            return
        todo = self.week_panel.todo_table._row_metadata[self.week_panel.todo_table.cursor_row]
        if not todo or not todo.provider or todo.provider != "google":
            self.bell()
            return
        # Prepopulate a task editor with current fields
        try:
            from .screens import TaskEditScreen

            self.push_screen(
                TaskEditScreen("Edit task", title=todo.task, notes=getattr(todo, "note", None)),
                lambda data: self._on_task_edited(todo.task_id, data),
            )
        except Exception:
            self.bell()

    def _on_task_edited(self, task_id: str | None, data: dict | None) -> None:
        if not data or not task_id:
            return
        list_id = self.selected_google_tasklist or (self.google_tasks_service.list_tasklists() or [None])[0]
        if not list_id:
            self.bell()
            return
        try:
            kwargs: dict = {}
            if "title" in data and data["title"]:
                kwargs["title"] = data["title"]
            # Auto-set due based on the list's related event occurrence
            due_val = self._resolve_due_for_list(list_id, None)
            if due_val is not None:
                kwargs["due"] = due_val
            if "notes" in data and data["notes"]:
                kwargs["notes"] = data["notes"]
            if kwargs:
                self.google_tasks_service.update_task(list_id, task_id, **kwargs)
                self._invalidate_tasklist_cache(list_id)
        except Exception as exc:
            self._display_error("Google Tasks", str(exc))
            return
        self.refresh_plan()

    def _sync_event_tasks_to_google(self, event_name: str, scheduled_start: "datetime.datetime" | None = None) -> None:
        """Create or sync a Google Tasks list for the event.

        The behavior is simple: ensure a task list exists that matches the
        event name. For each bullet in the event file, ensure a task exists in
        that list — create missing tasks. Do not delete extra tasks.
        """
        if not self.google_tasks_service:
            return
        # parse event file using the task engine helper
        defs = self.task_engine.parse_event_file(event_name)
        if not defs:
            return
        # Find an existing list with the same title or create one
        try:
            lists = self.google_tasks_service.list_tasklists()
        except Exception as exc:
            raise
        list_id: str | None = None
        for l in lists:
            if l.title == event_name:
                list_id = l.id
                break
        if not list_id:
            # Create one
            created = self.google_tasks_service.create_tasklist(event_name)
            list_id = created.id
        # List current tasks
        remote = self.google_tasks_service.list_tasks(list_id)
        existing_titles = {t.title for t in remote}
        # Determine an appropriate scheduled start to attach to created tasks.
        # If we were given an explicit scheduled_start and it is today, use it.
        # Otherwise search this week's assigned templates for an occurrence
        # of the named event and prefer the nearest future occurrence. If
        # none found, notify the user that the tasks may be hanging/unlinked
        # to the current schedule and proceed without attaching a due time.
        resolved_start: "datetime.datetime" | None = None
        try:
            today = self.current_date
        except Exception:
            from datetime import date as _date

            today = _date.today()

        if scheduled_start is not None and getattr(scheduled_start, "date", lambda: None)() == today:
            resolved_start = scheduled_start
        else:
            # Search the week's templates for an event with this name
            from datetime import datetime as _dt, timedelta as _td

            occurrences: list[_dt] = []
            now = _dt.now()
            for day_name in WEEKDAY_ORDER:
                tpl_name = self.week_assignments.get(day_name)
                if not tpl_name:
                    continue
                try:
                    tpl = self.templates.get(tpl_name)
                except Exception:
                    continue
                # compute the date for this weekday in the current week
                try:
                    target_index = WEEKDAY_ORDER.index(day_name)
                    # Compute Monday of the current week then offset by target_index
                    start_of_week = today - _td(days=today.weekday())
                    target_date = start_of_week + _td(days=target_index)
                except Exception:
                    continue
                try:
                    prayer_schedule = self.prayer_service.get_schedule(target_date)
                    plan = self.scheduler.build_plan(tpl, plan_date=target_date, prayer_schedule=prayer_schedule)
                except Exception:
                    continue
                for sched in plan.items:
                    try:
                        ev_name = sched.event.name if hasattr(sched.event, "name") else None
                    except Exception:
                        ev_name = None
                    if ev_name == event_name:
                        occurrences.append(sched.start)
            if occurrences:
                # prefer the nearest future occurrence, otherwise closest overall
                future = [o for o in occurrences if o >= now]
                if future:
                    resolved_start = min(future)
                else:
                    resolved_start = min(occurrences, key=lambda o: abs((o - now).total_seconds()))
            else:
                # Notify user that no occurrence found this week — likely a hanging todo
                try:
                    self._display_error(
                        "Tasks Sync",
                        f"No scheduled occurrence for '{event_name}' found this week; tasks may be unrelated to the ongoing schedule.",
                    )
                except Exception:
                    # Fallback: update status line if UI not mounted
                    try:
                        self.status_line.update(
                            f"[yellow]No scheduled occurrence for '{event_name}' found this week; tasks may be hanging.[/yellow]"
                        )
                    except Exception:
                        pass
                resolved_start = None
        # Create missing tasks
        for defn in defs:
            if defn.label in existing_titles:
                continue
            notes = defn.note or None
            # Persist count info in notes when available
            if defn.total_occurrences is not None:
                notes = (notes or "") + f" (count: {defn.total_occurrences})"
            try:
                due_val: str | None = None
                if resolved_start is not None:
                    # Use RFC3339 timestamp. If the scheduled time is naive,
                    # attach the configured timezone (or system default) before
                    # converting to UTC. Google Tasks expects an RFC3339-style
                    # datetime string (e.g. '2025-12-31T10:00:00Z').
                    from zoneinfo import ZoneInfo
                    from datetime import timezone as _tz, datetime as _dt

                    tzname = self.config.location.timezone if getattr(self.config, 'location', None) and getattr(self.config.location, 'timezone', None) else None
                    try:
                        tz = ZoneInfo(tzname) if tzname else _dt.now().astimezone().tzinfo
                    except Exception:
                        tz = _dt.now().astimezone().tzinfo

                    # If naive, attach the configured timezone
                    if resolved_start.tzinfo is None:
                        scheduled_aware = resolved_start.replace(tzinfo=tz)
                    else:
                        scheduled_aware = resolved_start
                    # Convert to UTC and use 'Z' suffix
                    due_val = scheduled_aware.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
                self.google_tasks_service.create_task(list_id, title=defn.label, notes=notes, due=due_val)
            except Exception as exc:
                # non-fatal: continue but surface error at the end
                raise
        # Invalidate cache after creating missing tasks so next view refresh
        # picks up the new items.
        try:
            self._invalidate_tasklist_cache(list_id)
        except Exception:
            pass

    def _sync_week_to_google_calendar(self) -> int:
        """Sync scheduled events for the current week to Google Calendar.

        This only syncs events for the current week (Monday..Sunday) and
        creates recurring weekly events (no end date) for each scheduled
        occurrence. Existing events created by Munazzim (identified via
        extendedProperties.private) are not duplicated.
        """
        # If no google_calendar_service is available, skip.
        if not getattr(self, "google_calendar_service", None):
            return 0

        # Compute fingerprint of current week templates + template contents (still compute for record)
        try:
            fingerprint = self._compute_templates_fingerprint()
        except Exception:
            fingerprint = None
    # Determine calendar to use: config.planner.google_calendar else 'Munazzim'
        cal_name = self.config.planner.google_calendar or "Munazzim"
        # Compute timezone once up front so create_calendar can receive it
        tzname = self.config.location.timezone if getattr(self.config, 'location', None) and getattr(self.config.location, 'timezone', None) else None
        from zoneinfo import ZoneInfo
        try:
            tz = ZoneInfo(tzname) if tzname else datetime.now().astimezone().tzinfo
        except Exception:
            tz = datetime.now().astimezone().tzinfo
        try:
            cals = self.google_calendar_service.list_calendars()
        except Exception as exc:
            raise
        cal_id: str | None = None
        # APPLY: Always apply the nuke+create behavior for the week.
        stats = self._sync_week_to_google_calendar_apply()
        # If there were errors, surface an error modal; keep behavior best-effort.
        if stats.get("errors"):
            try:
                # Show the full details in a modal for copy/share
                self._display_sync_errors("Google Calendar Sync Errors", stats.get("errors", []))
            except Exception:
                try:
                    self._display_error("Google Calendar", f"{len(stats['errors'])} errors during sync. See logs for details.")
                except Exception:
                    pass
        created = stats.get("created_count", 0)
        # Save fingerprint if present
        try:
            if fingerprint:
                self._write_last_fingerprint(fingerprint)
        except Exception:
            pass
        return created
        # NOTE: end of _sync_week_to_google_calendar

    def _find_event_occurrence(self, event_name: str, scheduled_start: "datetime.datetime" | None = None) -> "datetime.datetime" | None:
        """Find a scheduled occurrence (datetime) for an event name.

        Preference order:
        1. If scheduled_start is provided and is today, return it.
        2. Search the week's templates to find scheduled occurrences and prefer the nearest future occurrence, otherwise the closest occurrence.
        3. Return None if no occurrence found.
        """
        from datetime import datetime as _dt, timedelta as _td
        from zoneinfo import ZoneInfo

        # Prefer explicit scheduled_start when it matches today's date
        try:
            today = self.current_date
        except Exception:
            today = date.today()
        if scheduled_start is not None and getattr(scheduled_start, "date", lambda: None)() == today:
            return scheduled_start

        occurrences: list[_dt] = []
        now = _dt.now()
        for day_name in WEEKDAY_ORDER:
            tpl_name = self.week_assignments.get(day_name)
            if not tpl_name:
                continue
            try:
                tpl = self.templates.get(tpl_name)
            except Exception:
                continue
            try:
                target_index = WEEKDAY_ORDER.index(day_name)
                start_of_week = today - _td(days=today.weekday())
                target_date = start_of_week + _td(days=target_index)
            except Exception:
                continue
            try:
                prayer_schedule = self.prayer_service.get_schedule(target_date)
                plan = self.scheduler.build_plan(tpl, plan_date=target_date, prayer_schedule=prayer_schedule)
            except Exception:
                continue
            for sched in plan.items:
                try:
                    ev_name = sched.event.name if hasattr(sched.event, "name") else None
                except Exception:
                    ev_name = None
                if ev_name == event_name:
                    occurrences.append(sched.start)
        if not occurrences:
            return None
        future = [o for o in occurrences if o >= now]
        if future:
            return min(future)
        return min(occurrences, key=lambda o: abs((o - now).total_seconds()))

    def _resolve_due_for_list(self, list_id: str | None, scheduled_start: "datetime.datetime" | None = None) -> str | None:
        """Return an RFC3339 UTC string for the due time associated with the
        given list (if it maps to a scheduled event in this week's plan), or
        None if no mapping can be found.
        """
        if not list_id or not getattr(self, "google_tasks_service", None):
            return None
        try:
            lists = self.google_tasks_service.list_tasklists()
        except Exception:
            return None
        list_title = next((l.title for l in lists if l.id == list_id), None)
        if not list_title:
            return None
        occ = self._find_event_occurrence(list_title, scheduled_start)
        if not occ:
            return None
        # Convert to UTC/Z-based RFC3339
        from zoneinfo import ZoneInfo
        from datetime import timezone as _tz, datetime as _dt

        tzname = self.config.location.timezone if getattr(self.config, 'location', None) and getattr(self.config.location, 'timezone', None) else None
        try:
            tz = ZoneInfo(tzname) if tzname else _dt.now().astimezone().tzinfo
        except Exception:
            tz = _dt.now().astimezone().tzinfo
        if occ.tzinfo is None:
            occ = occ.replace(tzinfo=tz)
        try:
            return occ.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            return None
        # NOTE: All the sync logic is handled in _sync_week_to_google_calendar_debug()
        # The code that follows used to be the sync logic; it is kept here only
        # for backward compatibility and is not executed.

        # Helper to map weekday name to BYDAY style codes used in RRULE
        weekday_map = {
            "monday": "MO",
            "tuesday": "TU",
            "wednesday": "WE",
            "thursday": "TH",
            "friday": "FR",
            "saturday": "SA",
            "sunday": "SU",
        }

        now_dt = datetime.now()
        # We'll list existing events for the whole week to dedupe
        start_of_week = None
        from datetime import timedelta as _td
        try:
            today = self.current_date
        except Exception:
            from datetime import date as _date

            today = _date.today()
        # Resolve start_of_week (date) as Monday of the current week
        start_of_week_date = today - _td(days=today.weekday())
        # Time range for a calendar list query: start at 00:00 of Monday to end 23:59:59 Sunday
        from zoneinfo import ZoneInfo
        from datetime import timezone as _tz

        tzname = self.config.location.timezone if getattr(self.config, 'location', None) and getattr(self.config.location, 'timezone', None) else None
        try:
            tz = ZoneInfo(tzname) if tzname else datetime.now().astimezone().tzinfo
        except Exception:
            tz = datetime.now().astimezone().tzinfo
        start_dt = datetime.combine(start_of_week_date, datetime.min.time()).replace(tzinfo=tz)
        end_dt = (start_dt + _td(days=7)).replace(tzinfo=tz)
        time_min = start_dt.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
        time_max = end_dt.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")

        try:
            existing = self.google_calendar_service.list_events(cal_id, timeMin=time_min, timeMax=time_max)
        except Exception:
            existing = []

        # Build a set of existing signatures for deduping: eventname|byday|time
        existing_signatures = set()
        for ev in existing:
            try:
                props = getattr(ev, 'extended_properties', None) or {}
                private = (props.get("private") or {})
                sig = private.get("munazzim_signature")
                if sig:
                    existing_signatures.add(sig)
            except Exception:
                continue

        # Iterate the week, generate plans and create events
        for day_name in WEEKDAY_ORDER:
            tpl_name = self.week_assignments.get(day_name)
            # If no assignment exists for this day, fall back to the active
            # template name (or configured default) so the whole week is
            # synced based on the day-specific active/default template
            if not tpl_name:
                tpl_name = self.active_template_name or self.config.planner.default_template
            if not tpl_name:
                continue
            try:
                tpl = self.templates.get(tpl_name)
            except Exception:
                continue
            # Compute target_date for this day_name inside the current week
            try:
                target_index = WEEKDAY_ORDER.index(day_name)
                start_of_week_date = today - _td(days=today.weekday())
                target_date = start_of_week_date + _td(days=target_index)
            except Exception:
                continue
            try:
                prayer_schedule = self.prayer_service.get_schedule(target_date)
                plan = self.scheduler.build_plan(tpl, plan_date=target_date, prayer_schedule=prayer_schedule)
            except Exception:
                continue
            for sched in plan.items:
                try:
                    ev_name = sched.display_name
                except Exception:
                    continue
                byday = weekday_map.get(day_name, None)
                if not byday:
                    continue
                # signature for dedupe: name|byday|localtime
                local_time = sched.start.replace(tzinfo=tz) if sched.start.tzinfo is None else sched.start.astimezone(tz)
                sig = f"{ev_name}|{byday}|{local_time.strftime('%H:%M')}"
                # Do not filter by existing remote events — we always want to include
                # the local plan in the payloads so higher levels can decide what
                # to apply. This avoids hiding existing events from debugging
                # and ensures the planned list is complete.
                # Build event payload
                start_iso = local_time.isoformat()
                # compute end by combining scheduled end if present or use duration
                if getattr(sched, "end", None):
                    end_local = sched.end.replace(tzinfo=tz) if sched.end.tzinfo is None else sched.end.astimezone(tz)
                else:
                    end_local = (local_time + sched.event.duration)
                    if end_local.tzinfo is None:
                        end_local = end_local.replace(tzinfo=tz)
                end_iso = end_local.isoformat()
                rrule = f"RRULE:FREQ=WEEKLY;BYDAY={byday}"
                payload = {
                    "summary": ev_name,
                    "start": {"dateTime": start_iso},
                    "end": {"dateTime": end_iso},
                    "recurrence": [rrule],
                    "extendedProperties": {"private": {"munazzim_signature": sig, "munazzim_event": getattr(sched.event, 'name', '')}},
                }
                try:
                    # Always attempt to create the event as we drive sync from the
                    # local fingerprint; remote is considered authoritative read-only
                    # and we don't attempt to smart-sync.
                    self.google_calendar_service.create_event(cal_id, payload)
                    created_count += 1
                except Exception:
                    continue
        # Save the fingerprint so subsequent runs can be skipped unless local change
        try:
            if fingerprint:
                self._write_last_fingerprint(fingerprint)
        except Exception:
            pass
        return created_count

    def _sync_today_to_google_calendar(self, delete_all: bool = True) -> int:
        """Force-synchronize today's events (delete and replace).

        By default only Munazzim-created events are deleted. If delete_all is True,
        all events in the day range (for the configured calendar) will be deleted.
        Returns the number of created events.
        """
        if not getattr(self, "google_calendar_service", None):
            return 0
        # Compute today's date and call the daily apply
        try:
            today = self.current_date
        except Exception:
            from datetime import date as _date
            today = _date.today()
        stats = self._sync_day_to_google_calendar_apply(today, delete_all=delete_all)
        if stats.get("errors"):
            try:
                self._display_sync_errors("Google Calendar Sync Errors", stats.get("errors", []))
            except Exception:
                pass
        return stats.get("created_count", 0)

    def _compute_templates_fingerprint(self) -> str | None:
        """Compute a deterministic fingerprint for the set of templates used for weekly sync.

        The fingerprint includes the `week_assignments` mapping and the content
        of the template source files when available; otherwise it includes template
        structure stringification.
        """
        # Gather the week assignment names
        week_assignments = dict(self.week_assignments or {})
        # Ensure we also include active and default template names
        if self.active_template_name:
            week_assignments.setdefault("__active__", self.active_template_name)
        if self.config.planner.default_template:
            week_assignments.setdefault("__default__", self.config.planner.default_template)
        # Build a dict of template -> content or file-hash
        templates_map: dict[str, str] = {}
        for name in set(week_assignments.values()):
            if not name:
                continue
            try:
                rec = self.templates.record(name)
                if rec.path and rec.path.exists():
                    data = rec.path.read_bytes()
                    templates_map[name] = hashlib.sha256(data).hexdigest()
                else:
                    # Fallback: use template representation
                    templates_map[name] = hashlib.sha256(str(rec.template).encode("utf-8")).hexdigest()
            except Exception:
                templates_map[name] = ""

        payload = {
            "week_assignments": week_assignments,
            "templates": templates_map,
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _state_file_path(self) -> Path:
        return Path.home() / ".config" / "munazzim" / "google_calendar_sync_state.json"

    def _read_last_fingerprint(self) -> str | None:
        path = self._state_file_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("last_fingerprint")
        except Exception:
            return None

    def _write_last_fingerprint(self, fingerprint: str) -> None:
        path = self._state_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"last_fingerprint": fingerprint, "last_synced": datetime.utcnow().isoformat()}
        path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")

    def _collect_weekly_event_payloads(self) -> list[dict]:
        """Return the planned event payloads that would be created by sync.

        This is a dry-run helper for debugging: it computes the same
        event payloads as the sync function but does not call the Google API.
        """
        payloads: list[dict] = []
        if not getattr(self, "google_calendar_service", None):
            return payloads
        cal_name = self.config.planner.google_calendar or "Munazzim"
        try:
            cals = self.google_calendar_service.list_calendars()
        except Exception:
            # Can't list calendars; gracefully return empty list
            return payloads
        cal_id: str | None = None
        for c in cals:
            if c.summary == cal_name:
                cal_id = c.id
                break
        if not cal_id:
            # No calendar yet: still compute the proposed payloads for the week
            cal_id = None

        # Build existing signatures for dedupe so dry run matches actual behavior
        try:
            now_dt = datetime.now()
            from datetime import timedelta as _td
            tzname = self.config.location.timezone if getattr(self.config, 'location', None) and getattr(self.config.location, 'timezone', None) else None
            from zoneinfo import ZoneInfo
            try:
                tz = ZoneInfo(tzname) if tzname else datetime.now().astimezone().tzinfo
            except Exception:
                tz = datetime.now().astimezone().tzinfo
            from datetime import timezone as _tz
            try:
                today = self.current_date
            except Exception:
                from datetime import date as _date

                today = _date.today()
            from datetime import timezone as _tz
            start_of_week_date = today - _td(days=today.weekday())
            start_dt = datetime.combine(start_of_week_date, datetime.min.time()).replace(tzinfo=tz)
            end_dt = (start_dt + _td(days=7)).replace(tzinfo=tz)
            time_min = start_dt.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
            time_max = end_dt.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
            existing = self.google_calendar_service.list_events(cal_id or "primary", timeMin=time_min, timeMax=time_max)
        except Exception:
            existing = []
        existing_signatures = set()
        for ev in existing:
            try:
                props = getattr(ev, 'extended_properties', None) or {}
                private = (props.get("private") or {})
                sig = private.get("munazzim_signature")
                if sig:
                    existing_signatures.add(sig)
            except Exception:
                continue

        # Reuse the same logic as the sync: compute payloads without creating them
        weekday_map = {
            "monday": "MO",
            "tuesday": "TU",
            "wednesday": "WE",
            "thursday": "TH",
            "friday": "FR",
            "saturday": "SA",
            "sunday": "SU",
        }
        from datetime import timedelta as _td
        try:
            today = self.current_date
        except Exception:
            from datetime import date as _date
            today = _date.today()
        for day_name in WEEKDAY_ORDER:
            tpl_name = self.week_assignments.get(day_name)
            if not tpl_name:
                tpl_name = self.active_template_name or self.config.planner.default_template
            if not tpl_name:
                continue
            try:
                tpl = self.templates.get(tpl_name)
            except Exception:
                continue
            try:
                target_index = WEEKDAY_ORDER.index(day_name)
                start_of_week_date = today - _td(days=today.weekday())
                target_date = start_of_week_date + _td(days=target_index)
            except Exception:
                continue
            try:
                prayer_schedule = self.prayer_service.get_schedule(target_date)
                plan = self.scheduler.build_plan(tpl, plan_date=target_date, prayer_schedule=prayer_schedule)
            except Exception:
                continue
            for sched in plan.items:
                try:
                    ev_name = sched.display_name
                except Exception:
                    continue
                byday = weekday_map.get(day_name, None)
                if not byday:
                    continue
                local_time = sched.start.replace(tzinfo=tz) if sched.start.tzinfo is None else sched.start.astimezone(tz)
                sig = f"{ev_name}|{byday}|{local_time.strftime('%H:%M')}"
                # Do not filter planned daily payloads based on remote existence
                start_iso = local_time.isoformat()
                if getattr(sched, "end", None):
                    end_local = sched.end.replace(tzinfo=tz) if sched.end.tzinfo is None else sched.end.astimezone(tz)
                else:
                    end_local = (local_time + sched.event.duration)
                    if end_local.tzinfo is None:
                        end_local = end_local.replace(tzinfo=tz)
                end_iso = end_local.isoformat()
                rrule = f"RRULE:FREQ=WEEKLY;BYDAY={byday}"
                payload = {
                    "summary": ev_name,
                    "start": {"dateTime": start_iso, "timeZone": (tzname or 'UTC')},
                    "end": {"dateTime": end_iso, "timeZone": (tzname or 'UTC')},
                    "recurrence": [rrule],
                    "extendedProperties": {"private": {"munazzim_signature": sig, "munazzim_event": getattr(sched.event, 'name', '')}},
                }
                payloads.append(payload)
        return payloads

    def _collect_daily_event_payloads(self, target_date) -> list[dict]:
        """Return the planned event payloads that would be created for a single day.

        This mirrors `_collect_weekly_event_payloads` but computes payloads only for
        the provided `target_date`.
        """
        payloads: list[dict] = []
        if not getattr(self, "google_calendar_service", None):
            return payloads
        cal_name = self.config.planner.google_calendar or "Munazzim"
        try:
            cals = self.google_calendar_service.list_calendars()
        except Exception:
            return payloads
        cal_id: str | None = None
        for c in cals:
            if c.summary == cal_name:
                cal_id = c.id
                break
        if not cal_id:
            cal_id = None

        # Build existing signatures for dedupe so dry run matches actual behavior
        try:
            tzname = self.config.location.timezone if getattr(self.config, 'location', None) and getattr(self.config.location, 'timezone', None) else None
            from zoneinfo import ZoneInfo
            from datetime import timezone as _tz
            try:
                tz = ZoneInfo(tzname) if tzname else datetime.now().astimezone().tzinfo
            except Exception:
                tz = datetime.now().astimezone().tzinfo
            from datetime import timezone as _tz
            start_dt = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=tz)
            end_dt = (start_dt + timedelta(days=1)).replace(tzinfo=tz)
            time_min = start_dt.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
            time_max = end_dt.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
            existing = self.google_calendar_service.list_events(cal_id or "primary", timeMin=time_min, timeMax=time_max)
        except Exception:
            existing = []
        existing_signatures = set()
        for ev in existing:
            try:
                props = getattr(ev, 'extended_properties', None) or {}
                private = (props.get("private") or {})
                sig = private.get("munazzim_signature")
                if sig:
                    existing_signatures.add(sig)
            except Exception:
                continue

        # Build payloads for the given date only
        weekday_map = {
            "monday": "MO",
            "tuesday": "TU",
            "wednesday": "WE",
            "thursday": "TH",
            "friday": "FR",
            "saturday": "SA",
            "sunday": "SU",
        }
        try:
            today = target_date
        except Exception:
            from datetime import date as _date
            today = _date.today()
        # For each day, but we only care about the one day passed in, compare
        day_name = today.strftime('%A').lower()
        tpl_name = self.week_assignments.get(day_name)
        if not tpl_name:
            tpl_name = self.active_template_name or self.config.planner.default_template
        if not tpl_name:
            return payloads
        try:
            tpl = self.templates.get(tpl_name)
        except Exception:
            return payloads
        try:
            prayer_schedule = self.prayer_service.get_schedule(today)
            plan = self.scheduler.build_plan(tpl, plan_date=today, prayer_schedule=prayer_schedule)
        except Exception:
            return payloads
        for sched in plan.items:
            try:
                ev_name = sched.display_name
            except Exception:
                continue
            byday = weekday_map.get(day_name, None)
            if not byday:
                continue
            local_time = sched.start
            if local_time.tzinfo is None:
                try:
                    tzname = self.config.location.timezone if getattr(self.config, 'location', None) and getattr(self.config.location, 'timezone', None) else None
                    tz = ZoneInfo(tzname) if tzname else datetime.now().astimezone().tzinfo
                except Exception:
                    tz = datetime.now().astimezone().tzinfo
                local_time = local_time.replace(tzinfo=tz)
            sig = f"{ev_name}|{byday}|{local_time.strftime('%H:%M')}"
            start_iso = local_time.isoformat()
            if getattr(sched, "end", None):
                end_local = sched.end.replace(tzinfo=tz) if sched.end.tzinfo is None else sched.end.astimezone(tz)
            else:
                end_local = (local_time + sched.event.duration)
                if end_local.tzinfo is None:
                    end_local = end_local.replace(tzinfo=tz)
            end_iso = end_local.isoformat()
            rrule = f"RRULE:FREQ=WEEKLY;BYDAY={byday}"
            payload = {
                "summary": ev_name,
                "start": {"dateTime": start_iso, "timeZone": (tzname or 'UTC')},
                "end": {"dateTime": end_iso, "timeZone": (tzname or 'UTC')},
                "recurrence": [rrule],
                "extendedProperties": {"private": {"munazzim_signature": sig, "munazzim_event": getattr(sched.event, 'name', '')}},
            }
            payloads.append(payload)
        return payloads

    def _sync_week_to_google_calendar_debug(self) -> dict:
        """A diagnostic sync that returns details about planned/created events and any failures.

        This mirrors `_sync_week_to_google_calendar` but collects statistics and returns
        a dict with keys: planned_count, created_count, skipped_count, errors (list).
        """
        results = {
            "planned_count": 0,
            "created_count": 0,
            "skipped_count": 0,
            "errors": [],
        }
        if not getattr(self, "google_calendar_service", None):
            return results
        cal_name = self.config.planner.google_calendar or "Munazzim"
        # Determine timezone for calendar creation and event payloads
        tzname = self.config.location.timezone if getattr(self.config, 'location', None) and getattr(self.config.location, 'timezone', None) else None
        from zoneinfo import ZoneInfo
        try:
            tz = ZoneInfo(tzname) if tzname else datetime.now().astimezone().tzinfo
        except Exception:
            tz = datetime.now().astimezone().tzinfo
        try:
            cals = self.google_calendar_service.list_calendars()
        except Exception as exc:
            results["errors"].append(str(exc))
            return results
        cal_id: str | None = None
        for c in cals:
            if c.summary == cal_name:
                cal_id = c.id
                break
        # If no calendar exists, we will create one (or attempt to) later.
        created_cal = None
        if not cal_id:
            try:
                try:
                    created_cal = self.google_calendar_service.create_calendar(cal_name, time_zone=(tzname or 'UTC'))
                except TypeError:
                    # Older service or fake may not accept tz param; call without it
                    created_cal = self.google_calendar_service.create_calendar(cal_name)
                cal_id = created_cal.id
            except Exception as exc:
                results["errors"].append(str(exc))
                return results

    # Do not fetch remote events for debug; always show the complete local plan

    # Build payloads and compute which events *would* be created as in normal sync
        weekday_map = {
            "monday": "MO",
            "tuesday": "TU",
            "wednesday": "WE",
            "thursday": "TH",
            "friday": "FR",
            "saturday": "SA",
            "sunday": "SU",
        }
        from datetime import timedelta as _td
        try:
            today = self.current_date
        except Exception:
            from datetime import date as _date
            today = _date.today()
        for day_name in WEEKDAY_ORDER:
            tpl_name = self.week_assignments.get(day_name)
            if not tpl_name:
                tpl_name = self.active_template_name or self.config.planner.default_template
            if not tpl_name:
                continue
            try:
                tpl = self.templates.get(tpl_name)
            except Exception as exc:
                results["errors"].append(str(exc))
                continue
            try:
                target_index = WEEKDAY_ORDER.index(day_name)
                start_of_week_date = today - _td(days=today.weekday())
                target_date = start_of_week_date + _td(days=target_index)
            except Exception as exc:
                results["errors"].append(str(exc))
                continue
            try:
                prayer_schedule = self.prayer_service.get_schedule(target_date)
                plan = self.scheduler.build_plan(tpl, plan_date=target_date, prayer_schedule=prayer_schedule)
            except Exception as exc:
                results["errors"].append(str(exc))
                continue
            for sched in plan.items:
                try:
                    ev_name = sched.display_name
                except Exception as exc:
                    results["errors"].append(str(exc))
                    continue
                byday = weekday_map.get(day_name, None)
                if not byday:
                    continue
                local_time = sched.start.replace(tzinfo=tz) if sched.start.tzinfo is None else sched.start.astimezone(tz)
                sig = f"{ev_name}|{byday}|{local_time.strftime('%H:%M')}"
                results["planned_count"] += 1
                start_iso = local_time.isoformat()
                if getattr(sched, "end", None):
                    end_local = sched.end.replace(tzinfo=tz) if sched.end.tzinfo is None else sched.end.astimezone(tz)
                else:
                    end_local = (local_time + sched.event.duration)
                    if end_local.tzinfo is None:
                        end_local = end_local.replace(tzinfo=tz)
                end_iso = end_local.isoformat()
                rrule = f"RRULE:FREQ=WEEKLY;BYDAY={byday}"
                payload = {
                    "summary": ev_name,
                    "start": {"dateTime": start_iso, "timeZone": (tzname or 'UTC')},
                    "end": {"dateTime": end_iso, "timeZone": (tzname or 'UTC')},
                    "recurrence": [rrule],
                    "extendedProperties": {"private": {"munazzim_signature": sig, "munazzim_event": getattr(sched.event, 'name', '')}},
                }
                # For the debug variant we do not actually create events; instead
                # just count how many would be created. This keeps the debug
                # operation non-destructive for diagnosis.
                results["would_create_count"] = results.get("would_create_count", 0) + 1
        return results
    def _sync_week_to_google_calendar_apply(self) -> dict:
        """Apply the sync and actually create events, returning stats and errors."""
        results = {"planned_count": 0, "created_count": 0, "skipped_count": 0, "deleted_count": 0, "errors": []}
        if not getattr(self, "google_calendar_service", None):
            return results
        # Compute timezone for calendar creation
        tzname = self.config.location.timezone if getattr(self.config, 'location', None) and getattr(self.config.location, 'timezone', None) else None
        from zoneinfo import ZoneInfo
        try:
            tz = ZoneInfo(tzname) if tzname else datetime.now().astimezone().tzinfo
        except Exception:
            tz = datetime.now().astimezone().tzinfo
        # Create calendar if needed and then create events
        cal_name = self.config.planner.google_calendar or "Munazzim"
        try:
            cals = self.google_calendar_service.list_calendars()
        except Exception as exc:
            results["errors"].append(str(exc))
            return results
        cal_id = None
        for c in cals:
            if c.summary == cal_name:
                cal_id = c.id
                break
        if not cal_id:
            try:
                try:
                    created_cal = self.google_calendar_service.create_calendar(cal_name, time_zone=(tzname or 'UTC'))
                except TypeError:
                    created_cal = self.google_calendar_service.create_calendar(cal_name)
                cal_id = created_cal.id
            except Exception as exc:
                results["errors"].append(str(exc))
                return results
    # Collect existing events for the week and delete them all (nuke the week)
        try:
            from datetime import timedelta as _td
            try:
                today = self.current_date
            except Exception:
                from datetime import date as _date
                today = _date.today()
            tzname = self.config.location.timezone if getattr(self.config, 'location', None) and getattr(self.config.location, 'timezone', None) else None
            from zoneinfo import ZoneInfo
            from datetime import timezone as _tz
            try:
                tz = ZoneInfo(tzname) if tzname else datetime.now().astimezone().tzinfo
            except Exception:
                tz = datetime.now().astimezone().tzinfo
            start_of_week_date = today - _td(days=today.weekday())
            start_dt = datetime.combine(start_of_week_date, datetime.min.time()).replace(tzinfo=tz)
            end_dt = (start_dt + _td(days=7)).replace(tzinfo=tz)
            time_min = start_dt.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
            time_max = end_dt.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
            existing = self.google_calendar_service.list_events(cal_id or "primary", timeMin=time_min, timeMax=time_max)
        except Exception as exc:
            existing = []
        # Map to event ids list and also collect generic existing ids for deletion
        existing_ids: list[str] = []
        existing_signatures_map: dict[str, list[str]] = {}
        for ev in existing:
            try:
                event_id = getattr(ev, "recurring_event_id", None) or getattr(ev, "recurringEventId", None) or getattr(ev, "id", None)
                props = getattr(ev, 'extended_properties', None) or {}
                private = (props.get("private") or {})
                sig = private.get("munazzim_signature")
                if sig and event_id:
                    existing_signatures_map.setdefault(sig, []).append(event_id)
                if event_id:
                    existing_ids.append(event_id)
            except Exception:
                continue
        # Now compute planned payloads and decide which events to delete/create
        # We use a two-pass approach: compute planned signatures, delete stale events
        # (those that were created by Munazzim but are no longer planned), then
        # create any remaining planned events that are missing.
        planned_payloads = self._collect_weekly_event_payloads()
        planned_signatures_map: dict[str, dict] = {}
        for p in planned_payloads:
            try:
                props = p.get("extendedProperties", {})
                private = props.get("private", {})
                sig = private.get("munazzim_signature")
            except Exception:
                sig = None
            if sig:
                planned_signatures_map[sig] = p

        existing_signatures = set(existing_signatures_map.keys())

        # Nuke the week's events first by deleting all existing event ids in the range
    # DEBUG-LOG (removed)
        for eid in existing_ids:
            if not eid:
                continue
            try:
                self.google_calendar_service.delete_event(cal_id, eid)
                results["deleted_count"] += 1
            except Exception as exc:
                results["errors"].append(str(exc))
                continue

        # Create any planned events that are not present in the existing set
        # create planned events by iterating days from Monday -> Sunday; within
        # each day, create events from first to last (scheduler already preserves order)
        weekday_map = {
            "monday": "MO",
            "tuesday": "TU",
            "wednesday": "WE",
            "thursday": "TH",
            "friday": "FR",
            "saturday": "SA",
            "sunday": "SU",
        }
        from datetime import timedelta as _td
        try:
            today = self.current_date
        except Exception:
            from datetime import date as _date
            today = _date.today()
        for day_name in WEEKDAY_ORDER:
            tpl_name = self.week_assignments.get(day_name)
            if not tpl_name:
                tpl_name = self.active_template_name or self.config.planner.default_template
            if not tpl_name:
                continue
            try:
                tpl = self.templates.get(tpl_name)
            except Exception as exc:
                results["errors"].append(str(exc))
                continue
            try:
                target_index = WEEKDAY_ORDER.index(day_name)
                start_of_week_date = today - _td(days=today.weekday())
                target_date = start_of_week_date + _td(days=target_index)
            except Exception as exc:
                results["errors"].append(str(exc))
                continue
            try:
                prayer_schedule = self.prayer_service.get_schedule(target_date)
                plan = self.scheduler.build_plan(tpl, plan_date=target_date, prayer_schedule=prayer_schedule)
            except Exception as exc:
                results["errors"].append(str(exc))
                continue
            for sched in plan.items:
                try:
                    ev_name = sched.display_name
                except Exception as exc:
                    results["errors"].append(str(exc))
                    continue
                byday = weekday_map.get(day_name, None)
                if not byday:
                    continue
                local_time = sched.start.replace(tzinfo=tz) if sched.start.tzinfo is None else sched.start.astimezone(tz)
                sig = f"{ev_name}|{byday}|{local_time.strftime('%H:%M')}"
                start_iso = local_time.isoformat()
                if getattr(sched, "end", None):
                    end_local = sched.end.replace(tzinfo=tz) if sched.end.tzinfo is None else sched.end.astimezone(tz)
                else:
                    end_local = (local_time + sched.event.duration)
                    if end_local.tzinfo is None:
                        end_local = end_local.replace(tzinfo=tz)
                end_iso = end_local.isoformat()
                rrule = f"RRULE:FREQ=WEEKLY;BYDAY={byday}"
                payload = {
                    "summary": ev_name,
                    "start": {"dateTime": start_iso, "timeZone": (tzname or 'UTC')},
                    "end": {"dateTime": end_iso, "timeZone": (tzname or 'UTC')},
                    "recurrence": [rrule],
                    "extendedProperties": {"private": {"munazzim_signature": sig, "munazzim_event": getattr(sched.event, 'name', '')}},
                }
                results["planned_count"] += 1
                try:
                    self.google_calendar_service.create_event(cal_id, payload)
                    results["created_count"] += 1
                except Exception as exc:
                    results["errors"].append(str(exc))
                    continue
        return results
    def _sync_day_to_google_calendar_apply(self, target_date, delete_all: bool = False) -> dict:
        """Apply the daily sync: delete and create events for a single day.

        If delete_all is True, delete all events in the day's range; otherwise
        only delete Munazzim-created events identified by our extended properties.
        """
        results = {"planned_count": 0, "created_count": 0, "skipped_count": 0, "deleted_count": 0, "errors": []}
        if not getattr(self, "google_calendar_service", None):
            return results
        tzname = self.config.location.timezone if getattr(self.config, 'location', None) and getattr(self.config.location, 'timezone', None) else None
        from zoneinfo import ZoneInfo
        try:
            tz = ZoneInfo(tzname) if tzname else datetime.now().astimezone().tzinfo
        except Exception:
            tz = datetime.now().astimezone().tzinfo

        cal_name = self.config.planner.google_calendar or "Munazzim"
        try:
            cals = self.google_calendar_service.list_calendars()
        except Exception as exc:
            results["errors"].append(str(exc))
            return results
        cal_id = None
        for c in cals:
            if c.summary == cal_name:
                cal_id = c.id
                break
        if not cal_id:
            try:
                try:
                    created_cal = self.google_calendar_service.create_calendar(cal_name, time_zone=(tzname or 'UTC'))
                except TypeError:
                    created_cal = self.google_calendar_service.create_calendar(cal_name)
                cal_id = created_cal.id
            except Exception as exc:
                results["errors"].append(str(exc))
                return results

        # Determine times for the day range
        from datetime import timezone as _tz
        start_dt = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=tz)
        end_dt = (start_dt + timedelta(days=1)).replace(tzinfo=tz)
        time_min = start_dt.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
        time_max = end_dt.astimezone(_tz.utc).isoformat().replace("+00:00", "Z")
        try:
            existing = self.google_calendar_service.list_events(cal_id or "primary", timeMin=time_min, timeMax=time_max)
        except Exception as exc:
            results["errors"].append(str(exc))
            existing = []

        existing_signatures_map: dict[str, list[str]] = {}
        existing_ids = []
        for ev in existing:
            try:
                props = getattr(ev, 'extended_properties', None) or {}
                private = (props.get("private") or {})
                sig = private.get("munazzim_signature")
                event_id = getattr(ev, "recurring_event_id", None) or getattr(ev, "recurringEventId", None) or getattr(ev, "id", None)
                if sig:
                    existing_signatures_map.setdefault(sig, []).append(event_id)
                # Track all ids in case of delete_all
                if event_id:
                    existing_ids.append(event_id)
            except Exception:
                continue

        # Compute planned payloads for the date
        planned_payloads = self._collect_daily_event_payloads(target_date)
        planned_signatures = set()
        planned_signatures_map: dict[str, dict] = {}
        for p in planned_payloads:
            try:
                props = p.get("extendedProperties", {})
                private = props.get("private", {})
                sig = private.get("munazzim_signature")
            except Exception:
                sig = None
            if sig:
                planned_signatures.add(sig)
                planned_signatures_map[sig] = p

        # Nuke all existing events in the day range to ensure a clean slate
        for eid in existing_ids:
            try:
                self.google_calendar_service.delete_event(cal_id, eid)
                results["deleted_count"] += 1
            except Exception as exc:
                results["errors"].append(str(exc))
                continue

        # Create events for the day, ordered by their start times
        # Build a list of (sig, payload, start_dt) so we can sort and create
        ordered: list[tuple[str, dict, datetime]] = []
        for sig, payload in planned_signatures_map.items():
            try:
                start_iso = payload.get("start", {}).get("dateTime")
                if start_iso and isinstance(start_iso, str) and start_iso.endswith("Z"):
                    start_iso = start_iso.replace("Z", "+00:00")
                start_dt = datetime.fromisoformat(start_iso) if start_iso else None
            except Exception:
                start_dt = None
            if start_dt is None:
                # fallback to now if we can't parse; it will still create
                start_dt = datetime.now()
            ordered.append((sig, payload, start_dt))
        ordered.sort(key=lambda t: t[2])
        for sig, payload, _ in ordered:
            results["planned_count"] += 1
            try:
                self.google_calendar_service.create_event(cal_id, payload)
                results["created_count"] += 1
            except Exception as exc:
                results["errors"].append(str(exc))
                continue
        return results


    def _editor_command(self) -> list[str] | None:
        raw = (
            os.environ.get("MUNAZZIM_EDITOR")
            or os.environ.get("VISUAL")
            or os.environ.get("EDITOR")
        )
        if not raw:
            for candidate in ("nvim", "vim", "hx", "helix", "nano", "vi"):
                if shutil.which(candidate):
                    raw = candidate
                    break
        if not raw:
            return None
        return shlex.split(raw)

    def _launch_editor(self, directory: Path, target: Path | None = None) -> bool:
        command = self._editor_command()
        if not command:
            self._display_error("No editor configured", "Set $EDITOR/$VISUAL or install vim/helix/nano.")
            return False
        run_command = list(command)
        if target is not None:
            run_command.append(str(target))
        try:
            with self.suspend():
                result = subprocess.run(run_command, cwd=str(directory), check=False)
        except FileNotFoundError:
            self._display_error("Editor not found", command[0])
            return False
        if result.returncode != 0:
            self._display_error("Editor exited with errors", f"Command returned {result.returncode}")
            return False
        return True

    def _show_template_error_if_any(self) -> bool:
        errors = self.templates.errors()
        if not errors:
            return False
        error = errors[0]
        screen = TemplateErrorScreen(error.path, error.message)
        self.push_screen(screen, lambda result, path=error.path: self._on_template_error_closed(result, path))
        return True

    def _on_template_error_closed(self, open_requested: bool | None, path: Path | None) -> None:
        if open_requested and path:
            directory = path.parent if path.parent.exists() else Path.home()
            self._launch_editor(directory, path)
        self.templates.reload()
        self.task_engine.refresh()
        self.refresh_plan()

    # Layout helper functions -------------------------------------------------
    def _apply_layout_ratios(self) -> None:
        """Apply the stored column and row ratios to the Textual layout.

        This updates the width values for the `#plan-panel` and `#side-panel`
        and the height values for the `#plan-table` and `#week-table`.
        """
        # Update column widths
        try:
            plan_panel = self.query_one("#plan-panel")
            side_panel = self.query_one("#side-panel")
        except Exception:
            return
        plan_panel.styles.width = f"{self._plan_column_fr}fr"
        side_panel.styles.width = f"{self._side_column_fr}fr"

        # Apply vertical ratio to the main table widgets; guard queries
        try:
            plan_table = self.query_one("#plan-table")
            week_table = self.query_one("#week-table")
            plan_table.styles.height = f"{self._plan_table_fr}fr"
            week_table.styles.height = f"{self._week_table_fr}fr"
        except Exception:
            # Not mounted yet — do nothing
            pass

    def _adjust_horizontal_ratio(self, delta: float) -> None:
        """Adjust column ratios while keeping the total constant.

        delta is applied to the plan column; the side column is adjusted so the
        sum equals _column_total_fr. Keep both columns >= 0.5fr.
        """
        min_fr = 0.5
        plan_new = max(min_fr, self._plan_column_fr + delta)
        # clamp such that side >= min_fr
        side_new = max(min_fr, self._column_total_fr - plan_new)
        # If the sum changed because of clamping, re-normalize plan
        if side_new + plan_new != self._column_total_fr:
            # recompute plan such that sum == total after min clamp
            plan_new = max(min_fr, self._column_total_fr - side_new)
        self._plan_column_fr = plan_new
        self._side_column_fr = side_new
        self._apply_layout_ratios()

    def action_set_side_half(self) -> None:
        """Set the right panel to occupy half the screen (equal fr to plan)."""
        half = self._column_total_fr / 2.0
        self._plan_column_fr = half
        self._side_column_fr = half
        self._apply_layout_ratios()

    def _adjust_vertical_ratio(self, delta: float) -> None:
        """Adjust the relative heights of the plan and week tables.

        delta is applied to the plan table; week table is adjusted so both are
        at least 0.4fr.
        """
        min_fr = 0.4
        plan_new = max(min_fr, self._plan_table_fr + delta)
        week_new = max(min_fr, (self._plan_table_fr + self._week_table_fr) - plan_new)
        if plan_new + week_new != (self._plan_table_fr + self._week_table_fr):
            plan_new = max(min_fr, (self._plan_table_fr + self._week_table_fr) - week_new)
        self._plan_table_fr = plan_new
        self._week_table_fr = week_new
        self._apply_layout_ratios()

    # Actions triggered by user key bindings ---------------------------------
    def action_resize_left(self) -> None:
        self._adjust_horizontal_ratio(-0.2)

    def action_resize_right(self) -> None:
        self._adjust_horizontal_ratio(0.2)

    def action_resize_up(self) -> None:
        self._adjust_vertical_ratio(-0.2)

    def action_resize_down(self) -> None:
        self._adjust_vertical_ratio(0.2)
