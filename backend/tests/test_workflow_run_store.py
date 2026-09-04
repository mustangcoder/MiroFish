import concurrent.futures
from datetime import datetime, timedelta, timezone

import pytest

from app.services.workflow_run_store import WorkflowRunStore


def _create(store, resource_id="sim-1"):
    return store.create_or_get_run(
        resource_type="simulation",
        resource_id=resource_id,
        task_id="task-1",
        stage="simulation",
        input_fingerprint="fp-1",
        checkpoint={"current": 3, "total": 40},
    )


def test_only_one_active_run_is_created_concurrently(tmp_path):
    store = WorkflowRunStore(tmp_path / "mirofishplus.db")

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        runs = list(pool.map(lambda _: _create(store), range(12)))

    assert len({run["run_id"] for run in runs}) == 1


def test_lease_requires_owner_and_can_be_taken_after_expiry(tmp_path):
    now = [datetime(2026, 9, 3, tzinfo=timezone.utc)]
    store = WorkflowRunStore(tmp_path / "mirofishplus.db", clock=lambda: now[0])
    run = _create(store)

    assert store.acquire_lease(run["run_id"], "worker-a", 30) is True
    assert store.acquire_lease(run["run_id"], "worker-b", 30) is False
    assert store.heartbeat(run["run_id"], "worker-b", {"current": 4}) is False

    now[0] += timedelta(seconds=31)
    assert store.acquire_lease(run["run_id"], "worker-b", 30) is True
    assert store.heartbeat(run["run_id"], "worker-b", {"current": 4}) is True


def test_checkpoint_events_are_ordered_and_recoverable(tmp_path):
    store = WorkflowRunStore(tmp_path / "mirofishplus.db")
    run = _create(store)

    first = store.append_checkpoint(run["run_id"], "round_completed", {"round": 3})
    second = store.append_checkpoint(run["run_id"], "round_completed", {"round": 4})

    assert (first, second) == (1, 2)
    assert [event["sequence"] for event in store.list_checkpoint_events(run["run_id"])] == [1, 2]
    assert [item["run_id"] for item in store.list_recoverable()] == [run["run_id"]]


def test_sensitive_checkpoint_keys_are_rejected(tmp_path):
    store = WorkflowRunStore(tmp_path / "mirofishplus.db")

    with pytest.raises(ValueError, match="敏感字段"):
        store.create_or_get_run(
            resource_type="report",
            resource_id="report-1",
            task_id="task-1",
            stage="report",
            input_fingerprint="fp",
            checkpoint={"nested": {"api_key": "must-not-persist"}},
        )
