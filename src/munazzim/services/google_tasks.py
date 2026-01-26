from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from google.auth.transport.requests import Request  # type: ignore[import]
from google.oauth2.credentials import Credentials  # type: ignore[import]
from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore[import]
from googleapiclient.discovery import build  # type: ignore[import]


SCOPES = ["https://www.googleapis.com/auth/tasks"]


@dataclass(slots=True)
class TaskList:
    id: str
    title: str


@dataclass(slots=True)
class TaskItem:
    id: str
    title: str
    due: str | None
    notes: str | None
    status: str
    recurrence: list[str] | None


class GoogleTasksService:
    """Abstraction over Google Tasks API.

    Authentication is interactive by default: an installed app flow opens the
    user's browser and requests permissions. Tokens are cached in
    ~/.config/munazzim/google_tasks_token.json (explicit path).
    """

    def __init__(self, client_secrets_path: str | Path | None = None, token_path: str | Path | None = None):
        self.client_secrets_path = Path(client_secrets_path) if client_secrets_path else Path.home() / ".config" / "munazzim" / "google_client_secret.json"
        self.token_path = Path(token_path) if token_path else Path.home() / ".config" / "munazzim" / "google_tasks_token.json"
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
                # Interactive flow: the user must have a client secrets file.
                if not self.client_secrets_path.exists():
                    raise FileNotFoundError(
                        f"Google client secrets file not found: {self.client_secrets_path}. Set up a project in the Google Cloud Console and place the JSON here."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(self.client_secrets_path), SCOPES)
                creds = flow.run_local_server(port=0)
            # persist credentials
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            with self.token_path.open("w", encoding="utf-8") as fh:
                fh.write(creds.to_json())
        self._creds = creds
        # Build service lazily and store
        self._service = build("tasks", "v1", credentials=creds)

    def list_tasklists(self) -> list[TaskList]:
        self._ensure_authenticated()
        results = self._service.tasklists().list(maxResults=100).execute()
        items = results.get("items", [])
        return [TaskList(id=item["id"], title=item.get("title", "")) for item in items]

    def create_tasklist(self, title: str) -> TaskList:
        self._ensure_authenticated()
        payload = {"title": title}
        created = self._service.tasklists().insert(body=payload).execute()
        return TaskList(id=created.get("id"), title=created.get("title", ""))

    def list_tasks(self, tasklist_id: str, show_completed: bool = True) -> list[TaskItem]:
        self._ensure_authenticated()
        # fields: id, title, due, notes, status, recurrence
        results = self._service.tasks().list(tasklist=tasklist_id, showCompleted=show_completed, maxResults=200).execute()
        items = results.get("items", [])
        output: list[TaskItem] = []
        for it in items:
            output.append(
                TaskItem(
                    id=it.get("id"),
                    title=it.get("title", ""),
                    due=it.get("due"),
                    notes=it.get("notes"),
                    status=it.get("status", "needsAction"),
                    recurrence=it.get("recurrence"),
                )
            )
        return output

    def create_task(self, tasklist_id: str, title: str, due: str | None = None, notes: str | None = None, recurrence: list[str] | None = None) -> TaskItem:
        self._ensure_authenticated()
        payload: dict = {"title": title}
        if notes:
            payload["notes"] = notes
        if due:
            payload["due"] = due
        if recurrence:
            payload["recurrence"] = recurrence
        created = self._service.tasks().insert(tasklist=tasklist_id, body=payload).execute()
        return TaskItem(
            id=created.get("id"),
            title=created.get("title", ""),
            due=created.get("due"),
            notes=created.get("notes"),
            status=created.get("status", "needsAction"),
            recurrence=created.get("recurrence"),
        )

    def update_task(self, tasklist_id: str, task_id: str, **kwargs) -> TaskItem:
        self._ensure_authenticated()
        body = {}
        for key in ("title", "notes", "due", "status", "recurrence"):
            if key in kwargs:
                body[key] = kwargs[key]
        updated = self._service.tasks().patch(tasklist=tasklist_id, task=task_id, body=body).execute()
        return TaskItem(
            id=updated.get("id"),
            title=updated.get("title", ""),
            due=updated.get("due"),
            notes=updated.get("notes"),
            status=updated.get("status", "needsAction"),
            recurrence=updated.get("recurrence"),
        )

    def delete_task(self, tasklist_id: str, task_id: str) -> None:
        self._ensure_authenticated()
        self._service.tasks().delete(tasklist=tasklist_id, task=task_id).execute()
