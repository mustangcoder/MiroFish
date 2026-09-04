import json
import sqlite3
from types import SimpleNamespace

from app.services.simulation_round_checkpoint import SimulationRoundCheckpoint
from app.services.simulation_runner import RunnerStatus, SimulationRunState, SimulationRunner


def _write_database(path, values):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS events(value TEXT)")
        connection.execute("DELETE FROM events")
        connection.executemany("INSERT INTO events(value) VALUES (?)", [(value,) for value in values])


def test_commit_and_restore_return_to_last_complete_round(tmp_path):
    database = tmp_path / "twitter_simulation.db"
    actions = tmp_path / "twitter" / "actions.jsonl"
    actions.parent.mkdir()
    _write_database(database, ["round-1"])
    actions.write_text(json.dumps({"round": 1}) + "\n", encoding="utf-8")
    checkpoints = SimulationRoundCheckpoint(tmp_path)

    checkpoints.commit("twitter", 1, 3, database, actions)
    _write_database(database, ["round-1", "incomplete-round-2"])
    with actions.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"round": 2}) + "\n")
        stream.write('{"round":')

    restored = checkpoints.restore("twitter", database, actions)

    assert restored == {"completed_round": 1, "total_actions": 3}
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM events").fetchall() == [("round-1",)]
    assert actions.read_text(encoding="utf-8") == json.dumps({"round": 1}) + "\n"


def test_platform_checkpoints_do_not_overwrite_each_other(tmp_path):
    checkpoints = SimulationRoundCheckpoint(tmp_path)
    for platform in ("twitter", "reddit"):
        database = tmp_path / f"{platform}_simulation.db"
        actions = tmp_path / platform / "actions.jsonl"
        actions.parent.mkdir()
        actions.write_text("", encoding="utf-8")
        _write_database(database, [platform])
        checkpoints.commit(platform, 4, 7, database, actions)

    payload = json.loads((tmp_path / "round_checkpoint.json").read_text(encoding="utf-8"))
    assert payload["platforms"]["twitter"]["completed_round"] == 4
    assert payload["platforms"]["reddit"]["completed_round"] == 4


def test_restore_without_checkpoint_keeps_new_run_untouched(tmp_path):
    database = tmp_path / "twitter_simulation.db"
    actions = tmp_path / "twitter" / "actions.jsonl"

    assert SimulationRoundCheckpoint(tmp_path).restore("twitter", database, actions) is None
    assert not database.exists()
    assert not actions.exists()


def test_runner_recovers_stale_run_with_original_options(tmp_path, monkeypatch):
    simulation_id = "sim-recover"
    simulation_dir = tmp_path / simulation_id
    simulation_dir.mkdir()
    state = SimulationRunState(
        simulation_id=simulation_id,
        runner_status=RunnerStatus.RUNNING,
        total_rounds=40,
        platform="parallel",
        max_rounds=40,
        graph_memory_update_enabled=True,
        graph_id="graph-1",
    )
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    SimulationRunner._save_run_state(state)
    (simulation_dir / "round_checkpoint.json").write_text(
        json.dumps({
            "version": 1,
            "platforms": {
                "twitter": {"completed_round": 12},
                "reddit": {"completed_round": 11},
            },
        }),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(
        SimulationRunner,
        "start_simulation",
        classmethod(lambda cls, **kwargs: calls.append(kwargs) or SimpleNamespace()),
    )

    result = SimulationRunner.recover_interrupted_simulations()

    assert result == {simulation_id: "round:11"}
    assert calls == [{
        "simulation_id": simulation_id,
        "platform": "parallel",
        "max_rounds": 40,
        "enable_graph_memory_update": True,
        "graph_id": "graph-1",
        "resume_from_round": 11,
    }]
