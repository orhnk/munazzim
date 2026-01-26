from types import SimpleNamespace
from datetime import datetime, time, timedelta, date
from munazzim.tui.app import MunazzimApp
from munazzim.models import Event, ScheduledEvent

app = MunazzimApp()
app.week_panel.set_data = lambda *a, **k: None
app.week_assignments = {"monday":"A"}
app.active_template_name='A'
ev = Event(name='Reading', duration=timedelta(hours=1))
app.templates.get = lambda n: SimpleNamespace(name=n)
app.scheduler.build_plan = lambda tpl, plan_date=None, prayer_schedule=None: SimpleNamespace(items=[ScheduledEvent(event=ev, start=datetime.combine(date.today(), time(8,0)), end=datetime.combine(date.today(), time(9,0)))])
app.prayer_service.get_schedule = lambda d: None

print('Before tz:', getattr(app.config.location, 'timezone', None))

class CapturingService:
    def list_calendars(self):
        print('list_calendars called')
        return []
    def create_calendar(self, summary, time_zone=None):
        print('create_calendar called with', summary, time_zone)
        return SimpleNamespace(id='cal-1', summary=summary)
    def list_events(self, calendar_id, timeMin=None, timeMax=None):
        print('list_events called', calendar_id, timeMin, timeMax)
        return []
    def create_event(self, calendar_id, event_body):
        print('create_event called with payload keys', list(event_body.keys()))
        return SimpleNamespace(id='evt-1')

app.google_calendar_service = CapturingService()
app.config.location.timezone = 'UTC'

print('Running sync...')
app._sync_week_to_google_calendar()
print('Finished')
