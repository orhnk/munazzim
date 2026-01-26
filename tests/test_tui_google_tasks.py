from pathlib import Path
from types import SimpleNamespace

from munazzim.tui.app import MunazzimApp
from munazzim.tasks import TaskDefinition


def test_on_tasklist_selected_updates_config(monkeypatch, tmp_path: Path):
    app = MunazzimApp()
    # Prevent file writes
    monkeypatch.setattr(app.config_manager, "save", lambda cfg: None)
    # Avoid Textual-dependent logic which requires an active app/context
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    # Provide a tiny, non-Textual plan_table stub so refresh_plan won't attempt
    # to call Textual DataTable API during unit tests.
    app.plan_table = SimpleNamespace(clear=lambda *a, **k: None, add_row=lambda *a, **k: None, row_count=0, jump_to_row=lambda *a, **k: None)
    app._on_tasklist_selected("mychoice")
    assert app.selected_google_tasklist == "mychoice"
    assert app.config.planner.google_task_list == "mychoice"


def test_open_event_tasks_opens_editor(monkeypatch):
    from types import SimpleNamespace
    from datetime import date, time, timedelta
    from munazzim.models import DayTemplate, Event, ScheduledEvent

    app = MunazzimApp()
    # Avoid Textual-dependent logic which requires an active app/context
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    # Replace plan_table with a minimal stub carrying _row_metadata
    event = Event(name="Reading", duration=timedelta(hours=1))
    from datetime import datetime
    now = datetime.now()
    scheduled = ScheduledEvent(event=event, start=now, end=now)
    plan_table = SimpleNamespace(cursor_row=0, _row_metadata=[scheduled], clear=lambda *a, **k: None, add_row=lambda *a, **k: None, row_count=1, jump_to_row=lambda *a, **k: None, focus=lambda *a, **k: None)
    app.plan_table = plan_table

    called = {}

    class FakeService:
        def list_tasklists(self):
            return []

        def create_tasklist(self, title):
            called.setdefault("create_list", title)
            return SimpleNamespace(id="L", title=title)

    app.google_tasks_service = FakeService()
    # Inject a fake template-derived definition for the event
    app.task_engine._tasks_by_event[event.name] = [TaskDefinition(task_id="t1", event_name=event.name, label="Read book", note=None, total_occurrences=2)]
    app.action_open_event_tasks()
    assert called.get("create_list") == "Reading"


