from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace, field
from datetime import date, datetime
from hashlib import sha1
import json
from pathlib import Path

from .config import _default_config_root
from .models import DayPlan, DayTemplate, Event, Task, ScheduledEvent


@dataclass(slots=True)
class TaskDefinition:
    task_id: str
    event_name: str
    label: str
    note: str | None
    total_occurrences: int | None
    # Optional stage index for staged tasks; None means no stage blocking
    stage: int | None = None


@dataclass(slots=True)
class TaskProgress:
    completed: int = 0
    last_completed: date | None = None
    assignments: dict[str, "TaskAssignmentRecord"] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"completed": self.completed}
        if self.last_completed is not None:
            payload["last_completed"] = self.last_completed.isoformat()
        if self.assignments:
            payload["assignments"] = {
                assignment_id: record.to_dict()
                for assignment_id, record in self.assignments.items()
            }
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "TaskProgress":
        raw_completed = payload.get("completed", 0)
        try:
            completed = int(raw_completed)
        except (TypeError, ValueError):  # pragma: no cover - defensive parsing
            completed = 0
        raw_date = payload.get("last_completed")
        parsed_date: date | None = None
        if isinstance(raw_date, str) and raw_date:
            try:
                parsed_date = datetime.fromisoformat(raw_date).date()
            except ValueError:  # pragma: no cover - defensive parsing
                parsed_date = None
        assignments_payload = payload.get("assignments", {})
        assignments: dict[str, TaskAssignmentRecord] = {}
        if isinstance(assignments_payload, Mapping):
            for assignment_id, record_payload in assignments_payload.items():
                if isinstance(assignment_id, str) and isinstance(record_payload, Mapping):
                    assignments[assignment_id] = TaskAssignmentRecord.from_dict(record_payload)
        return cls(completed=completed, last_completed=parsed_date, assignments=assignments)


@dataclass(slots=True)
class TaskAssignmentRecord:
    completed: bool = False
    completed_at: date | None = None
    ordinal: int = 0
    event_label: str | None = None
    day: date | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "completed": self.completed,
            "ordinal": self.ordinal,
        }
        if self.completed_at is not None:
            payload["completed_at"] = self.completed_at.isoformat()
        if self.event_label:
            payload["event_label"] = self.event_label
        if self.day is not None:
            payload["day"] = self.day.isoformat()
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "TaskAssignmentRecord":
        completed = bool(payload.get("completed", False))
        ordinal_payload = payload.get("ordinal", 0)
        try:
            ordinal = int(ordinal_payload)
        except (TypeError, ValueError):  # pragma: no cover - defensive parsing
            ordinal = 0
        completed_at: date | None = None
        raw_completed_at = payload.get("completed_at")
        if isinstance(raw_completed_at, str) and raw_completed_at:
            try:
                completed_at = datetime.fromisoformat(raw_completed_at).date()
            except ValueError:  # pragma: no cover - defensive parsing
                completed_at = None
        day: date | None = None
        raw_day = payload.get("day")
        if isinstance(raw_day, str) and raw_day:
            try:
                day = datetime.fromisoformat(raw_day).date()
            except ValueError:  # pragma: no cover - defensive parsing
                day = None
        event_label = payload.get("event_label") if isinstance(payload.get("event_label"), str) else None
        return cls(
            completed=completed,
            completed_at=completed_at,
            ordinal=ordinal,
            event_label=event_label,
            day=day,
        )


@dataclass(slots=True)
class PlanTaskOccurrence:
    task_id: str
    label: str
    note: str | None
    event_label: str
    total_occurrences: int | None
    ordinal: int | None
    assignment_id: str | None
    checked: bool
    last_completed: date | None


