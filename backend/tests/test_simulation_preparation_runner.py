import threading
from types import SimpleNamespace

from app.models.task import TaskManager, TaskStatus
from app.services.simulation_prepare_store import SimulationPrepareStore
from app.services.simulation_preparation_runner import SimulationPreparationRunner
from app.services.simulation_manager import SimulationStatus
from app.services.workflow_run_store import WorkflowRunStore


class _ProjectManager:
    project = SimpleNamespace(simulation_requirement="Study the event")

    @classmethod
    def get_project(cls, project_id):
        return cls.project if project_id == "project-1" else None

    @staticmethod
    def get_extracted_text(project_id):
        return "Document"


class _Manager:
    def __init__(self, gate=None):
        self.gate = gate
        self.calls = 0
        self.state = SimpleNamespace(
            simulation_id="sim-1",
            project_id="project-1",
            graph_id="graph-1",
            entities_count=2,
            status=SimulationStatus.CREATED,
            to_simple_dict=lambda: {"simulation_id": "sim-1", "status": "ready"},
        )

    def get_simulation(self, simulation_id):
        return self.state if simulation_id == "sim-1" else None

    def prepare_simulation(self, **kwargs):
        self.calls += 1
        if self.gate:
            self.gate.wait(2)
        self.state.status = SimulationStatus.READY
        return self.state


def _runner(tmp_path, manager):
    TaskManager.configure_store(str(tmp_path / "mirofish.db"))
    return SimulationPreparationRunner(
        store=SimulationPrepareStore(tmp_path / "mirofish.db"),
        task_manager=TaskManager(),
        manager_factory=lambda: manager,
        project_manager=_ProjectManager,
        workflow_store=WorkflowRunStore(tmp_path / "mirofish.db"),
    )


def test_start_reuses_task_and_only_runs_one_worker_per_simulation(tmp_path):
    gate = threading.Event()
    manager = _Manager(gate)
    runner = _runner(tmp_path, manager)

    first = runner.start("sim-1", parallel_profile_count=4)
    second = runner.start("sim-1", parallel_profile_count=8)

    assert first["task_id"] == second["task_id"]
    assert second["reused"] is True
    gate.set()
    assert runner.wait("sim-1", timeout=3)
    assert manager.calls == 1


def test_start_revives_the_persisted_interrupted_task(tmp_path):
    manager = _Manager()
    runner = _runner(tmp_path, manager)
    task_id = runner.task_manager.create_task(
        "simulation_prepare", {"simulation_id": "sim-1", "project_id": "project-1"}
    )
    runner.task_manager.update_task(task_id, status=TaskStatus.INTERRUPTED)
    runner.store.create_or_get_run(
        simulation_id="sim-1",
        task_id=task_id,
        graph_id="graph-1",
        input_fingerprint="fingerprint",
        total_profiles=2,
        params={"entity_types": None, "use_llm_for_profiles": True, "parallel_profile_count": 5},
    )

    result = runner.start("sim-1")

    assert result["task_id"] == task_id
    assert runner.wait("sim-1", timeout=3)
    assert runner.task_manager.get_task(task_id).status is TaskStatus.COMPLETED


def test_startup_recovery_resumes_all_persisted_active_runs(tmp_path):
    manager = _Manager()
    runner = _runner(tmp_path, manager)
    task_id = runner.task_manager.create_task(
        "simulation_prepare", {"simulation_id": "sim-1", "project_id": "project-1"}
    )
    runner.task_manager.update_task(task_id, status=TaskStatus.INTERRUPTED)
    runner.store.create_or_get_run(
        simulation_id="sim-1",
        task_id=task_id,
        graph_id="graph-1",
        input_fingerprint="fingerprint",
        total_profiles=2,
        params={"entity_types": ["Person"], "use_llm_for_profiles": False, "parallel_profile_count": 3},
    )

    assert runner.recover_pending() == 1
    assert runner.wait("sim-1", timeout=3)
    assert manager.calls == 1


def test_startup_recovery_does_not_abort_when_a_simulation_was_removed(tmp_path):
    manager = _Manager()
    runner = _runner(tmp_path, manager)
    task_id = runner.task_manager.create_task(
        "simulation_prepare", {"simulation_id": "sim-missing", "project_id": "project-1"}
    )
    runner.store.create_or_get_run(
        simulation_id="sim-missing",
        task_id=task_id,
        graph_id="graph-missing",
        input_fingerprint="fingerprint",
        total_profiles=2,
        params={},
    )

    assert runner.recover_pending() == 0
    assert runner.store.get_active_run("sim-missing") is None
    assert runner.task_manager.get_task(task_id).status is TaskStatus.FAILED


def test_start_reports_deferred_when_previous_process_lease_has_not_expired(tmp_path):
    manager = _Manager()
    runner = _runner(tmp_path, manager)
    task_id = runner.task_manager.create_task(
        "simulation_prepare", {"simulation_id": "sim-1", "project_id": "project-1"}
    )
    active = runner.store.create_or_get_run(
        simulation_id="sim-1",
        task_id=task_id,
        graph_id="graph-1",
        input_fingerprint="fingerprint",
        total_profiles=2,
        params={},
    )
    workflow = runner.workflow_store.create_or_get_run(
        resource_type="simulation",
        resource_id="sim-1",
        task_id=task_id,
        stage="prepare",
        input_fingerprint=active["input_fingerprint"],
        checkpoint={},
    )
    assert runner.workflow_store.acquire_lease(workflow["run_id"], "old-process", 60)

    result = runner.start("sim-1")

    assert result["deferred"] is True
    assert manager.calls == 0