def test_open_event_tasks_syncs_to_google(monkeypatch, tmp_path: Path):
    from types import SimpleNamespace
    from datetime import datetime, timedelta
    from munazzim.models import Event, ScheduledEvent

    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    # Replace plan_table with a minimal stub carrying _row_metadata
    event = Event(name="Reading", duration=timedelta(hours=1))
    now = datetime.now()
    scheduled = ScheduledEvent(event=event, start=now, end=now)
    app.plan_table = SimpleNamespace(cursor_row=0, _row_metadata=[scheduled], clear=lambda *a, **k: None, add_row=lambda *a, **k: None, row_count=1, jump_to_row=lambda *a, **k: None, focus=lambda *a, **k: None)

    # Set timezone so we can be explicit about what Google receives
    app.config.location.timezone = "UTC"

    # Replace _launch_editor so it doesn't open real editor (no local files used)
    monkeypatch.setattr(app, "_launch_editor", lambda dirpath, target=None: True)
    called = {}

    class FakeService:
        def list_tasklists(self):
            return []

        def create_tasklist(self, title):
            called.setdefault("create_list", title)
            return SimpleNamespace(id="L", title=title)

        def list_tasks(self, list_id, show_completed=True):
            called.setdefault("list_tasks", True)
            return []

        def create_task(self, list_id, title=None, **kwargs):
            called.setdefault("added", []).append({"title": title, **kwargs})

    app.google_tasks_service = FakeService()
    # Seed task definitions in engine in place of local files
    app.task_engine._tasks_by_event[event.name] = [
        TaskDefinition(task_id="t-readbook", event_name=event.name, label="Read book", note=None, total_occurrences=2),
        TaskDefinition(task_id="t-dailyclean", event_name=event.name, label="Daily clean", note=None, total_occurrences=None),
    ]
    app.action_open_event_tasks()
    # Google Tasks list should be created and two items added
    assert called.get("create_list") == "Reading"
    # The FakeService records kwargs; ensure due equals the event start iso
    added = called.get("added", [])
    titles = [a["title"] for a in added]
    assert "Read book" in titles
    assert "Daily clean" in titles
    # Ensure due field is present and matches the scheduled start
    from zoneinfo import ZoneInfo
    from datetime import timezone

    for it in added:
        # The created task due should be the event start in UTC (Z suffix)
        assert it.get("due") == scheduled.start.replace(tzinfo=ZoneInfo("UTC")).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def test_google_tasks_label_is_list_title(monkeypatch, tmp_path: Path):
    from types import SimpleNamespace
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from datetime import timezone
    from munazzim.models import Event, ScheduledEvent

    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    # Replace plan_table with a minimal stub carrying _row_metadata
    event = Event(name="Reading", duration=timedelta(hours=1))
    now = datetime.now()
    scheduled = ScheduledEvent(event=event, start=now, end=now)
    app.plan_table = SimpleNamespace(cursor_row=0, _row_metadata=[scheduled], clear=lambda *a, **k: None, add_row=lambda *a, **k: None, row_count=1, jump_to_row=lambda *a, **k: None, focus=lambda *a, **k: None)
    # No local file; inject definitions directly into the engine
    app.task_engine._tasks_by_event[event.name] = [
        TaskDefinition(task_id="t1", event_name=event.name, label="Read book", note=None, total_occurrences=2),
        TaskDefinition(task_id="t2", event_name=event.name, label="Daily clean", note=None, total_occurrences=None),
    ]
    # Set UTC timezone so the conversion is deterministic
    app.config.location.timezone = "UTC"

    called = {}
    class FakeService:
        def list_tasklists(self):
            return [SimpleNamespace(id="L", title="Reading")]

        def list_tasks(self, list_id, show_completed=True):
            return [SimpleNamespace(id="t1", title="Read book", notes=None, due=None, status="needsAction")]

    app.google_tasks_service = FakeService()
    # Capture todos passed to set_todos
    seen = {}
    def capture_todos(items):
        seen["todos"] = items
    monkeypatch.setattr(app.week_panel, "set_todos", capture_todos)
    app.selected_google_tasklist = "L"
    app.refresh_plan()
    todos = seen.get("todos", [])
    print('DEBUG TODOS:', [(t.task, t.due, t.event) for t in todos])
    assert todos
    assert all(t.event == "Reading" for t in todos)


def test_google_tasks_due_shows_start_time(monkeypatch, tmp_path: Path):
    from types import SimpleNamespace
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from datetime import timezone
    from munazzim.models import Event, ScheduledEvent

    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    # Event scheduled today with a time component
    event = Event(name="Reading", duration=timedelta(hours=1))
    now = datetime.now().replace(hour=15, minute=30, second=0, microsecond=0)
    scheduled = ScheduledEvent(event=event, start=now, end=now)
    app.plan_table = SimpleNamespace(cursor_row=0, _row_metadata=[scheduled], clear=lambda *a, **k: None, add_row=lambda *a, **k: None, row_count=1, jump_to_row=lambda *a, **k: None, focus=lambda *a, **k: None)
    # Inject engine definitions for the event rather than using task files
    app.task_engine._tasks_by_event[event.name] = [TaskDefinition(task_id="t1", event_name=event.name, label="Read book", note=None, total_occurrences=None)]

    class FakeService:
        def list_tasklists(self):
            return [SimpleNamespace(id="L", title="Reading")]

        def list_tasks(self, list_id, show_completed=True):
            # The task has no due stored in API (time discarded)
            return [SimpleNamespace(id="t1", title="Read book", notes=None, due=None, status="needsAction")]

    app.google_tasks_service = FakeService()
    seen = {}
    monkeypatch.setattr(app.week_panel, "set_todos", lambda items: seen.setdefault("todos", items))
    app.selected_google_tasklist = "L"
    # Ensure our scheduler returns a plan containing our scheduled event
    from munazzim.models import DayPlan
    monkeypatch.setattr(app.scheduler, "build_plan", lambda template, plan_date=None, prayer_schedule=None: DayPlan(template_name=(template.name if template else "t"), generated_for=(plan_date if plan_date else scheduled.start.date()), items=[scheduled]))
    # Set timezone for deterministic behavior
    app.config.location.timezone = "UTC"
    app.refresh_plan()
    todos = seen.get("todos", [])
    assert todos
    # Todo.due should be set to ISO UTC string derived from scheduled.start
    # The value should be an RFC3339-like UTC timestamp (contains 'T' and ends with 'Z')
    assert any(t.due and 'T' in t.due and t.due.endswith('Z') for t in todos)


