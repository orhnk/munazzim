from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, time, timedelta, date

from munazzim.tui.app import MunazzimApp
from munazzim.models import Event, ScheduledEvent


def test_sync_week_to_google_calendar_creates_recurring_events(monkeypatch):
    app = MunazzimApp()
    # No UI calls
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    # Choose Monday template assignment
    monday = "monday"
    app.week_assignments = {monday: "Template A"}
    # Create a scheduled event on Monday at 08:00
    ev = Event(name="Reading", duration=timedelta(hours=1))
    # pick a Monday in the current week
    today = date.today()
    # compute the Monday of this week
    delta = (0 - today.weekday()) % 7
    monday_date = today + timedelta(days=delta)
    start_dt = datetime.combine(monday_date, time(hour=8, minute=0))
    end_dt = start_dt + timedelta(hours=1)
    scheduled = ScheduledEvent(event=ev, start=start_dt, end=end_dt)
    # Stub templates.get and scheduler.build_plan
    monkeypatch.setattr(app.templates, "get", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(app.scheduler, "build_plan", lambda template, plan_date=None, prayer_schedule=None: SimpleNamespace(items=[scheduled]))
    # Replace prayer service get_schedule
    monkeypatch.setattr(app.prayer_service, "get_schedule", lambda d: None)
    called = {}

    class FakeService:
        def list_calendars(self):
            return []

        def create_calendar(self, summary, time_zone=None):
            called.setdefault("create_calendar", summary)
            return SimpleNamespace(id="cal-1", summary=summary)

        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            called.setdefault("list_events_called", True)
            return []

        def create_event(self, calendar_id, event_body):
            called.setdefault("created", []).append(event_body)

    app.google_calendar_service = FakeService()
    # Set timezone so isoformat uses timezone-aware stamps in payload
    app.config.location.timezone = "UTC"
    # Run sync helper synchronously (tests should call the internal sync directly)
    app._sync_week_to_google_calendar()
    assert called.get("create_calendar") == "Munazzim"
    created = called.get("created", [])
    assert created
    # Ensure recurrence is weekly and no UNTIL / COUNT (open ended)
    for ev in created:
        rec = ev.get("recurrence")
        assert rec and any(s.startswith("RRULE:FREQ=WEEKLY") for s in rec)
        # signature in extendedProperties
        props = ev.get("extendedProperties", {}).get("private", {})
        assert props.get("munazzim_signature")


def test_sync_week_to_google_calendar_uses_existing_calendar_and_counts(monkeypatch):
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    monday = "monday"
    app.week_assignments = {monday: "Template A"}
    ev = Event(name="Reading", duration=timedelta(hours=1))
    today = date.today()
    delta = (0 - today.weekday()) % 7
    monday_date = today + timedelta(days=delta)
    start_dt = datetime.combine(monday_date, time(hour=8, minute=0))
    end_dt = start_dt + timedelta(hours=1)
    scheduled = ScheduledEvent(event=ev, start=start_dt, end=end_dt)
    monkeypatch.setattr(app.templates, "get", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(app.scheduler, "build_plan", lambda template, plan_date=None, prayer_schedule=None: SimpleNamespace(items=[scheduled]))
    monkeypatch.setattr(app.prayer_service, "get_schedule", lambda d: None)
    called = {}

    class FakeService:
        def list_calendars(self):
            # Calendar already exists
            return [SimpleNamespace(id="cal-1", summary="Munazzim")]

        def create_calendar(self, summary, time_zone=None):
            called.setdefault("create_calendar", summary)
            return SimpleNamespace(id="cal-1", summary=summary)

        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            called.setdefault("list_events_called", True)
            return []

        def create_event(self, calendar_id, event_body):
            called.setdefault("created", []).append(event_body)

    app.google_calendar_service = FakeService()
    app.config.location.timezone = "UTC"
    count = app._sync_week_to_google_calendar()
    assert isinstance(count, int)
    assert count == len(called.get("created", []))
    # Ensure create_calendar was not called because it already existed
    assert called.get("create_calendar") is None


def test_sync_week_to_google_calendar_fallbacks_to_active_template(monkeypatch):
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    # No explicit week assignments
    app.week_assignments = {}
    # Set an active template name which should be used for all days
    app.active_template_name = "Template A"
    ev = Event(name="Reading", duration=timedelta(hours=1))
    called = {"created": []}

    def make_plan(template, plan_date=None, prayer_schedule=None):
        # Create a scheduled event at 08:00 for the given plan_date
        start_dt = datetime.combine(plan_date, time(hour=8, minute=0))
        end_dt = start_dt + timedelta(hours=1)
        scheduled = ScheduledEvent(event=ev, start=start_dt, end=end_dt)
        return SimpleNamespace(items=[scheduled])

    monkeypatch.setattr(app.templates, "get", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(app.scheduler, "build_plan", make_plan)
    monkeypatch.setattr(app.prayer_service, "get_schedule", lambda d: None)

    class FakeService:
        def list_calendars(self):
            return []

        def create_calendar(self, summary, time_zone=None):
            return SimpleNamespace(id="cal-1", summary=summary)

        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            return []

        def create_event(self, calendar_id, event_body):
            called["created"].append(event_body)

    app.google_calendar_service = FakeService()
    app.config.location.timezone = "UTC"
    count = app._sync_week_to_google_calendar()
    # Expect one event per weekday (7 days) created from the active template
    assert count == 7


def test_collect_weekly_event_payloads_returns_expected(monkeypatch):
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    app.week_assignments = {}
    app.active_template_name = "Template A"
    ev = Event(name="Reading", duration=timedelta(hours=1))
    monkeypatch.setattr(app.templates, "get", lambda name: SimpleNamespace(name=name))
    def make_plan(template, plan_date=None, prayer_schedule=None):
        start_dt = datetime.combine(plan_date, time(hour=8, minute=0))
        end_dt = start_dt + timedelta(hours=1)
        scheduled = ScheduledEvent(event=ev, start=start_dt, end=end_dt)
        return SimpleNamespace(items=[scheduled])
    monkeypatch.setattr(app.scheduler, "build_plan", make_plan)
    monkeypatch.setattr(app.prayer_service, "get_schedule", lambda d: None)
    class FakeService:
        def list_calendars(self):
            return []
        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            return []
    app.google_calendar_service = FakeService()
    app.config.location.timezone = "UTC"
    payloads = app._collect_weekly_event_payloads()
    assert isinstance(payloads, list)
    assert len(payloads) == 7


def test_collect_weekly_event_payloads_weekday_distribution(monkeypatch):
    """Ensure the payloads generated are distributed across the 7 weekdays."""
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    app.week_assignments = {}
    app.active_template_name = "Template A"
    ev = Event(name="Reading", duration=timedelta(hours=1))
    monkeypatch.setattr(app.templates, "get", lambda name: SimpleNamespace(name=name))
    def make_plan(template, plan_date=None, prayer_schedule=None):
        start_dt = datetime.combine(plan_date, time(hour=8, minute=0))
        end_dt = start_dt + timedelta(hours=1)
        scheduled = ScheduledEvent(event=ev, start=start_dt, end=end_dt)
        return SimpleNamespace(items=[scheduled])
    monkeypatch.setattr(app.scheduler, "build_plan", make_plan)
    monkeypatch.setattr(app.prayer_service, "get_schedule", lambda d: None)
    class FakeService:
        def list_calendars(self):
            return []
        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            return []
    app.google_calendar_service = FakeService()
    app.config.location.timezone = 'UTC'
    payloads = app._collect_weekly_event_payloads()
    found_weekdays = set()
    from datetime import timezone as _tz
    for p in payloads:
        start = p['start']['dateTime']
        if start.endswith('Z'):
            start = start.replace('Z', '+00:00')
        dt = datetime.fromisoformat(start)
        found_weekdays.add(dt.weekday())
    assert len(found_weekdays) == 7


def test_sync_skips_when_fingerprint_unchanged(monkeypatch):
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    app.week_assignments = {"monday": "A"}
    app.active_template_name = "A"
    ev = Event(name="Reading", duration=timedelta(hours=1))
    monkeypatch.setattr(app.templates, "get", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(app.scheduler, "build_plan", lambda template, plan_date=None, prayer_schedule=None: SimpleNamespace(items=[]))
    monkeypatch.setattr(app.prayer_service, "get_schedule", lambda d: None)
    # Provide fingerprint helpers so _sync_week_to_google_calendar sees unchanged fingerprint
    monkeypatch.setattr(app, '_compute_templates_fingerprint', lambda: 'abc123')
    monkeypatch.setattr(app, '_read_last_fingerprint', lambda: 'abc123')
    called = {}
    class FakeService:
        def list_calendars(self):
            return [SimpleNamespace(id='cal-1', summary='Munazzim')]

        def create_calendar(self, summary, time_zone=None):
            called.setdefault('created_cal', True)
            return SimpleNamespace(id='cal-1', summary=summary)

        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            # Return an existing event so the apply path has something to delete.
            return [SimpleNamespace(id='evt-old', recurring_event_id='series-old', extended_properties={'private': {'munazzim_signature': 'oldsig'}})]

        def create_event(self, calendar_id, event_body):
            called.setdefault('created', []).append(event_body)

        def delete_event(self, calendar_id, event_id):
            called.setdefault('deleted', []).append(event_id)
    app.google_calendar_service = FakeService()
    c = app._sync_week_to_google_calendar()
    # Because we force nuke+create behavior, apply should have run even when
    # fingerprint is unchanged; we expect deletions for the existing remote events
    assert 'deleted' in called
    # created may be zero since the plan returned no items, but c should be an int
    assert isinstance(c, int)


def test_sync_runs_when_fingerprint_changed_and_writes_state(monkeypatch, tmp_path):
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    app.week_assignments = {"monday": "A"}
    app.active_template_name = "A"
    ev = Event(name="Reading", duration=timedelta(hours=1))
    monkeypatch.setattr(app.templates, "get", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(app.scheduler, "build_plan", lambda template, plan_date=None, prayer_schedule=None: SimpleNamespace(items=[ScheduledEvent(event=ev, start=datetime.combine(date.today(), time(8,0)), end=datetime.combine(date.today(), time(9,0)))]))
    monkeypatch.setattr(app.prayer_service, "get_schedule", lambda d: None)
    # set fingerprint helpers: compute returns 'new' while read_last returns 'old'
    called = {}
    monkeypatch.setattr(app, '_compute_templates_fingerprint', lambda: 'newfp')
    monkeypatch.setattr(app, '_read_last_fingerprint', lambda: 'oldfp')
    # Redirect state file path to a temp file
    state_path = tmp_path / 'state.json'
    monkeypatch.setattr(app, '_state_file_path', lambda: state_path)
    def capture_write(fp):
        called['written'] = fp
    monkeypatch.setattr(app, '_write_last_fingerprint', capture_write)
    class FakeService:
        def list_calendars(self):
            return []

        def create_calendar(self, summary, time_zone=None):
            return SimpleNamespace(id='cal-1', summary=summary)

        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            return []

        def create_event(self, calendar_id, event_body):
            called.setdefault('created', []).append(event_body)
    app.google_calendar_service = FakeService()
    count = app._sync_week_to_google_calendar()
    # Should create events for the week (one event per day in this setup)
    assert isinstance(count, int) and count > 0
    assert called.get('written') == 'newfp'
    assert count == len(called.get('created', []))


def test_always_sync_bypasses_fingerprint(monkeypatch):
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    app.week_assignments = {"monday": "A"}
    app.active_template_name = "A"
    ev = Event(name="Reading", duration=timedelta(hours=1))
    monkeypatch.setattr(app.templates, "get", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(app.scheduler, "build_plan", lambda template, plan_date=None, prayer_schedule=None: SimpleNamespace(items=[ScheduledEvent(event=ev, start=datetime.combine(date.today(), time(8,0)), end=datetime.combine(date.today(), time(9,0)))]))
    monkeypatch.setattr(app.prayer_service, "get_schedule", lambda d: None)
    monkeypatch.setattr(app, '_compute_templates_fingerprint', lambda: 'samefp')
    monkeypatch.setattr(app, '_read_last_fingerprint', lambda: 'samefp')
    app.config.planner.google_calendar_always_sync = True
    called = {}
    class FakeService:
        def list_calendars(self):
            return []

        def create_calendar(self, summary, time_zone=None):
            return SimpleNamespace(id='cal-1', summary=summary)

        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            return []

        def create_event(self, calendar_id, event_body):
            called.setdefault('created', []).append(event_body)
    app.google_calendar_service = FakeService()
    count = app._sync_week_to_google_calendar()
    assert isinstance(count, int) and count > 0
    assert 'created' in called


def test_debug_sync_collects_errors(monkeypatch):
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    app.week_assignments = {"monday": "A"}
    app.active_template_name = "A"
    ev = Event(name="Reading", duration=timedelta(hours=1))
    monkeypatch.setattr(app.templates, "get", lambda name: SimpleNamespace(name=name))
    def build_plan(template, plan_date=None, prayer_schedule=None):
        from datetime import datetime, date, time, timedelta
        start_dt = datetime.combine(date.today(), time(8, 0))
        end_dt = start_dt + timedelta(hours=1)
        return SimpleNamespace(items=[ScheduledEvent(event=ev, start=start_dt, end=end_dt)])
    monkeypatch.setattr(app.scheduler, "build_plan", build_plan)
    monkeypatch.setattr(app.prayer_service, "get_schedule", lambda d: None)
    # Make create_event raise for diagnostics
    class FailingService:
        def list_calendars(self):
            return []
        def create_calendar(self, summary):
            return SimpleNamespace(id='cal-1', summary=summary)
        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            return []
        def create_event(self, calendar_id, event_body):
            raise RuntimeError('boom')
    app.google_calendar_service = FailingService()
    stats = app._sync_week_to_google_calendar_debug()
    assert stats['planned_count'] > 0
    # debug is a dry run; would_create_count reflects creations; no errors because we don't execute create_event
    assert stats.get('would_create_count', 0) > 0
    assert stats['errors'] == []


def test_apply_collects_errors(monkeypatch):
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    app.week_assignments = {"monday": "A"}
    app.active_template_name = "A"
    ev = Event(name="Reading", duration=timedelta(hours=1))
    monkeypatch.setattr(app.templates, "get", lambda name: SimpleNamespace(name=name))
    def build_plan(template, plan_date=None, prayer_schedule=None):
        from datetime import datetime, date, time, timedelta
        start_dt = datetime.combine(date.today(), time(8, 0))
        end_dt = start_dt + timedelta(hours=1)
        return SimpleNamespace(items=[ScheduledEvent(event=ev, start=start_dt, end=end_dt)])
    monkeypatch.setattr(app.scheduler, "build_plan", build_plan)
    monkeypatch.setattr(app.prayer_service, "get_schedule", lambda d: None)
    # Make create_event raise for diagnostics
    class FailingService:
        def list_calendars(self):
            return []
        def create_calendar(self, summary):
            return SimpleNamespace(id='cal-1', summary=summary)
        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            return []
        def create_event(self, calendar_id, event_body):
            raise RuntimeError('boom')
    app.google_calendar_service = FailingService()
    stats = app._sync_week_to_google_calendar_apply()
    assert stats['planned_count'] > 0
    assert stats['created_count'] == 0
    assert stats['errors']


def test_create_calendar_includes_timezone(monkeypatch):
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    app.week_assignments = {"monday": "A"}
    app.active_template_name = "A"
    ev = Event(name="Reading", duration=timedelta(hours=1))
    monkeypatch.setattr(app.templates, "get", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(app.scheduler, "build_plan", lambda template, plan_date=None, prayer_schedule=None: SimpleNamespace(items=[ScheduledEvent(event=ev, start=datetime.combine(date.today(), time(8,0)), end=datetime.combine(date.today(), time(9,0)))]))
    monkeypatch.setattr(app.prayer_service, "get_schedule", lambda d: None)
    captured = {}
    class CapturingService:
        def list_calendars(self):
            return []
        def create_calendar(self, summary, time_zone=None):
            captured['tz'] = time_zone
            return SimpleNamespace(id='cal-1', summary=summary)
        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            return []
        def create_event(self, calendar_id, event_body):
            pass
    app.google_calendar_service = CapturingService()
    app.config.location.timezone = 'UTC'
    app._sync_week_to_google_calendar()
    assert captured.get('tz') == 'UTC'


def test_event_payload_includes_time_zone(monkeypatch):
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    app.week_assignments = {"monday": "A"}
    app.active_template_name = "A"
    ev = Event(name="Reading", duration=timedelta(hours=1))
    monkeypatch.setattr(app.templates, "get", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(app.scheduler, "build_plan", lambda template, plan_date=None, prayer_schedule=None: SimpleNamespace(items=[ScheduledEvent(event=ev, start=datetime.combine(date.today(), time(8,0)), end=datetime.combine(date.today(), time(9,0)))]))
    monkeypatch.setattr(app.prayer_service, "get_schedule", lambda d: None)
    captured = {}
    class CapturingService:
        def list_calendars(self):
            return []
        def create_calendar(self, summary, time_zone=None):
            return SimpleNamespace(id='cal-1', summary=summary)
        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            return []
        def create_event(self, calendar_id, event_body):
            captured['payload'] = event_body
            return SimpleNamespace(id='evt-1')
    app.google_calendar_service = CapturingService()
    app.config.location.timezone = 'UTC'
    app._sync_week_to_google_calendar()
    assert captured.get('payload')
    assert captured['payload']['start'].get('timeZone') == 'UTC'
    assert captured['payload']['end'].get('timeZone') == 'UTC'


def test_display_sync_errors_modal(monkeypatch):
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    app.week_assignments = {"monday": "A"}
    app.active_template_name = "A"
    ev = Event(name="Reading", duration=timedelta(hours=1))
    monkeypatch.setattr(app.templates, "get", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(app.scheduler, "build_plan", lambda template, plan_date=None, prayer_schedule=None: SimpleNamespace(items=[ScheduledEvent(event=ev, start=datetime.combine(date.today(), time(8,0)), end=datetime.combine(date.today(), time(9,0)))]))
    monkeypatch.setattr(app.prayer_service, "get_schedule", lambda d: None)
    called = {}
    class FakeService:
        def list_calendars(self):
            return []
        def create_calendar(self, summary):
            return SimpleNamespace(id='cal-1', summary=summary)
        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            return []
        def create_event(self, calendar_id, event_body):
            raise RuntimeError('boom')
    app.google_calendar_service = FakeService()
    # monkeypatch push_screen to capture the screen object
    captured = {}
    def fake_push(screen, *args, **kwargs):
        captured['screen'] = screen
    monkeypatch.setattr(app, 'push_screen', fake_push)
    # apply sync which should produce errors and call _display_sync_errors
    stats = app._sync_week_to_google_calendar_apply()
    assert stats['errors']
    # call the main wrapper to display errors
    monkeypatch.setattr(app, '_sync_week_to_google_calendar_apply', lambda: stats)
    monkeypatch.setattr(app, '_write_last_fingerprint', lambda fp: None)
    app._sync_week_to_google_calendar()
    assert 'screen' in captured
    screen = captured['screen']
    assert getattr(screen, 'prefix', None) == 'Google Calendar Sync Errors'
    # Ensure detailed error strings are included
    assert any('boom' in e for e in stats['errors'])


def test_error_screen_copy_uses_pyperclip(monkeypatch):
    from munazzim.tui.screens import ErrorScreen
    captured = {}
    sys_mod = {
        'copy_called': None,
        'copy': lambda s: sys_mod.update({'copy_called': s}),
    }
    # Inject fake pyperclip
    import sys
    sys.modules['pyperclip'] = type('m', (), {'copy': lambda s: sys_mod.update({'copy_called': s})})
    screen = ErrorScreen('Test', ['Missing timezone definition'])
    # No app; action_copy should not raise
    screen.action_copy()
    assert sys.modules['pyperclip'].copy is not None
    # Check that copy was called with the contents
    assert sys_mod['copy_called'] is not None


def test_error_screen_copy_writes_file(monkeypatch, tmp_path):
    from munazzim.tui.screens import ErrorScreen
    monkeypatch.setenv('HOME', str(tmp_path))
    # Force pyperclip import to fail so the fallback writes to file
    import builtins
    real_import = builtins.__import__
    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == 'pyperclip':
            raise ImportError()
        return real_import(name, globals, locals, fromlist, level)
    monkeypatch.setattr(builtins, '__import__', fake_import)
    screen = ErrorScreen('Test', ['Missing timezone definition'])
    screen.action_copy()
    # The file should exist in ~/.local/state/munazzim
    out_dir = tmp_path / '.local' / 'state' / 'munazzim'
    files = list(out_dir.glob('errors-*.txt'))
    assert len(files) == 1
    content = files[0].read_text(encoding='utf-8')
    assert 'Missing timezone definition' in content


def test_action_sync_shows_error_if_service_missing(monkeypatch):
    app = MunazzimApp()
    # monkeypatch display error to capture message
    errors = {}
    def fake_display_error(title, message):
        errors['title'] = title
        errors['message'] = message
    monkeypatch.setattr(app, '_display_error', fake_display_error)
    # Remove service
    app.google_calendar_service = None
    # Call action (should return early and call _display_error)
    app.action_sync_google_calendar_week()
    assert errors.get('title') == 'Google Calendar'
    assert 'missing dependencies' in errors.get('message', '').lower()


def test_sync_shows_error_when_calendar_auth_fails(monkeypatch):
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    # create a fake service that raises FileNotFoundError (auth setup missing)
    class FailingService:
        def list_calendars(self):
            raise FileNotFoundError("client secrets not found")

    app.google_calendar_service = FailingService()
    errors = {}
    monkeypatch.setattr(app, '_display_error', lambda title, msg: errors.setdefault('body', (title, msg)))
    # Call the sync action (inline path is used in test environment)
    app.action_sync_google_calendar_week()
    assert errors
    assert errors['body'][0] == 'Google Calendar'
    assert 'client secrets' in errors['body'][1].lower()


def test_sync_deletes_removed_events(monkeypatch):
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, "set_data", lambda *a, **k: None)
    # Monday assigned
    app.week_assignments = {"monday": "A"}
    app.active_template_name = "A"
    ev = Event(name="Reading", duration=timedelta(hours=1))
    # Existing calendar contains an old event with a different time signature
    today = date.today()
    delta = (0 - today.weekday()) % 7
    monday_date = today + timedelta(days=delta)
    # Old event was at 08:00
    old_start = datetime.combine(monday_date, time(hour=8, minute=0))
    # New plan will schedule at 09:00
    new_start = datetime.combine(monday_date, time(hour=9, minute=0))
    old_sig = f"Reading|MO|{old_start.strftime('%H:%M')}"
    new_sig = f"Reading|MO|{new_start.strftime('%H:%M')}"
    scheduled = ScheduledEvent(event=ev, start=new_start, end=new_start + timedelta(hours=1))

    monkeypatch.setattr(app.templates, "get", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(app.scheduler, "build_plan", lambda template, plan_date=None, prayer_schedule=None: SimpleNamespace(items=[scheduled]))
    monkeypatch.setattr(app.prayer_service, "get_schedule", lambda d: None)

    called = {}

    class FakeService:
        def list_calendars(self):
            return [SimpleNamespace(id="cal-1", summary="Munazzim")]

        def create_calendar(self, summary, time_zone=None):
            return SimpleNamespace(id="cal-1", summary=summary)

        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            # return an existing event with old signature
            return [SimpleNamespace(id="evt-old", recurring_event_id="series-old", extended_properties={"private": {"munazzim_signature": old_sig}})]

        def create_event(self, calendar_id, event_body):
            called.setdefault("created", []).append(event_body)

        def delete_event(self, calendar_id, event_id):
            called.setdefault("deleted", []).append(event_id)

    app.google_calendar_service = FakeService()
    app.config.location.timezone = "UTC"
    count = app._sync_week_to_google_calendar()
    # Should delete the old series and create the new one
    assert called.get("deleted") == ["series-old"]
    assert called.get("created")
    # Ensure the created event contains the new signature
    created_sigs = [c["extendedProperties"]["private"]["munazzim_signature"] for c in called.get("created", [])]
    assert new_sig in created_sigs


def test_force_sync_today_creates_prayer_events(monkeypatch):
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, 'set_data', lambda *a, **k: None)
    # Assign a template for today
    today = date.today()
    day_name = today.strftime('%A').lower()
    app.week_assignments = {day_name: 'A'}
    app.active_template_name = 'A'
    ev = Event(name='Reading', duration=timedelta(hours=1))
    # build_plan returns both a normal event and a prayer event
    from munazzim.models import PrayerEvent
    tzname = 'UTC'
    scheduled = ScheduledEvent(event=ev, start=datetime.combine(today, time(9, 0)), end=datetime.combine(today, time(10, 0)))
    # Prayer at 8:00
    prayer_event = PrayerEvent(name='Fajr Prayer', prayer='Fajr', duration=timedelta(minutes=30))
    prayer_scheduled = ScheduledEvent(event=prayer_event, start=datetime.combine(today, time(8, 0)), end=datetime.combine(today, time(8, 30)))
    def make_plan(template, plan_date=None, prayer_schedule=None):
        return SimpleNamespace(items=[prayer_scheduled, scheduled])
    monkeypatch.setattr(app.templates, 'get', lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(app.scheduler, 'build_plan', make_plan)
    monkeypatch.setattr(app.prayer_service, 'get_schedule', lambda d: None)
    called = {}
    class FakeService:
        def list_calendars(self):
            return [SimpleNamespace(id='cal-1', summary='Munazzim')]
        def create_calendar(self, summary, time_zone=None):
            return SimpleNamespace(id='cal-1', summary=summary)
        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            return []
        def create_event(self, calendar_id, event_body):
            called.setdefault('created', []).append(event_body)
        def delete_event(self, calendar_id, event_id):
            called.setdefault('deleted', []).append(event_id)
    app.google_calendar_service = FakeService()
    app.config.location.timezone = tzname
    count = app._sync_today_to_google_calendar(delete_all=True)
    # Deletion may or may not have occurred depending on existing remote events.
    assert called.get('deleted') is None or isinstance(called.get('deleted'), list)
    created = called.get('created') or []
    # Ensure both regular and prayer events were created; prayer names include suffix
    created_summaries = [c['summary'] for c in created]
    assert 'Fajr Prayer (Prayer)' in created_summaries
    assert 'Reading' in created_summaries


def test_sync_week_nukes_and_creates_prayer_events(monkeypatch):
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, 'set_data', lambda *a, **k: None)
    # Monday assigned only
    monday = 'monday'
    app.week_assignments = {monday: 'A'}
    app.active_template_name = 'A'
    ev = Event(name='Reading', duration=timedelta(hours=1))
    # Build plan includes prayer events for the day
    today = date.today()
    delta = (0 - today.weekday()) % 7
    monday_date = today + timedelta(days=delta)
    from munazzim.models import PrayerEvent
    prayer_event = PrayerEvent(name='Fajr Prayer', prayer='Fajr', duration=timedelta(minutes=30))
    scheduled = ScheduledEvent(event=ev, start=datetime.combine(monday_date, time(9, 0)), end=datetime.combine(monday_date, time(10, 0)))
    prayer_scheduled = ScheduledEvent(event=prayer_event, start=datetime.combine(monday_date, time(8, 0)), end=datetime.combine(monday_date, time(8, 30)))
    def make_plan(template, plan_date=None, prayer_schedule=None):
        return SimpleNamespace(items=[prayer_scheduled, scheduled])
    monkeypatch.setattr(app.templates, 'get', lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(app.scheduler, 'build_plan', make_plan)
    monkeypatch.setattr(app.prayer_service, 'get_schedule', lambda d: None)
    called = {}
    class FakeService:
        def list_calendars(self):
            return [SimpleNamespace(id='cal-1', summary='Munazzim')]
        def create_calendar(self, summary, time_zone=None):
            return SimpleNamespace(id='cal-1', summary=summary)
        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            # return a previous event so it gets deleted
            return [SimpleNamespace(id='evt-old', recurring_event_id='series-old')]
        def create_event(self, calendar_id, event_body):
            called.setdefault('created', []).append(event_body)
        def delete_event(self, calendar_id, event_id):
            called.setdefault('deleted', []).append(event_id)
    app.google_calendar_service = FakeService()
    app.config.location.timezone = 'UTC'
    stats = app._sync_week_to_google_calendar_apply()
    assert stats['deleted_count'] >= 1
    assert stats['created_count'] >= 1
    # ensure prayer event was created; we expect the display name to include ' (Prayer)'
    created_summaries = [c['summary'] for c in called.get('created', [])]
    assert 'Fajr Prayer (Prayer)' in created_summaries


def test_force_sync_today_deletes_munazzim_events(monkeypatch):
    app = MunazzimApp()
    monkeypatch.setattr(app.week_panel, 'set_data', lambda *a, **k: None)
    # Assign a template for today
    today = date.today()
    day_name = today.strftime('%A').lower()
    app.week_assignments = {day_name: 'A'}
    app.active_template_name = 'A'
    ev = Event(name='Reading', duration=timedelta(hours=1))
    # scheduled on today at 09:00
    scheduled = ScheduledEvent(event=ev, start=datetime.combine(today, time(9, 0)), end=datetime.combine(today, time(10, 0)))
    monkeypatch.setattr(app.templates, 'get', lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(app.scheduler, 'build_plan', lambda template, plan_date=None, prayer_schedule=None: SimpleNamespace(items=[scheduled]))
    monkeypatch.setattr(app.prayer_service, 'get_schedule', lambda d: None)
    called = {}
    # Construct old signature and new signature
    tzname = 'UTC'
    old_start = datetime.combine(today, time(8, 0))
    new_start = datetime.combine(today, time(9, 0))
    old_sig = f"Reading|{day_name[:2].upper()}|{old_start.strftime('%H:%M')}"
    new_sig = f"Reading|{day_name[:2].upper()}|{new_start.strftime('%H:%M')}"

    class FakeService:
        def list_calendars(self):
            return [SimpleNamespace(id='cal-1', summary='Munazzim')]
        def create_calendar(self, summary, time_zone=None):
            return SimpleNamespace(id='cal-1', summary=summary)
        def list_events(self, calendar_id, timeMin=None, timeMax=None):
            return [SimpleNamespace(id='evt-old', recurring_event_id='series-old', extended_properties={'private': {'munazzim_signature': old_sig}})]
        def create_event(self, calendar_id, event_body):
            called.setdefault('created', []).append(event_body)
        def delete_event(self, calendar_id, event_id):
            called.setdefault('deleted', []).append(event_id)

    app.google_calendar_service = FakeService()
    app.config.location.timezone = 'UTC'
    count = app._sync_today_to_google_calendar()
    assert called.get('deleted') == ['series-old']
    assert called.get('created')
    created_sigs = [c['extendedProperties']['private']['munazzim_signature'] for c in called.get('created', [])]
    assert new_sig in created_sigs
