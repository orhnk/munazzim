from __future__ import annotations

from dataclasses import dataclass
import tempfile
from datetime import datetime
from pathlib import Path
import os
from pathlib import Path
from typing import Iterable, Sequence

from textual.app import ComposeResult  # type: ignore[import]
from textual.binding import Binding  # type: ignore[import]
from textual.containers import Vertical  # type: ignore[import]
from textual.screen import ModalScreen, Screen  # type: ignore[import]
from textual.widgets import DataTable, ListItem, ListView, Static  # type: ignore[import]


@dataclass(slots=True)
class TemplateChoice:
    name: str
    description: str


@dataclass(slots=True)
class TaskListChoice:
    id: str
    title: str


class TemplatePickerScreen(ModalScreen[str | None]):
    """Modal list for selecting a template."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("j", "cursor_down", "Next", show=False),
        Binding("k", "cursor_up", "Previous", show=False),
    Binding("g", "cursor_top", "Top", show=False),
    Binding("G", "cursor_bottom", "Bottom", show=False),
    Binding("a", "new_template", "New Template"),
    ]

    def __init__(self, templates: Sequence[TemplateChoice]) -> None:
        super().__init__()
        self.templates = templates
        self.list_view: ListView | None = None
        self._id_to_template: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        title = Static("Select a template", classes="dialog-title")
        items = []
        self._id_to_template.clear()
        for idx, choice in enumerate(self.templates):
            body = Static(
                f"{choice.name}\n[dim]{choice.description or 'No description'}[/dim]",
                classes="dialog-item",
            )
            safe_id = f"template-{idx}"
            self._id_to_template[safe_id] = choice.name
            items.append(ListItem(body, id=safe_id))
        self.list_view = ListView(*items, id="template-picker-list")
        help_text = Static(
            "Enter = Select • Esc = Cancel • j/k move • gg/G start/end",
            classes="dialog-help",
        )
        yield Vertical(title, self.list_view, help_text, id="template-picker")

    def action_new_template(self) -> None:
        """Open the new-template editor scoped to the picker.

        This calls the app-level new template action and closes the picker.
        """
        try:
            if getattr(self, "app", None) and hasattr(self.app, "action_new_template"):
                self.app.action_new_template()
        except Exception:
            pass
        finally:
            try:
                self.dismiss(None)
            except Exception:
                pass


class TaskListPickerScreen(ModalScreen[str | None]):
    """Modal list for selecting a Google task list (or other task provider)."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("j", "cursor_down", "Next", show=False),
        Binding("k", "cursor_up", "Previous", show=False),
    ]

    def __init__(self, lists: Iterable[TaskListChoice]) -> None:
        super().__init__()
        self.lists = list(lists)
        self.list_view: ListView | None = None
        self._id_to_list: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        title = Static("Select task list", classes="dialog-title")
        items = []
        self._id_to_list.clear()
        for idx, choice in enumerate(self.lists):
            body = Static(f"{choice.title}\n[dim]{choice.id}[/dim]", classes="dialog-item")
            safe_id = f"tasklist-{idx}"
            self._id_to_list[safe_id] = choice.id
            items.append(ListItem(body, id=safe_id))
        self.list_view = ListView(*items, id="task-list-picker-list")
        help_text = Static("Enter = Select • Esc = Cancel • j/k move", classes="dialog-help")
        yield Vertical(title, self.list_view, help_text, id="task-list-picker")

    def on_mount(self) -> None:
        if self.list_view and self.list_view.children:
            self.list_view.index = 0

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not event.item.id:
            return
        tasklist_id = self._id_to_list.get(event.item.id, event.item.id)
        self.dismiss(tasklist_id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TextEntryScreen(ModalScreen[str | None]):
    """Simple modal that asks the user for a text value; returns the entry on OK."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, placeholder: str = "") -> None:
        super().__init__()
        self.prompt = prompt
        self.placeholder = placeholder
        self._input_id = "text-entry-input"

    def compose(self) -> ComposeResult:
        title = Static(self.prompt, classes="dialog-title")
        # Textual's Input widget is only used during runtime; using Static as fallback
        from textual.widgets import Input  # type: ignore[import]

        inp = Input(placeholder=self.placeholder, id=self._input_id)
        help_text = Static("Enter = OK • Esc = Cancel", classes="dialog-help")
        yield Vertical(title, inp, help_text, id="text-entry")

    def on_mount(self) -> None:
        try:
            self.query_one(f"#{self._input_id}").focus()
        except Exception:
            pass

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: "Input.Submitted") -> None:  # type: ignore[name-defined]
        self.dismiss(event.value)


class TaskEditScreen(ModalScreen[dict | None]):
    """Modal that asks the user for a task title, due date and recurrence string.

    The recurrence field should be a single RRULE string, for example
    "RRULE:FREQ=DAILY;COUNT=5". The caller will receive a dictionary with
    keys: title, due, recurrence, notes.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, title: str | None = None, notes: str | None = None) -> None:
        super().__init__()
        self.prompt = prompt
        self._title = title or ""
        self._notes = notes or ""
        self._title_id = "task-edit-title"
        self._notes_id = "task-edit-notes"

    def compose(self) -> ComposeResult:
        from textual.widgets import Input  # type: ignore[import]

        title = Static(self.prompt, classes="dialog-title")
        inp_title = Input(placeholder="Task title", id=self._title_id, value=self._title)
        inp_notes = Input(placeholder="Notes (optional)", id=self._notes_id, value=self._notes)
        help_text = Static("Enter in Title to save • Esc = Cancel", classes="dialog-help")
        yield Vertical(title, inp_title, inp_notes, help_text, id="task-edit-dialog")

    def on_mount(self) -> None:
        try:
            self.query_one(f"#{self._title_id}").focus()
        except Exception:
            pass

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_input_submitted(self, event: "Input.Submitted") -> None:  # type: ignore[name-defined]
        # Gather values from all inputs and return a dictionary
        try:
            t = self.query_one(f"#{self._title_id}").value
            n = self.query_one(f"#{self._notes_id}").value
        except Exception:
            self.dismiss(None)
            return
        out = {"title": t, "notes": n or None}
        self.dismiss(out)


