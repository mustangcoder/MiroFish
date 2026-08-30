from app import create_app
from app.api import model_settings
from urllib.error import URLError


class FakeMemoryBackendService:
    def __init__(self):
        self.saved = None
        self.applied = False

    def initialize_from_environment(self):
        return None

    def get_config(self):
        return {"backend": "cloud", "zep_api_key_masked": "zep***-key", "neo4j_uri": "", "neo4j_user": "", "neo4j_password_masked": ""}

    def save_config(self, config):
        self.saved = config
        return {**self.get_config(), "backend": config["backend"]}

    def apply_runtime_config(self):
        self.applied = True

    def test_connection(self, config):
        return {"backend": config["backend"], "status": "passed", "latency_ms": 12}


def test_memory_backend_api_reads_tests_and_applies_config(monkeypatch):
    service = FakeMemoryBackendService()
    monkeypatch.setattr(model_settings, "_memory_service", lambda: service)
    app = create_app()
    app.config.update(TESTING=True)
    client = app.test_client()

    get_response = client.get("/api/settings/models/memory-backend")
    test_response = client.post("/api/settings/models/memory-backend/test", json={"backend": "graphiti"})
    put_response = client.put("/api/settings/models/memory-backend", json={
        "backend": "graphiti",
        "neo4j_uri": "bolt://neo4j:7687",
        "neo4j_user": "neo4j",
        "neo4j_password": "password",
    })

    assert get_response.status_code == 200
    assert test_response.get_json()["data"]["status"] == "passed"
    assert put_response.status_code == 200
    assert service.saved["backend"] == "graphiti"
    assert service.applied is True


def test_unavailable_oauth_gateway_returns_logged_out_state(monkeypatch):
    monkeypatch.setattr(model_settings.urllib.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline")))

    data, status = model_settings._gateway_request("/account")

    assert status == 503
    assert data == {"authenticated": False, "error": "OAuth Gateway 不可用"}


def test_neo4j_localhost_failure_returns_docker_address_hint(monkeypatch):
    class FailingService(FakeMemoryBackendService):
        def test_connection(self, config):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(model_settings, "_memory_service", FailingService)
    app = create_app()
    app.config.update(TESTING=True)

    response = app.test_client().post("/api/settings/models/memory-backend/test", json={
        "backend": "graphiti",
        "neo4j_uri": "bolt://localhost:7687",
        "neo4j_user": "neo4j",
        "neo4j_password": "password",
    })

    assert response.status_code == 422
    assert "bolt://neo4j:7687" in response.get_json()["error"]
