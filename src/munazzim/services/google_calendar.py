from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from google.auth.transport.requests import Request  # type: ignore[import]
from google.oauth2.credentials import Credentials  # type: ignore[import]
from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import]
from googleapiclient.discovery import build  # type: ignore[import]


SCOPES = ["https://www.googleapis.com/auth/calendar"]


@dataclass(slots=True)
class Calendar:
    id: str
    summary: str


@dataclass(slots=True)
class CalendarEvent:
    id: str
    summary: str
    start: Mapping | None
    end: Mapping | None
    recurrence: list[str] | None
    extended_properties: Mapping | None
    recurring_event_id: str | None = None


class GoogleCalendarService:
    def __init__(self, client_secrets_path: str | Path | None = None, token_path: str | Path | None = None):
        self.client_secrets_path = Path(client_secrets_path) if client_secrets_path else Path.home() / ".config" / "munazzim" / "google_client_secret.json"
        self.token_path = Path(token_path) if token_path else Path.home() / ".config" / "munazzim" / "google_calendar_token.json"
        self._creds: Credentials | None = None
        self._service = None

    def _ensure_authenticated(self) -> None:
        if self._service is not None:
            return
        creds = None
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.client_secrets_path.exists():
                    raise FileNotFoundError(
                        f"Google client secrets file not found: {self.client_secrets_path}. Set up a project in the Google Cloud Console and place the JSON here."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(self.client_secrets_path), SCOPES)
                creds = flow.run_local_server(port=0)
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            with self.token_path.open("w", encoding="utf-8") as fh:
                fh.write(creds.to_json())
        self._creds = creds
        self._service = build("calendar", "v3", credentials=creds)

    def list_calendars(self) -> list[Calendar]:
        self._ensure_authenticated()
        results = self._service.calendarList().list(maxResults=200).execute()
        items = results.get("items", [])
        return [Calendar(id=item["id"], summary=item.get("summary", "")) for item in items]

    def create_calendar(self, summary: str, time_zone: str | None = None) -> Calendar:
        self._ensure_authenticated()
        body = {"summary": summary}
        if time_zone:
            body["timeZone"] = time_zone
        created = self._service.calendars().insert(body=body).execute()
        return Calendar(id=created.get("id"), summary=created.get("summary", ""))

    def list_events(self, calendar_id: str = "primary", timeMin: str | None = None, timeMax: str | None = None) -> list[CalendarEvent]:
        self._ensure_authenticated()
        kwargs: dict = {"calendarId": calendar_id}
        if timeMin:
            kwargs["timeMin"] = timeMin
        if timeMax:
            kwargs["timeMax"] = timeMax
        # List event instances in the time range so recurring events are expanded
        kwargs["singleEvents"] = True
        kwargs["orderBy"] = "startTime"
        results = self._service.events().list(**kwargs).execute()
        items = results.get("items", [])
        out: list[CalendarEvent] = []
        for it in items:
            out.append(
                CalendarEvent(
                    id=it.get("id"),
                    summary=it.get("summary", ""),
                    start=it.get("start"),
                    end=it.get("end"),
                    recurrence=it.get("recurrence"),
                    extended_properties=it.get("extendedProperties"),
                    recurring_event_id=it.get("recurringEventId"),
                )
            )
        return out

    def create_event(self, calendar_id: str, event_body: dict) -> CalendarEvent:
        self._ensure_authenticated()
        created = self._service.events().insert(calendarId=calendar_id, body=event_body).execute()
        return CalendarEvent(
            id=created.get("id"),
            summary=created.get("summary", ""),
            start=created.get("start"),
            end=created.get("end"),
            recurrence=created.get("recurrence"),
            extended_properties=created.get("extendedProperties"),
        )

    def update_event(self, calendar_id: str, event_id: str, body: dict) -> CalendarEvent:
        self._ensure_authenticated()
        updated = self._service.events().patch(calendarId=calendar_id, eventId=event_id, body=body).execute()
        return CalendarEvent(
            id=updated.get("id"),
            summary=updated.get("summary", ""),
            start=updated.get("start"),
            end=updated.get("end"),
            recurrence=updated.get("recurrence"),
            extended_properties=updated.get("extendedProperties"),
        )

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        self._ensure_authenticated()
        self._service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