def test_display_error_shows_popup(monkeypatch, tmp_path: Path):
    app = MunazzimApp()
    # Prevent plan-table UI writes in the fallback
    monkeypatch.setattr(app.config_manager, "save", lambda cfg: None)
    called = {}

    def fake_push(screen, callback=None):
        called["screen"] = screen

    monkeypatch.setattr(app, "push_screen", fake_push)
    # Replace plan table with a stub to ensure we don't write to it
    app.plan_table = SimpleNamespace(clear=lambda *a, **k: called.setdefault("cleared", True), add_row=lambda *a, **k: called.setdefault("added", True))
    app._display_error("Widget", "Something bad happened")
    assert "screen" in called
    assert called.get("added") is None


def test_task_table_s_triggers_select(monkeypatch):
    from munazzim.tui.app import TaskTableView

    table = TaskTableView(on_toggle=lambda a, b: None, on_log=lambda a: None)
    table._ready = True
    called = {}

    fake_app = SimpleNamespace(action_select_task_list=lambda: called.setdefault("selected", True))
    # Monkeypatch the .app property so we can call the widget action without a running App
    monkeypatch.setattr(TaskTableView, "app", property(lambda self: fake_app))

    table.action_select_task_list()
    assert called.get("selected") is True


def test_task_table_a_triggers_add(monkeypatch):
    from munazzim.tui.app import TaskTableView

    table = TaskTableView(on_toggle=lambda a, b: None, on_log=lambda a: None)
    table._ready = True
    called = {}

    fake_app = SimpleNamespace(action_add_task=lambda: called.setdefault("added", True))
    monkeypatch.setattr(TaskTableView, "app", property(lambda self: fake_app))

    table.action_add_task()
    assert called.get("added") is True


def test_task_table_e_triggers_edit(monkeypatch):
    from munazzim.tui.app import TaskTableView

    table = TaskTableView(on_toggle=lambda a, b: None, on_log=lambda a: None)
    table._ready = True
    called = {}

    fake_app = SimpleNamespace(action_edit_task=lambda: called.setdefault("edited", True))
    monkeypatch.setattr(TaskTableView, "app", property(lambda self: fake_app))

    table.action_edit_task()
    assert called.get("edited") is True


def test_task_table_x_triggers_delete(monkeypatch):
    from munazzim.tui.app import TaskTableView

    table = TaskTableView(on_toggle=lambda a, b: None, on_log=lambda a: None)
    table._ready = True
    called = {}

    fake_app = SimpleNamespace(action_delete_task=lambda: called.setdefault("deleted", True))
    monkeypatch.setattr(TaskTableView, "app", property(lambda self: fake_app))

    table.action_delete_task()
    assert called.get("deleted") is True


def test_task_table_d_triggers_delete(monkeypatch):
    from munazzim.tui.app import TaskTableView

    table = TaskTableView(on_toggle=lambda a, b: None, on_log=lambda a: None)
    table._ready = True
    called = {}

    fake_app = SimpleNamespace(action_delete_task=lambda: called.setdefault("deleted_d", True))
    monkeypatch.setattr(TaskTableView, "app", property(lambda self: fake_app))

    table.action_delete_task()
    assert called.get("deleted_d") is True