class TaskStore:
    """Persists task completion counts under the user's config directory."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (_default_config_root() / "taskbook.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, TaskProgress] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._records.clear()
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):  # pragma: no cover - defensive parsing
            self._records.clear()
            return
        tasks_payload = data.get("tasks", {})
        if not isinstance(tasks_payload, dict):
            self._records.clear()
            return
        self._records = {
            task_id: TaskProgress.from_dict(payload)
            for task_id, payload in tasks_payload.items()
            if isinstance(task_id, str) and isinstance(payload, Mapping)
        }

    def save(self) -> None:
        serialised = {
            "tasks": {task_id: progress.to_dict() for task_id, progress in self._records.items()}
        }
        self.path.write_text(json.dumps(serialised, indent=2, sort_keys=True), encoding="utf-8")

    def prune(self, known_ids: Iterable[str]) -> None:
        known = set(known_ids)
        removed = False
        for task_id in list(self._records.keys()):
            if task_id not in known:
                self._records.pop(task_id, None)
                removed = True
        if removed:
            self.save()

    def completed(self, task_id: str) -> int:
        return self._records.get(task_id, TaskProgress()).completed

    def progress(self, task_id: str) -> TaskProgress:
        current = self._records.get(task_id)
        if current is None:
            return TaskProgress()
        return TaskProgress(
            completed=current.completed,
            last_completed=current.last_completed,
            assignments={
                key: TaskAssignmentRecord(
                    completed=record.completed,
                    completed_at=record.completed_at,
                    ordinal=record.ordinal,
                    event_label=record.event_label,
                    day=record.day,
                )
                for key, record in current.assignments.items()
            },
        )

    def increment(self, task_id: str, *, maximum: int | None = None, step: int = 1) -> None:
        if step <= 0:
            return
        progress = self._records.setdefault(task_id, TaskProgress())
        new_total = progress.completed + step
        if maximum is not None:
            new_total = min(new_total, maximum)
        if new_total == progress.completed:
            return
        progress.completed = new_total
        progress.last_completed = date.today()
        self.save()

    def set_task_completed(self, task_id: str, completed: bool) -> None:
        """Set completed state for non-assignment (open) tasks.

        This records or clears the last_completed timestamp and adjusts the
        simple completed counter so UI can show/check a single "Done" box.
        """
        progress = self._records.setdefault(task_id, TaskProgress())
        currently = progress.last_completed is not None
        if currently == completed:
            return
        if completed:
            progress.completed = progress.completed + 1
            progress.last_completed = date.today()
        else:
            progress.completed = max(progress.completed - 1, 0)
            progress.last_completed = None
        self.save()

    def assignment_count(self, task_id: str) -> int:
        progress = self._records.get(task_id)
        if not progress:
            return 0
        return len(progress.assignments)

    def assignment_record(self, task_id: str, assignment_id: str) -> TaskAssignmentRecord | None:
        progress = self._records.get(task_id)
        if not progress:
            return None
        return progress.assignments.get(assignment_id)

    def ensure_assignment(
        self,
        task_id: str,
        assignment_id: str,
        *,
        ordinal: int,
        day: date,
        event_label: str,
    ) -> TaskAssignmentRecord:
        progress = self._records.setdefault(task_id, TaskProgress())
        record = progress.assignments.get(assignment_id)
        if record is None:
            record = TaskAssignmentRecord(
                completed=False,
                completed_at=None,
                ordinal=ordinal,
                event_label=event_label,
                day=day,
            )
            progress.assignments[assignment_id] = record
            self.save()
        return record

    def set_assignment_completed(self, task_id: str, assignment_id: str, completed: bool) -> None:
        progress = self._records.setdefault(task_id, TaskProgress())
        record = progress.assignments.get(assignment_id)
        if record is None:
            return
        if record.completed == completed:
            return
        record.completed = completed
        record.completed_at = date.today() if completed else None
        delta = 1 if completed else -1
        progress.completed = max(progress.completed + delta, 0)
        if completed:
            progress.last_completed = record.completed_at
        else:
            progress.last_completed = self._latest_completed_date(progress)
        self.save()

    def _latest_completed_date(self, progress: TaskProgress) -> date | None:
        latest: date | None = None
        for record in progress.assignments.values():
            if record.completed and record.completed_at:
                if latest is None or record.completed_at > latest:
                    latest = record.completed_at
        return latest


class TaskAssignmentEngine:
    """Aggregates tasks from templates and provides per-event assignments."""

    def __init__(self, repository, store: TaskStore | None = None, external_task_provider: object | None = None) -> None:
        self.repository = repository
        self.store = store or TaskStore()
        self._tasks_by_event: dict[str, list[TaskDefinition]] = {}
        self._definitions_by_id: dict[str, TaskDefinition] = {}
        # Optional external provider (e.g. GoogleTasksService) used for
        # integrating cloud-managed task lists as external per-event tasks.
        self.external_task_provider = external_task_provider
        self.refresh()

    def refresh(self) -> None:
        aggregate: dict[str, dict[str, TaskDefinition]] = {}
        definitions_by_id: dict[str, TaskDefinition] = {}
        for template_name in self.repository.template_names():
            template = self.repository.get(template_name)
            for event in template.events:
                for definition in self._definitions_from_event(event):
                    aggregate.setdefault(event.name, {})[definition.task_id] = definition
                    definitions_by_id[definition.task_id] = definition
        # Merge per-event tasks from any external provider (e.g. Google Tasks)
        # into the aggregate so cloud-managed tasks appear in the UI and
        # in TaskAssignmentEngine outputs. This replaces the previous local
        # filesystem-based approach.
        try:
            if self.external_task_provider is not None:
                self._load_tasks_from_google(aggregate, definitions_by_id)
        except Exception:
            pass
        self._tasks_by_event = {key: list(definitions.values()) for key, definitions in aggregate.items()}
        self._definitions_by_id = definitions_by_id
        # Done above
        self.store.prune(definitions_by_id.keys())

    def annotate_template(self, template: DayTemplate) -> DayTemplate:
        events = [self._attach_to_event(event) for event in template.events]
        return replace(template, events=events)

    def tasks_for_event_name(self, name: str) -> list[Task]:
        definitions = self._tasks_by_event.get(name)
        if not definitions:
            return []
        return [self._task_from_definition(defn) for defn in definitions]

    def complete_task(self, task_id: str, *, step: int = 1) -> None:
        definition = self._definitions_by_id.get(task_id)
        if not definition:
            return
        self.store.increment(task_id, maximum=definition.total_occurrences, step=step)

    def plan_occurrences(self, plan: DayPlan) -> list[PlanTaskOccurrence]:
        occurrences: list[PlanTaskOccurrence] = []
        for index, scheduled in enumerate(plan.items):
            definitions = self._tasks_by_event.get(scheduled.event.name)
            if not definitions:
                continue
            # Stage blocking: some tasks are authored with a stage index. Only
            # include tasks for the active stage; stages are applied only when
            # tasks list contains stage annotations. Tasks with stage=None are
            # always included.
            staged = [d for d in definitions if d.stage is not None]
            if staged:
                # Group by stage and {1,2,..} find the first incomplete stage
                stages = sorted({d.stage for d in staged if d.stage is not None})

                def _is_completed(d: TaskDefinition) -> bool:
                    # For counted tasks, require that the completed counter
                    # reached total_occurrences. For open tasks, consider an
                    # item completed if there's any last_completed date.
                    if d.total_occurrences is not None:
                        return self.store.completed(d.task_id) >= d.total_occurrences
                    progress = self.store.progress(d.task_id)
                    return progress.last_completed is not None

                active_stage: int | None = None
                for s in stages:
                    defs = [d for d in staged if d.stage == s]
                    if not all(_is_completed(d) for d in defs):
                        active_stage = s
                        break
                # If all stages were completed, allow everything.
                if active_stage is not None:
                    # Filter definitions to those in the active stage or those
                    # without a stage annotation. This keeps other stages hidden
                    # until the prior one completes.
                    definitions = [d for d in definitions if d.stage is None or d.stage == active_stage]
            event_label = scheduled.display_name
            for definition in definitions:
                if definition.total_occurrences is None:
                    progress = self.store.progress(definition.task_id)
                    occurrences.append(
                        PlanTaskOccurrence(
                            task_id=definition.task_id,
                            label=definition.label,
                            note=definition.note,
                            event_label=event_label,
                            total_occurrences=None,
                            ordinal=None,
                            assignment_id=None,
                            # Consider 'checked' true when the last completion
                            # date equals the plan day — this allows toggling
                            # the open task on/off per day.
                            checked=(progress.last_completed == plan.generated_for),
                            last_completed=progress.last_completed,
                        )
                    )
                    continue
                assignment_id = self._assignment_id(definition.task_id, plan.generated_for, scheduled, index)
                record = self.store.assignment_record(definition.task_id, assignment_id)
                if record is None:
                    assigned_count = self.store.assignment_count(definition.task_id)
                    if assigned_count >= definition.total_occurrences:
                        continue
                    record = self.store.ensure_assignment(
                        definition.task_id,
                        assignment_id,
                        ordinal=assigned_count + 1,
                        day=plan.generated_for,
                        event_label=event_label,
                    )
                occurrences.append(
                    PlanTaskOccurrence(
                        task_id=definition.task_id,
                        label=definition.label,
                        note=definition.note,
                        event_label=event_label,
                        total_occurrences=definition.total_occurrences,
                        ordinal=record.ordinal or None,
                        assignment_id=assignment_id,
                        checked=record.completed,
                        last_completed=record.completed_at,
                    )
                )
        return occurrences

    def toggle_assignment(self, assignment_id: str, completed: bool) -> None:
        task_id = self._task_id_from_assignment(assignment_id)
        if not task_id:
            return
        self.store.set_assignment_completed(task_id, assignment_id, completed)

    def unlog_task(self, task_id: str) -> None:
        """Clear the last-completed marker for an open (non-counted) task."""
        self.store.set_task_completed(task_id, False)

    def _attach_to_event(self, event: Event) -> Event:
        assigned = self.tasks_for_event_name(event.name)
        if assigned:
            return replace(event, tasks=assigned)
        if not event.tasks:
            return event
        cloned = [
            Task(
                label=task.label,
                note=task.note,
                total_occurrences=task.total_occurrences,
                remaining_occurrences=task.remaining_occurrences,
                task_id=task.task_id,
                completed_occurrences=task.completed_occurrences,
                last_completed=task.last_completed,
            )
            for task in event.tasks
        ]
        return replace(event, tasks=cloned)

    def _task_from_definition(self, definition: TaskDefinition) -> Task:
        progress = self.store.progress(definition.task_id)
        remaining: int | None = None
        if definition.total_occurrences is not None:
            remaining = max(definition.total_occurrences - progress.completed, 0)
        return Task(
            label=definition.label,
            note=definition.note,
            total_occurrences=definition.total_occurrences,
            remaining_occurrences=remaining,
            task_id=definition.task_id,
            completed_occurrences=progress.completed,
            last_completed=progress.last_completed,
        )

    def _definitions_from_event(self, event: Event) -> Iterable[TaskDefinition]:
        for task in event.tasks:
            label = task.label.strip()
            note = task.note.strip() if task.note else None
            total = task.total_occurrences
            if total is None:
                total = task.remaining_occurrences
            task_id = task.task_id or self._derive_task_id(event.name, label, note)
            yield TaskDefinition(
                task_id=task_id,
                event_name=event.name,
                label=label or "Task",
                note=note,
                total_occurrences=total,
                stage=None,
            )

    def _load_tasks_from_google(self, aggregate: dict[str, dict[str, TaskDefinition]], definitions_by_id: dict[str, TaskDefinition]) -> None:
        """Load tasks from a Google Tasks provider and merge them into the
        aggregate mapping as TaskDefinition objects.

        Each Google Tasks 'tasklist' is considered an event if its title
        matches an event name. Each task in the list becomes a TaskDefinition
        keyed by a unique id that includes the provider context to avoid
        collisions with template-defined ids.
        """
        # Avoid importing the provider's type at module import time; it's
        # injected as a runtime object. Guard if provider is not set.
        svc = self.external_task_provider
        if svc is None:
            return
        try:
            lists = svc.list_tasklists()
        except Exception:
            return
        # Iterate lists and their items to create TaskDefinitions for each
        for l in lists:
            title = getattr(l, "title", None)
            if not title:
                continue
            try:
                items = svc.list_tasks(l.id)
            except Exception:
                continue
            for it in items:
                # Derive a stable unique id for Google tasks so we can
                # track progress separately from template-defined ids.
                gid = getattr(it, "id", None)
                if not gid:
                    continue
                # Create an identifier that explicitly marks the provider
                # and list that owns the task. This prevents collisions
                # with template-derived ids and makes it easy to route
                # updates back to the API.
                task_id = f"google:{l.id}:{gid}"
                label = getattr(it, "title", "Task") or "Task"
                note = getattr(it, "notes", None) or None
                # For recurring tasks, keep them as open tasks (no count)
                total = None
                # Avoid overwriting template-defined tasks with the same
                # logical label; use id-based uniqueness.
                defn = TaskDefinition(task_id=task_id, event_name=title, label=label, note=note, total_occurrences=total, stage=None)
                aggregate.setdefault(title, {})[task_id] = defn
                definitions_by_id[task_id] = defn

    # Previously the engine supported reading per-event task files from the
    # local configuration directory. That behavior has been removed in favor
    # of cloud-backed task lists (e.g., Google Tasks). To preserve a clear
    # migration path, the engine no longer references local files here.

    def parse_event_file(self, event_name: str) -> list[TaskDefinition]:
        """Return the TaskDefinition objects currently known for the
        given event. Previously this parsed a local file; since external
        task lists are now used, return whatever definitions the engine
        has merged from templates and external providers.
        """
        return list(self._tasks_by_event.get(event_name, []))

    def _derive_task_id(self, event_name: str, label: str, note: str | None) -> str:
        slug = f"{event_name}\u241f{label}\u241f{note or ''}".encode("utf-8")
        digest = sha1(slug).hexdigest()
        return f"task-{digest}"

    def _assignment_id(
        self,
        task_id: str,
        plan_date: date,
        scheduled: ScheduledEvent,
        index: int,
    ) -> str:
        signature = "|".join(
            [
                task_id,
                plan_date.isoformat(),
                scheduled.start.isoformat(),
                scheduled.end.isoformat(),
                scheduled.event.name,
                str(index),
            ]
        )
        digest = sha1(signature.encode("utf-8")).hexdigest()
        return f"{task_id}:{digest}"

    def _task_id_from_assignment(self, assignment_id: str) -> str | None:
        if ":" not in assignment_id:
            return None
        task_id, _ = assignment_id.split(":", 1)
        if not task_id:
            return None
        if task_id not in self._definitions_by_id:
            return None
        return task_id


def annotate_plan_template(template: DayTemplate, engine: TaskAssignmentEngine) -> DayTemplate:
    """Helper for situations where dependency injection is inconvenient."""

    return engine.annotate_template(template)
