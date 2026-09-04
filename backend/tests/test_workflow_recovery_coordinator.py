import threading

from app.models.task import TaskManager, TaskStatus
from app.services.workflow_recovery_coordinator import WorkflowRecoveryCoordinator
from app.services.workflow_run_store import WorkflowRunStore


def test_coordinator_recovers_in_dependency_order_and_isolates_failures(tmp_path):
    database = tmp_path / "mirofishplus.db"
    TaskManager.configure_store(str(database))
    tasks = TaskManager()
    store = WorkflowRunStore(database)
    calls = []
    for stage in ("report", "graph", "ontology"):
        task_id = tasks.create_task(stage, {"resource_id": stage})
        store.create_or_get_run(
            resource_type="project", resource_id=stage, task_id=task_id,
            stage=stage, input_fingerprint=stage, checkpoint={},
        )

    coordinator = WorkflowRecoveryCoordinator(store=store, task_manager=tasks, max_concurrency=1)
    coordinator.register("ontology", lambda run: calls.append("ontology"))
    coordinator.register("graph", lambda run: (_ for _ in ()).throw(RuntimeError("broken graph")))
    coordinator.register("report", lambda run: calls.append("report"))

    summary = coordinator.recover_pending(wait=True)

    assert calls == ["ontology", "report"]
    assert summary.scheduled == 3
    assert summary.failed == 1
    assert summary.completed == 2


def test_task_is_revived_with_original_id(tmp_path):
    database = tmp_path / "mirofishplus.db"
    TaskManager.configure_store(str(database))
    manager = TaskManager()
    task_id = manager.create_task("simulation", {})
    manager.update_task(task_id, status=TaskStatus.INTERRUPTED)

    assert manager.revive_task(task_id, "正在从检查点恢复", {"checkpoint_current": 4}) is True
    task = manager.get_task(task_id)
    assert task.task_id == task_id
    assert task.status is TaskStatus.PROCESSING
    assert task.progress_detail["checkpoint_current"] == 4


def test_two_coordinators_cannot_run_the_same_checkpoint(tmp_path):
    database = tmp_path / "mirofishplus.db"
    TaskManager.configure_store(str(database))
    tasks = TaskManager()
    store = WorkflowRunStore(database)
    task_id = tasks.create_task("report", {})
    store.create_or_get_run(
        resource_type="report", resource_id="report-1", task_id=task_id,
        stage="report", input_fingerprint="fp", checkpoint={},
    )
    gate = threading.Event()
    calls = []

    def handler(run):
        calls.append(run["run_id"])
        gate.wait(2)

    first = WorkflowRecoveryCoordinator(store=store, task_manager=tasks)
    second = WorkflowRecoveryCoordinator(store=WorkflowRunStore(database), task_manager=tasks)
    first.register("report", handler)
    second.register("report", handler)

    first.recover_pending(wait=False)
    second.recover_pending(wait=False)
    gate.set()
    first.wait()
    second.wait()

    assert len(calls) == 1