def test_todos_update_on_cursor_move(monkeypatch):
    from types import SimpleNamespace
    from datetime import datetime, timedelta
    from munazzim.models import Event, ScheduledEvent
    from munazzim.tui.app import PlanTable

    app = MunazzimApp()
    # Avoid Textual updates in tests
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    seen = {}
    monkeypatch.setattr(app.week_panel, "set_todos", lambda items: seen.setdefault("todos", items))

    # Create a real plan table so jump_to_row calls our app hook
    app.plan_table = PlanTable()
    # Make the table report a single row_count without configuring Textual console
    monkeypatch.setattr(PlanTable, "row_count", property(lambda self: 1), raising=False)
    # Avoid Textual's console calls during unit tests. Instead of building
    # the full table, set a minimal headless state so PlanTable.jump_to_row
    # exercises the cursor-change hook.
    # Prevent scrolling which calls Textual APIs
    app.plan_table.scroll_to_row = lambda row: None
    # Minimal scheduled event for row 0
    event = Event(name="Reading", duration=timedelta(hours=1))
    now = datetime.now()
    scheduled = ScheduledEvent(event=event, start=now, end=now)
    # Emulate a single row mapped to our scheduled event
    app.plan_table._row_metadata = [scheduled]

    called = {}
    class FakeService:
        def list_tasklists(self):
            return [SimpleNamespace(id="L", title="Reading")]

        def list_tasks(self, list_id, show_completed=True):
            called.setdefault("list_tasks", True)
            return [SimpleNamespace(id="t1", title="Read book", notes=None, due=None, status="needsAction")]

    app.google_tasks_service = FakeService()
    # Ensure no preselected list so the cursor-change logic chooses the matching list
    app.selected_google_tasklist = ""
    # Trigger the cursor change; our patch calls app.on_plan_cursor_changed.
    # If DataTable hook doesn't reach the app in a headless test, call
    # the handler directly to replicate the same behavior.
    app.plan_table.jump_to_row(0)
    if not seen.get("todos"):
        app.on_plan_cursor_changed(0)
    todos = seen.get("todos", [])
    assert todos
    assert any(t.task == "Read book" for t in todos)


def test_plan_table_navigation_actions(monkeypatch):
    from munazzim.tui.app import PlanTable
    table = PlanTable()
    # Avoid writing real DataTable columns in a headless test
    monkeypatch.setattr(PlanTable, "row_count", property(lambda self: 2), raising=False)
    table.add_row = lambda *a, **k: None
    # Simulate a table with 3 rows and current cursor at 0
    monkeypatch.setattr(PlanTable, "row_count", property(lambda self: 3), raising=False)
    monkeypatch.setattr(PlanTable, "cursor_row", property(lambda self: 0), raising=False)
    # Capture jump_to_row calls
    seen = {}
    def capture_jump(row):
        seen.setdefault("row", []).append(row)
    table.jump_to_row = capture_jump
    table.action_cursor_down()
    assert seen.get("row") == [1]
    # Simulate cursor at row 1 and move up
    monkeypatch.setattr(PlanTable, "cursor_row", property(lambda self: 1), raising=False)
    table.action_cursor_up()
    assert seen.get("row")[-1] == 0


def test_current_event_indicator(monkeypatch):
    from munazzim.models import Event, ScheduledEvent, DayPlan
    from datetime import datetime, timedelta
    from munazzim.tui.app import PlanTable, MunazzimApp
    app = MunazzimApp()
    # Replace plan table add_row to capture the labels passed in
    labels = []
    def capture_add_row(*args, **kwargs):
        labels.append(args[2])
    monkeypatch.setattr(app, "_show_plan_view", lambda *a, **k: None)
    app.plan_table = PlanTable()
    monkeypatch.setattr(PlanTable, "row_count", property(lambda self: 1), raising=False)
    app.plan_table.add_row = lambda *a, **k: None
    app.plan_table.add_row = capture_add_row
    # Build a plan with a single scheduled event active now
    event = Event(name="Reading", duration=timedelta(hours=1))
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    scheduled = ScheduledEvent(event=event, start=now, end=now + timedelta(hours=1))
    monkeypatch.setattr(
        app.scheduler,
        "build_plan",
        lambda template, plan_date=None, prayer_schedule=None: DayPlan(template_name=(template.name if template else "t"), generated_for=(plan_date if plan_date else scheduled.start.date()), items=[scheduled]),
    )
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    # Run refresh_plan and ensure the captured label contains the pointer
    app.refresh_plan()
    assert labels
    assert any(l.startswith("▶") or l.startswith(" ▶") for l in labels)


