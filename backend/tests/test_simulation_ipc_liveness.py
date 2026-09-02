import json
import os

import pytest

from app.services.simulation_ipc import SimulationIPCClient, SimulationIPCServer
from app.services.simulation_runner import SimulationRunner
from app.services.zep_tools import ZepToolsService


def test_alive_status_without_a_live_owner_is_marked_stale(tmp_path):
    status_path = tmp_path / "env_status.json"
    status_path.write_text(json.dumps({"status": "alive", "timestamp": "old"}))

    assert SimulationIPCClient(str(tmp_path)).check_env_alive() is False
    assert json.loads(status_path.read_text())["status"] == "stale"


def test_ipc_server_records_its_process_identity(tmp_path):
    server = SimulationIPCServer(str(tmp_path))

    server.start()

    status = json.loads((tmp_path / "env_status.json").read_text())
    assert status["status"] == "alive"
    assert status["pid"] == os.getpid()
    assert SimulationIPCClient(str(tmp_path)).check_env_alive() is True


def test_batch_interview_fails_before_writing_a_command_for_stale_environment(tmp_path, monkeypatch):
    simulation_dir = tmp_path / "sim-1"
    simulation_dir.mkdir()
    (simulation_dir / "env_status.json").write_text(json.dumps({"status": "alive"}))
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        SimulationIPCClient,
        "send_batch_interview",
        lambda *args, **kwargs: pytest.fail("stale environment must not receive a command"),
    )

    with pytest.raises(ValueError, match="未运行或已关闭"):
        SimulationRunner.interview_agents_batch(
            "sim-1", [{"agent_id": 1, "prompt": "question"}]
        )


def test_report_interview_skips_agent_selection_when_environment_is_stale(monkeypatch):
    service = ZepToolsService.__new__(ZepToolsService)
    monkeypatch.setattr(SimulationRunner, "check_env_alive", classmethod(lambda cls, simulation_id: False))
    monkeypatch.setattr(
        service,
        "_load_agent_profiles",
        lambda simulation_id: pytest.fail("profiles and LLM selection must be skipped"),
    )

    result = service.interview_agents("sim-1", "topic")

    assert "不可用" in result.summary


def test_startup_reconciliation_marks_stale_alive_files(tmp_path, monkeypatch):
    simulation_dir = tmp_path / "sim-1"
    simulation_dir.mkdir()
    (simulation_dir / "env_status.json").write_text(json.dumps({"status": "alive"}))
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))

    assert SimulationRunner.reconcile_stale_environment_statuses() == 1
    assert json.loads((simulation_dir / "env_status.json").read_text())["status"] == "stale"
