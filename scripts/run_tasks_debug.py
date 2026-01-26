from types import SimpleNamespace
from pathlib import Path
from datetime import datetime, time, timedelta, date
from munazzim.tui.app import MunazzimApp
from munazzim.models import Event, ScheduledEvent

app = MunazzimApp()
app.week_panel.set_data = lambda *a, **k: None
# stub plan_table because refresh_plan expects it
app.plan_table = SimpleNamespace(cursor_row=0, _row_metadata=[], clear=lambda *a, **k: None, add_row=lambda *a, **k: None, row_count=0, jump_to_row=lambda *a, **k: None)

# set schedule
app.week_assignments = {}
# Keep the app default active_template_name so the scheduler can build a plan

# Event scheduled today
now = datetime.now()
ev = Event(name='Reading', duration=timedelta(hours=1))
scheduled = ScheduledEvent(event=ev, start=now, end=now)
app.scheduler.build_plan = lambda template, plan_date=None, prayer_schedule=None: SimpleNamespace(items=[scheduled])
app.prayer_service.get_schedule = lambda d: None

class FakeService:
    def list_tasklists(self):
        print('list_tasklists called')
        return [SimpleNamespace(id='L', title='Reading')]
    def list_tasks(self, list_id, show_completed=True):
        print('list_tasks called for', list_id)
        return [SimpleNamespace(id='t1', title='Read book', notes=None, due=None, status='needsAction')]

app.google_tasks_service = FakeService()
app.selected_google_tasklist = 'L'
# Hook week_panel.set_todos to capture
captured = {}
def capture_todos(items):
    print('set_todos called, items length', len(items) if items else 0)
    captured.setdefault('todos', items)
app.week_panel.set_todos = capture_todos

# Debug: show template names, active template and planner result before refreshing
print('selected_google_tasklist:', app.selected_google_tasklist)
print('google_tasks_service truthiness:', bool(app.google_tasks_service))
print('templates:', app.templates.template_names())
print('active_template_name:', app.active_template_name)
try:
    tmpl = app.templates.get(app.active_template_name)
    print('template.present=', getattr(tmpl, 'name', None))
except Exception as e:
    print('template.get error', e)
try:
    plan = app.scheduler.build_plan(app.templates.get(app.active_template_name))
    print('plan items:', len(getattr(plan, 'items', [])))
except Exception as e:
    print('build_plan error', e)
print('Direct list_tasks call result:', app.google_tasks_service.list_tasks(app.selected_google_tasklist))
# Use actual templates from repository to simulate test environment
try:
    del app.templates.template_names
except Exception:
    pass
try:
    del app.templates.get
except Exception:
    pass
app.refresh_plan()
print('captured todos:', captured.get('todos'))
