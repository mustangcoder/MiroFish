from flask import Flask

from app.api import simulation as simulation_api


def test_batch_interview_failure_surfaces_top_level_error(monkeypatch):
    monkeypatch.setattr(
        simulation_api.SimulationRunner,
        "check_env_alive",
        classmethod(lambda _cls, _simulation_id: True),
    )
    monkeypatch.setattr(
        simulation_api.SimulationRunner,
        "interview_agents_batch",
        classmethod(lambda _cls, **_kwargs: {
            "success": False,
            "error": "provider circuit is open",
            "interviews_count": 1,
        }),
    )
    app = Flask(__name__)

    with app.test_request_context(
        "/api/simulation/interview/batch",
        method="POST",
        json={
            "simulation_id": "sim-1",
            "interviews": [{"agent_id": 19, "prompt": "question"}],
        },
    ):
        response, status = simulation_api.interview_agents_batch()

    body = response.get_json()
    assert status == 422
    assert body["success"] is False
    assert body["error"] == "provider circuit is open"
    assert body["data"]["interviews_count"] == 1