class TemplateErrorScreen(ModalScreen[bool | None]):
    """Modal dialog to surface template parse errors and let the user open the file."""

    BINDINGS = [
        Binding("enter", "open_file", "Open file"),
    ]

    def __init__(self, path: Path | None, message: str) -> None:
        super().__init__()
        self.path = path
        self.message = message

    def compose(self) -> ComposeResult:
        body = (
            "Got this error:\n\n"
            + self.message
            + "\n\npress enter to open the file."
        )
        filename = str(self.path) if self.path else "Unknown file"
        yield Vertical(
            Static("Template Error", classes="dialog-title"),
            Static(f"File: {filename}", classes="dialog-item", markup=False),
            Static(body, classes="dialog-item", markup=False),
        )

    def action_open_file(self) -> None:
        self.dismiss(True)


class ErrorScreen(ModalScreen[None]):
    """Modal screen to display a brief error message with dismiss action.

    Shows a title (prefix) and the error detail. Dismiss with Enter or
    Esc.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "dismiss", "OK"),
        Binding("c", "copy", "Copy Error"),
    ]

    def __init__(self, prefix: str, message: str | list[str]) -> None:
        super().__init__()
        self.prefix = prefix
        # Normalize message(s) to a list for uniform rendering
        if isinstance(message, list):
            self.messages = list(message)
        else:
            self.messages = [str(message)]

    def compose(self) -> ComposeResult:
        title = Static(f"{self.prefix}", classes="dialog-title")
        help_text = Static("Enter = OK • Esc = Cancel", classes="dialog-help")
        items = []
        for idx, msg in enumerate(self.messages, start=1):
            items.append(Static(msg, classes="dialog-item", markup=False))
        yield Vertical(title, *items, help_text, id="error-dialog")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_dismiss(self) -> None:
        self.dismiss(None)

    def action_copy(self) -> None:
        """Copy the error messages to the system clipboard if available, else save to a file.

        The function attempts to use the pyperclip module; if that's unavailable,
        it writes a timestamped file to ~/.local/state/munazzim/ and updates the
        status area if the app is running.
        """
        payload = "\n\n".join(self.messages)
        # Try clipboard (pyperclip) first
        try:
            import pyperclip  # type: ignore

            try:
                pyperclip.copy(payload)
                copied = True
                msg = "Copied errors to clipboard"
            except Exception:
                copied = False
                msg = "Failed to copy to clipboard"
        except Exception:
            copied = False
            msg = "clipboard not available; saving to file"

        if not copied:
            # fallback: write to a file in ~/.local/state/munazzim
            try:
                home = Path.home()
                state_dir = home / ".local" / "state" / "munazzim"
                state_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
                fname = state_dir / f"errors-{ts}.txt"
                with fname.open("w", encoding="utf-8") as fh:
                    fh.write(payload)
                msg = f"Saved errors to {fname}"
            except Exception:
                msg = "Failed to save errors to file"

        # Attempt to update status via the App if present; otherwise ignore
        try:
            if getattr(self, "app", None) and getattr(self.app, "status_line", None):
                self.app.status_line.update(msg)
        except Exception:
            pass


    # SyncErrorsScreen consolidated into ErrorScreen. Use ErrorScreen with a list of messages.


class WeekPlannerScreen(Screen[dict[str, str] | None]):
    """Screen that lets the user assign templates to weekdays."""

    BINDINGS = [
        Binding("right", "next_template", "Next Template"),
        Binding("left", "previous_template", "Previous Template"),
        Binding("delete", "clear", "Clear Day"),
        Binding("s", "save", "Save"),
        Binding("escape", "cancel", "Cancel"),
        Binding("l", "next_template", "Next Template", show=False),
        Binding("h", "previous_template", "Previous Template", show=False),
        Binding("j", "cursor_down", "Next Day", show=False),
        Binding("k", "cursor_up", "Previous Day", show=False),
    Binding("g", "cursor_top", "First Day", show=False),
        Binding("G", "cursor_bottom", "Last Day", show=False),
    ]

    def __init__(self, assignments: dict[str, str], template_names: Sequence[str]) -> None:
        super().__init__()
        self.assignments = dict(assignments)
        self.template_names = list(template_names)
        self.table = DataTable(zebra_stripes=True)
        self._day_row_keys: dict[str, object] = {}
        self._template_column_key: object | None = None
        self.day_order = [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ]

    def compose(self) -> ComposeResult:
        self._day_row_keys.clear()
        column_keys = self.table.add_columns("Day", "Template")
        if len(column_keys) >= 2:
            self._template_column_key = column_keys[1]
        else:
            self._template_column_key = None
        for day in self.day_order:
            display = day.capitalize()
            template = self.assignments.get(day, "")
            row_key = self.table.add_row(display, template)
            self._day_row_keys[day] = row_key
        self.table.cursor_type = "row"
        self.table.focus()
        if self.day_order:
            self.table.cursor_coordinate = (0, 0)
        help_text = Static(
            "←/→ or h/l change template • j/k move • gg/G jump • Delete clear • S save • Esc cancel",
            classes="dialog-help",
        )
        yield Vertical(Static("Weekly Template Planner", classes="dialog-title"), self.table, help_text)

    def _current_day_key(self) -> str | None:
        if self.table.cursor_row is None:
            return None
        row_index = self.table.cursor_row
        if row_index < 0 or row_index >= len(self.day_order):
            return None
        return self.day_order[row_index]

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

    def _move_cursor(self, delta: int) -> None:
        if self.table.row_count == 0:
            return
        current_row = self.table.cursor_row or 0
        target_row = max(0, min(current_row + delta, self.table.row_count - 1))
        current_col = self.table.cursor_column or 0
        self.table.cursor_coordinate = (target_row, current_col)

    def _jump_cursor(self, target_row: int) -> None:
        if self.table.row_count == 0:
            return
        target_row = max(0, min(target_row, self.table.row_count - 1))
        current_col = self.table.cursor_column or 0
        self.table.cursor_coordinate = (target_row, current_col)

    def action_cursor_down(self) -> None:
        self._move_cursor(1)

    def action_cursor_up(self) -> None:
        self._move_cursor(-1)

    def action_cursor_top(self) -> None:
        self._jump_cursor(0)

    def action_cursor_bottom(self) -> None:
        self._jump_cursor(self.table.row_count - 1 if self.table.row_count else 0)

    def _update_row_cell(self, day_key: str, value: str) -> None:
        row_key = self._day_row_keys.get(day_key)
        column_key = self._template_column_key
        if row_key is None or column_key is None:
            # fallback to cursor row if keys missing
            if self.table.cursor_row is not None:
                self.table.update_cell_at(self.table.cursor_row, 1, value)
            return
        try:
            self.table.update_cell(row_key, column_key, value)
        except Exception:  # pragma: no cover - defensive fallback
            if self.table.cursor_row is not None:
                self.table.update_cell_at(self.table.cursor_row, 1, value)

    def action_save(self) -> None:
        self.dismiss(dict(self.assignments))

    def action_cancel(self) -> None:
        self.dismiss(None)
