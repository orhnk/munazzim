from __future__ import annotations

from datetime import date, datetime, time, timedelta
import tempfile
import unittest
from pathlib import Path

from munazzim.models import DayPlan, DayTemplate, Event, ScheduledEvent, Task
from munazzim.tasks import TaskAssignmentEngine, TaskStore, TaskDefinition


class _FakeRepository:
    def __init__(self, templates: list[DayTemplate]) -> None:
        self._templates = {template.name: template for template in templates}

    def template_names(self) -> list[str]:
        return list(self._templates.keys())

    def get(self, name: str) -> DayTemplate:
        return self._templates[name]


class TaskAssignmentEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        reading_event = Event(
            name="Reading",
            duration=timedelta(hours=1),
            tasks=[
                Task(label="Read Empire of Cotton", total_occurrences=3, remaining_occurrences=3),
            ],
        )
        writing_event = Event(
            name="Writing",
            duration=timedelta(minutes=45),
            tasks=[
                Task(label="Reflective journal", total_occurrences=None, remaining_occurrences=None),
            ],
        )
        self.repository = _FakeRepository(
            [
                DayTemplate(name="Template A", start_time=time(5, 0), description="", events=[reading_event]),
                DayTemplate(
                    name="Template B",
                    start_time=time(6, 0),
                    description="",
                    events=[Event(name="Reading", duration=timedelta(minutes=30)), writing_event],
                ),
            ]
        )
        self._tmpdir = tempfile.TemporaryDirectory()
        store_path = Path(self._tmpdir.name) / "tasks.json"
        self.engine = TaskAssignmentEngine(self.repository, TaskStore(store_path))

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_cross_template_task_assignment(self) -> None:
        template = self.repository.get("Template B")
        annotated = self.engine.annotate_template(template)
        reading_event = annotated.events[0]
        self.assertEqual(reading_event.name, "Reading")
        self.assertEqual(len(reading_event.tasks), 1)
        task = reading_event.tasks[0]
        self.assertEqual(task.label, "Read Empire of Cotton")
        self.assertEqual(task.remaining_occurrences, 3)
        self.assertEqual(task.total_occurrences, 3)

    def test_completion_updates_remaining_occurrences(self) -> None:
        template = self.repository.get("Template B")
        first_pass = self.engine.annotate_template(template)
        task = first_pass.events[0].tasks[0]
        self.assertIsNotNone(task.task_id)
        task_id = task.task_id or ""
        self.engine.complete_task(task_id)
        second_pass = self.engine.annotate_template(template)
        updated_task = second_pass.events[0].tasks[0]
        self.assertEqual(updated_task.remaining_occurrences, 2)
        self.assertEqual(updated_task.completed_occurrences, 1)

    def test_open_tasks_record_last_completed(self) -> None:
        template = self.repository.get("Template B")
        annotated = self.engine.annotate_template(template)
        open_task = annotated.events[1].tasks[0]
        self.assertIsNotNone(open_task.task_id)
        self.assertIsNone(open_task.last_completed)
        task_id = open_task.task_id or ""
        self.engine.complete_task(task_id)
        refreshed = self.engine.annotate_template(template)
        updated = refreshed.events[1].tasks[0]
        self.assertGreaterEqual(updated.completed_occurrences, 1)
        self.assertEqual(updated.last_completed, date.today())

    def test_open_task_can_be_unlogged(self) -> None:
        template = self.repository.get("Template B")
        annotated = self.engine.annotate_template(template)
        open_task = annotated.events[1].tasks[0]
        self.assertIsNotNone(open_task.task_id)
        task_id = open_task.task_id or ""
        # mark it done
        self.engine.complete_task(task_id)
        refreshed = self.engine.annotate_template(template)
        updated = refreshed.events[1].tasks[0]
        self.assertIsNotNone(updated.last_completed)
        # clear the last completed marker
        self.engine.unlog_task(task_id)
        refreshed_again = self.engine.annotate_template(template)
        updated_again = refreshed_again.events[1].tasks[0]
        self.assertIsNone(updated_again.last_completed)

    def test_plan_occurrences_limit_assignments(self) -> None:
        template = self.repository.get("Template A")
        annotated = self.engine.annotate_template(template)
        reading_event = annotated.events[0]
        plan_day = date(2025, 1, 1)
        plan = DayPlan(template_name="Template A", generated_for=plan_day)
        start = datetime.combine(plan_day, time(6, 0))
        for offset in range(4):
            scheduled = ScheduledEvent(
                event=reading_event,
                start=start + timedelta(minutes=offset * 30),
                end=start + timedelta(minutes=offset * 30 + 30),
            )
            plan.add(scheduled)
        occurrences = self.engine.plan_occurrences(plan)
        self.assertEqual(len(occurrences), 3)
        self.assertEqual([occ.ordinal for occ in occurrences], [1, 2, 3])

    def test_assignment_toggle_updates_store(self) -> None:
        template = self.repository.get("Template A")
        annotated = self.engine.annotate_template(template)
        reading_event = annotated.events[0]
        plan_day = date(2025, 1, 2)
        plan = DayPlan(template_name="Template A", generated_for=plan_day)
        start = datetime.combine(plan_day, time(7, 0))
        scheduled = ScheduledEvent(
            event=reading_event,
            start=start,
            end=start + timedelta(minutes=30),
        )
        plan.add(scheduled)
        occurrences = self.engine.plan_occurrences(plan)
        self.assertEqual(len(occurrences), 1)
        occurrence = occurrences[0]
        self.assertIsNotNone(occurrence.assignment_id)
        assignment_id = occurrence.assignment_id or ""
        self.assertFalse(occurrence.checked)
        self.engine.toggle_assignment(assignment_id, True)
        refreshed = self.engine.plan_occurrences(plan)
        self.assertTrue(refreshed[0].checked)
        self.engine.toggle_assignment(assignment_id, False)
        refreshed_again = self.engine.plan_occurrences(plan)
        self.assertFalse(refreshed_again[0].checked)


    def test_load_tasks_from_taskdir(self) -> None:
        # Create a temporary directory that acts as ~/.config/munazzim
        import tempfile
        from pathlib import Path

        # We no longer read tasks from the local taskdir. Instead, the
        # engine can merge task definitions supplied by an external provider.
        # Simulate this by constructing TaskDefinition objects and injecting
        # them into the engine._tasks_by_event mapping.
        reading_event = Event(
            name="Reading",
            duration=timedelta(hours=1),
            tasks=[],
        )
        repo = _FakeRepository([DayTemplate(name="T", start_time=time(5, 0), description="", events=[reading_event])])
        engine = TaskAssignmentEngine(repo, TaskStore(Path(self._tmpdir.name) / "other-tasks.json"))
        # Inject definitions mimicking what would have been parsed previously
        defs = [
            TaskDefinition(task_id="t-1", event_name="Reading", label="Finish Lord of the Rings", note=None, total_occurrences=20, stage=1),
            TaskDefinition(task_id="t-2", event_name="Reading", label="Clean your table", note=None, total_occurrences=None, stage=1),
            TaskDefinition(task_id="t-3", event_name="Reading", label="Second stage task", note=None, total_occurrences=10, stage=2),
        ]
        engine._tasks_by_event["Reading"] = defs
        engine._definitions_by_id.update({d.task_id: d for d in defs})
        # No refresh: we've injected definitions directly
        tasks = engine.tasks_for_event_name("Reading")
        labels = [t.label for t in tasks]
        self.assertIn("Finish Lord of the Rings", labels)
        self.assertIn("Clean your table", labels)
        # Clean is an infinite task, so it should have None total_occurrences
        clean_task = next(t for t in tasks if t.label == "Clean your table")
        self.assertIsNone(clean_task.total_occurrences)

    def test_stage_blocking(self) -> None:
        reading_event = Event(
            name="Reading",
            duration=timedelta(hours=1),
            tasks=[],
        )
        repo = _FakeRepository([DayTemplate(name="T", start_time=time(5, 0), description="", events=[reading_event])])
        engine = TaskAssignmentEngine(repo, TaskStore(Path(self._tmpdir.name) / "other-tasks.json"))
        # Inject staged definitions like the old file parser would have produced
        defs2 = [
            TaskDefinition(task_id="sa", event_name="Reading", label="Step A", note=None, total_occurrences=1, stage=1),
            TaskDefinition(task_id="sb", event_name="Reading", label="Step B", note=None, total_occurrences=1, stage=2),
        ]
        engine._tasks_by_event["Reading"] = defs2
        engine._definitions_by_id.update({d.task_id: d for d in defs2})

        # Create plan with a single scheduled reading event
        plan_day = date(2025, 1, 2)
        plan = DayPlan(template_name="Test", generated_for=plan_day)
        start = datetime.combine(plan_day, time(7, 0))
        plan.add(ScheduledEvent(event=reading_event, start=start, end=start + timedelta(minutes=30)))

        occurrences = engine.plan_occurrences(plan)
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].label, "Step A")

        # Complete the first stage
        tasks = engine.tasks_for_event_name("Reading")
        all_tasks_labels = [t.label for t in tasks]
        self.assertIn("Step B", all_tasks_labels)
        first = next(t for t in tasks if t.label == "Step A")
        engine.complete_task(first.task_id or "")
        # Ensure the progress store recorded completion
        self.assertGreaterEqual(engine.store.completed(first.task_id or ""), 1)
        # Sanity-check the definitions loaded
        defns = engine._tasks_by_event.get("Reading", [])
        stages = {d.label: d.stage for d in defns}
        totals = {d.label: d.total_occurrences for d in defns}
        self.assertIn("Step A", stages)
        self.assertEqual(stages["Step A"], 1)
        self.assertEqual(totals["Step A"], 1)
        self.assertEqual(stages.get("Step B"), 2)
        defA = next(d for d in defns if d.label == "Step A")
        self.assertGreaterEqual(engine.store.completed(defA.task_id), defA.total_occurrences or 0)

        # Recompute the active stage in the test to verify it becomes 2
        staged = [d for d in defns if d.stage is not None]
        stages = sorted({d.stage for d in staged if d.stage is not None})
        active_stage = None
        for s in stages:
            defs = [d for d in staged if d.stage == s]
            if not all((engine.store.completed(d.task_id) >= (d.total_occurrences or 0) if d.total_occurrences is not None else engine.store.progress(d.task_id).last_completed is not None) for d in defs):
                active_stage = s
                break
        self.assertEqual(active_stage, 2)
        filtered = [d for d in defns if d.stage is None or d.stage == active_stage]
        labels = [d.label for d in filtered]
        self.assertIn("Step B", labels)
        defB = next(d for d in defns if d.label == "Step B")
        self.assertEqual(engine.store.assignment_count(defB.task_id), 0)

        occurrences = engine.plan_occurrences(plan)
        # When stage 1 completes, stage 2 tasks are shown and stage 1 is
        # hidden. Ensure the stage-2 task is present and the stage-1 task
        # is not shown.
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].label, "Step B")