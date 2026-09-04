from types import SimpleNamespace

from app.models.task import TaskManager, TaskStatus
from app.services.report_generation_runner import ReportGenerationRunner
from app.services.workflow_run_store import WorkflowRunStore


class _Report:
    status = SimpleNamespace(value="completed")
    report_id = "report-1"
    error = None


class _Agent:
    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def generate_report(self, **kwargs):
        self.calls.append(kwargs)
        kwargs["progress_callback"]("generating", 60, "section")
        return _Report()


def test_runner_reuses_original_ids_and_resume_mode(tmp_path):
    database = tmp_path / "mirofishplus.db"
    TaskManager.configure_store(str(database))
    tasks = TaskManager()
    store = WorkflowRunStore(database)
    runner = ReportGenerationRunner(
        store=store, task_manager=tasks, agent_factory=_Agent,
        save_report=lambda report: None,
        register_reader=lambda *args: None, unregister_reader=lambda *args: None,
    )

    first = runner.start(
        graph_id="graph-1", simulation_id="sim-1", project_id="project-1",
        simulation_requirement="requirement", report_id="report-1",
    )
    runner.wait("report-1")

    assert first["report_id"] == "report-1"
    assert _Agent.calls[-1]["resume"] is True
    assert tasks.get_task(first["task_id"]).status is TaskStatus.COMPLETED
    assert store.get_run(first["run_id"])["status"] == "completed"


def test_recover_pending_revives_interrupted_task(tmp_path):
    database = tmp_path / "mirofishplus.db"
    TaskManager.configure_store(str(database))
    tasks = TaskManager()
    task_id = tasks.create_task("report_generate", {"report_id": "report-1"})
    tasks.update_task(task_id, status=TaskStatus.INTERRUPTED)
    store = WorkflowRunStore(database)
    store.create_or_get_run(
        resource_type="report", resource_id="report-1", task_id=task_id,
        stage="report", input_fingerprint="fp",
        checkpoint={
            "graph_id": "graph-1", "simulation_id": "sim-1",
            "project_id": "project-1", "simulation_requirement": "requirement",
            "report_id": "report-1", "locale": "zh",
        },
    )
    runner = ReportGenerationRunner(
        store=store, task_manager=tasks, agent_factory=_Agent,
        save_report=lambda report: None,
        register_reader=lambda *args: None, unregister_reader=lambda *args: None,
    )

    assert runner.recover_pending() == 1
    runner.wait("report-1")
    assert tasks.get_task(task_id).status is TaskStatus.COMPLETED
