from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def test_main_compose_requires_successful_bootstrap_before_app_start():
    config = _load("docker-compose.yml")
    bootstrap = config["services"]["bootstrap"]
    app = config["services"]["mirofish"]

    assert config["name"] == "mirofish"
    assert app["build"]["dockerfile"] == "Dockerfile"
    assert bootstrap["restart"] == "no"
    assert bootstrap["command"] == [
        "uv", "run", "--project", "backend", "python",
        "backend/scripts/bootstrap_local.py",
    ]
    assert "./backend/uploads:/app/backend/uploads" in bootstrap["volumes"]
    assert app["depends_on"]["bootstrap"]["condition"] == "service_completed_successfully"
    assert app["healthcheck"]["test"][-1] == "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/health', timeout=3)"


def test_local_compose_keeps_neo4j_version_and_injects_container_address():
    config = _load("docker-compose.local.yml")
    services = config["services"]

    assert services["neo4j"]["image"] == "neo4j:5.26.0"
    for service_name in ("bootstrap", "mirofish"):
        service = services[service_name]
        assert service["environment"]["ZEP_BACKEND"] == "graphiti"
        assert service["environment"]["NEO4J_URI"] == "bolt://neo4j:7687"
        assert service["depends_on"]["neo4j"]["condition"] == "service_healthy"
