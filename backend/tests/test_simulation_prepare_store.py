import concurrent.futures

from app.services.simulation_prepare_store import SimulationPrepareStore


def test_create_run_reuses_the_only_active_run_for_a_simulation(tmp_path):
    store = SimulationPrepareStore(tmp_path / "mirofish.db")

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        runs = list(executor.map(
            lambda _: store.create_or_get_run(
                simulation_id="sim-1",
                task_id="task-1",
                graph_id="graph-1",
                input_fingerprint="fingerprint-1",
                total_profiles=2,
                params={"parallel": 4},
            ),
            range(8),
        ))

    assert len({run["run_id"] for run in runs}) == 1
    assert store.get_active_run("sim-1")["task_id"] == "task-1"


def test_profiles_are_saved_transactionally_and_restored_in_entity_order(tmp_path):
    store = SimulationPrepareStore(tmp_path / "mirofish.db")
    run = store.create_or_get_run(
        simulation_id="sim-1",
        task_id="task-1",
        graph_id="graph-1",
        input_fingerprint="fingerprint-1",
        total_profiles=3,
        params={},
    )

    store.save_profile(run["run_id"], "entity-b", 1, 1, "Person", {"name": "B"})
    store.save_profile(run["run_id"], "entity-a", 0, 0, "Person", {"name": "A"})

    profiles = store.load_completed_profiles(run["run_id"])
    current = store.get_run(run["run_id"])

    assert [profile["entity_uuid"] for profile in profiles] == ["entity-a", "entity-b"]
    assert [profile["profile"]["name"] for profile in profiles] == ["A", "B"]
    assert current["completed_profiles"] == 2
    assert current["total_profiles"] == 3


def test_superseding_a_run_preserves_it_and_allows_a_new_active_run(tmp_path):
    store = SimulationPrepareStore(tmp_path / "mirofish.db")
    first = store.create_or_get_run(
        simulation_id="sim-1",
        task_id="task-1",
        graph_id="graph-1",
        input_fingerprint="old",
        total_profiles=1,
        params={},
    )

    store.supersede_active("sim-1")
    second = store.create_or_get_run(
        simulation_id="sim-1",
        task_id="task-2",
        graph_id="graph-1",
        input_fingerprint="new",
        total_profiles=1,
        params={},
    )

    assert first["run_id"] != second["run_id"]
    assert store.get_run(first["run_id"])["status"] == "superseded"
    assert store.get_active_run("sim-1")["run_id"] == second["run_id"]


def test_recoverable_runs_only_include_pending_or_running_work(tmp_path):
    store = SimulationPrepareStore(tmp_path / "mirofish.db")
    active = store.create_or_get_run(
        simulation_id="sim-active",
        task_id="task-active",
        graph_id="graph-1",
        input_fingerprint="active",
        total_profiles=1,
        params={"use_llm": True},
    )
    completed = store.create_or_get_run(
        simulation_id="sim-done",
        task_id="task-done",
        graph_id="graph-2",
        input_fingerprint="done",
        total_profiles=0,
        params={},
    )
    store.update_run(completed["run_id"], status="completed", stage="completed")

    recoverable = store.list_recoverable_runs()

    assert [run["run_id"] for run in recoverable] == [active["run_id"]]
    assert recoverable[0]["params"] == {"use_llm": True}
