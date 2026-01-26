import os
from pathlib import Path
from typing import Any

import pytest

from munazzim.services.google_tasks import GoogleTasksService, TaskItem, TaskList


class FakeTasksAPI:
    def __init__(self, resp):
        self._resp = resp

    def tasklists(self):
        return self

    def list(self, **kwargs):
        return self

    def execute(self):
        return self._resp

    def tasks(self):
        return self

    def insert(self, **kwargs):
        # return created resource mirroring payload
        body = kwargs.get("body", {})
        return FakeTasksAPI({"id": "c1", "title": body.get("title"), **body})

    def patch(self, **kwargs):
        body = kwargs.get("body", {})
        return FakeTasksAPI({"id": kwargs.get("task"), **body})

    def delete(self, **kwargs):
        return FakeTasksAPI({})


def test_list_tasklists_uses_api(monkeypatch, tmp_path: Path) -> None:
    svc = GoogleTasksService(client_secrets_path=str(tmp_path / "creds.json"), token_path=str(tmp_path / "token.json"))
    fake = FakeTasksAPI({"items": [{"id": "l1", "title": "List 1"}]})

    # Patch ensure auth to avoid interactive flow and set a fake service
    monkeypatch.setattr(svc, "_ensure_authenticated", lambda: setattr(svc, "_service", fake))

    lists = svc.list_tasklists()
    assert isinstance(lists, list)
    assert lists[0].id == "l1"
    assert lists[0].title == "List 1"


def test_create_update_and_delete_task(monkeypatch, tmp_path: Path) -> None:
    svc = GoogleTasksService(client_secrets_path=str(tmp_path / "creds.json"), token_path=str(tmp_path / "token.json"))
    fake = FakeTasksAPI({})
    monkeypatch.setattr(svc, "_ensure_authenticated", lambda: setattr(svc, "_service", fake))

    created = svc.create_task("l1", title="Buy milk", due="2025-12-31T10:00:00Z", notes="1L", recurrence=["RRULE:FREQ=DAILY;COUNT=3"])  # type: ignore[arg-type]
    # Insert returns object with id and passed fields
    assert created.title == "Buy milk"
    assert created.due == "2025-12-31T10:00:00Z"
    assert created.notes == "1L"
    assert isinstance(created.recurrence, list)

    updated = svc.update_task("l1", "c1", title="Buy almond milk", status="completed")
    assert updated.title == "Buy almond milk"

    # delete_task should not raise
    svc.delete_task("l1", "c1")


# Verify that trying to authenticate without a client secret raises helpful error
def test_ensure_auth_requires_client_secrets(tmp_path: Path) -> None:
    svc = GoogleTasksService(client_secrets_path=str(tmp_path / "missing.json"), token_path=str(tmp_path / "token.json"))
    # The actual _ensure_authenticated would open a flow; since file doesn't exist it should raise FileNotFoundError
    with pytest.raises(FileNotFoundError):
        svc._ensure_authenticated()