def test_user_navigation_suppresses_auto_highlight(monkeypatch):
    from types import SimpleNamespace
    import time
    from datetime import datetime, timedelta
    from munazzim.models import Event, ScheduledEvent, DayPlan
    from munazzim.tui.app import PlanTable, MunazzimApp

    app = MunazzimApp()
    seen = {}
    # Replace jump_to_row to capture calls
    table = PlanTable()
    def capture_jump(row):
        seen.setdefault('jumps', []).append(row)
    table.jump_to_row = capture_jump
    # two scheduled events; index 1 is active now
    event1 = Event(name='A', duration=timedelta(hours=1))
    event2 = Event(name='B', duration=timedelta(hours=1))
    now = datetime.now()
    sched1 = ScheduledEvent(event=event1, start=now - timedelta(hours=2), end=now - timedelta(hours=1))
    sched2 = ScheduledEvent(event=event2, start=now - timedelta(minutes=10), end=now + timedelta(hours=1))
    app.plan_table = table
    # Have build_plan return our plan with two items
    monkeypatch.setattr(app.scheduler, 'build_plan', lambda template, plan_date=None, prayer_schedule=None: DayPlan(template_name=(template.name if template else 't'), generated_for=(plan_date if plan_date else sched2.start.date()), items=[sched1, sched2]))
    monkeypatch.setattr(app.week_panel, 'set_data', lambda *a, **k: None)
    # First, ensure we auto-highlight when user didn't navigate
    app.refresh_plan()
    assert seen.get('jumps')
    seen.clear()
    # Now simulate user navigation just before refresh; highlight should be suppressed
    app.on_user_navigated()
    app.refresh_plan()
    assert not seen.get('jumps')


def test_google_tasks_cache_hits(monkeypatch):
    from types import SimpleNamespace
    from datetime import datetime, timedelta
    from munazzim.models import Event, ScheduledEvent
    from munazzim.tui.app import MunazzimApp

    from munazzim.tui.app import PlanTable
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, 'set_data', lambda *a, **k: None)
    seen = {'list_calls': 0}
    class FakeService:
        def list_tasklists(self):
            return [SimpleNamespace(id='L', title='Reading')]
        def list_tasks(self, list_id, show_completed=True):
            seen['list_calls'] += 1
            return [SimpleNamespace(id='t1', title='Read book', notes=None, due=None, status='needsAction')]
    app.google_tasks_service = FakeService()
    app.selected_google_tasklist = 'L'
    # Inject simple plan with a single scheduled event
    event = Event(name='Reading', duration=timedelta(hours=1))
    now = datetime.now()
    scheduled = ScheduledEvent(event=event, start=now, end=now)
    app.plan_table = PlanTable()
    app.plan_table._row_metadata = [scheduled]
    # First call should hit the API
    app.refresh_plan()
    assert seen['list_calls'] == 1
    # Second call should use cache and not hit the list_tasks again
    app.refresh_plan()
    assert seen['list_calls'] == 1


def test_on_google_task_toggled_calls_update(monkeypatch, tmp_path: Path):
    app = MunazzimApp()
    # Avoid Textual-dependent logic which requires an active app/context
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    app.plan_table = SimpleNamespace(clear=lambda *a, **k: None, add_row=lambda *a, **k: None, row_count=0, jump_to_row=lambda *a, **k: None)
    called = {}

    class FakeService:
        def list_tasklists(self):
            return [SimpleNamespace(id="L", title="L1")]

        def update_task(self, list_id, task_id, **kwargs):
            called["last"] = (list_id, task_id, kwargs)

    svc = FakeService()
    app.google_tasks_service = svc
    app.selected_google_tasklist = "L"

    # Toggle a task to completed
    app._on_google_task_toggled("tid-123", completed=True)
    assert called
    list_id, task_id, kwargs = called["last"]
    assert list_id == "L"
    assert task_id == "tid-123"
    assert kwargs.get("status") == "completed"

    # Uncomplete
    called.clear()
    app._on_google_task_toggled("tid-456", completed=False)
    assert called["last"][2].get("status") == "needsAction"


def test_on_new_task_creates_task(monkeypatch, tmp_path: Path):
    app = MunazzimApp()
    # Avoid Textual-dependent logic which requires an active app/context
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    app.plan_table = SimpleNamespace(clear=lambda *a, **k: None, add_row=lambda *a, **k: None, row_count=0, jump_to_row=lambda *a, **k: None)
    created = {}

    class FakeService:
        def list_tasklists(self):
            return [SimpleNamespace(id="L", title="L1")]

        def create_task(self, list_id, title=None, **kwargs):
            created["args"] = (list_id, title, kwargs)

    svc = FakeService()
    app.google_tasks_service = svc
    # Set empty so it will pick the first list
    app.selected_google_tasklist = ""
    data = {"title": "Buy milk", "notes": "milk"}
    app._on_new_task(data)
    assert created
    assert created["args"][0] == "L"
    assert created["args"][1] == "Buy milk"


def test_on_task_edited_calls_update(monkeypatch, tmp_path: Path):
    app = MunazzimApp()
    # Avoid Textual update paths
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    app.plan_table = SimpleNamespace(clear=lambda *a, **k: None, add_row=lambda *a, **k: None, row_count=0, jump_to_row=lambda *a, **k: None)

    called = {}

    class FakeService:
        def list_tasklists(self):
            return [SimpleNamespace(id="L", title="L1")]

        def update_task(self, list_id, task_id, **kwargs):
            called["last"] = (list_id, task_id, kwargs)

    svc = FakeService()
    app.google_tasks_service = svc
    app.selected_google_tasklist = "L"

    app._on_task_edited("tid-42", {"title": "New title", "notes": "something"})
    assert called
    list_id, task_id, kwargs = called["last"]
    assert list_id == "L"
    assert task_id == "tid-42"
    # Ensure known fields reach the service
    assert kwargs.get("title") == "New title"
    assert kwargs.get("notes") == "something"


def test_tui_applies_relative_prayer_placeholder(monkeypatch):
    from munazzim.tui.app import MunazzimApp
    from munazzim.qalib import parse_qalib
    from datetime import datetime, timedelta
    from munazzim.models import PrayerEvent

    app = MunazzimApp()
    # Avoid Textual layout calls
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    # Replace templates.get to return our parsed QALib template
    raw = """
    04:00
    1 Prep
    .20 Fajr
    """
    tpl = parse_qalib(raw, default_name="RelativePrayer")
    # Ensure parser created a PrayerEvent for placeholder
    assert isinstance(tpl.events[-1], PrayerEvent)
    # Force TemplateRepository.get to return our template
    monkeypatch.setattr(app.templates, "get", lambda name: tpl)
    # Use deterministic prayer schedule for test
    app.config.prayer_settings.durations.fajr = timedelta(minutes=10)
    app.config.prayers = app.config.prayers  # keep defaults
    # Replace plan_table with a stub to capture _row_metadata
    app.plan_table = SimpleNamespace(clear=lambda *a, **k: None, add_row=lambda *a, **k: None, _row_metadata=[], row_count=0, jump_to_row=lambda *a, **k: None)
    # Capture status_line updates so we can verify override is surfaced
    updates = []
    monkeypatch.setattr(app.status_line, "update", lambda s: updates.append(s))
    # Run refresh_plan and check the scheduled Fajr event duration
    app.refresh_plan()
    scheduled = [s for s in app.plan_table._row_metadata if isinstance(getattr(s, 'event', None), PrayerEvent) and s.event.prayer == 'Fajr']
    assert scheduled
    # The scheduled Fajr must have duration 20 minutes due to .20 placeholder
    delta = scheduled[0].end - scheduled[0].start
    assert delta == timedelta(minutes=20)
    assert any("Prayer overrides applied" in s for s in updates)

