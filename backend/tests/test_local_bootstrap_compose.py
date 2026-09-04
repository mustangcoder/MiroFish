from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def test_main_compose_requires_successful_bootstrap_before_app_start():
    config = _load("docker-compose.yml")
    bootstrap = config["services"]["bootstrap"]
    app = config["services"]["mirofish"]

    assert config["name"] == "mirofishplus"
    assert bootstrap["container_name"] == "mirofishplus-bootstrap"
    assert app["container_name"] == "mirofishplus"
    assert app["build"]["dockerfile"] == "Dockerfile"
    assert bootstrap["restart"] == "no"
    assert bootstrap["command"] == [
        "uv", "run", "--project", "backend", "python",
        "backend/scripts/bootstrap_local.py",
    ]
    assert "${MIROFISH_UPLOADS_DIR:-./backend/uploads}:/app/backend/uploads" in bootstrap["volumes"]
    assert app["depends_on"]["bootstrap"]["condition"] == "service_completed_successfully"
    assert app["healthcheck"]["test"][-1] == "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5001/health', timeout=3)"


def test_local_compose_keeps_neo4j_version_and_injects_container_address():
    config = _load("docker-compose.local.yml")
    services = config["services"]

    assert services["neo4j"]["image"] == "neo4j:5.26.0"
    assert services["neo4j"]["container_name"] == "mirofishplus-neo4j"
    for service_name in ("bootstrap", "mirofish"):
        service = services[service_name]
        assert service["environment"]["ZEP_BACKEND"] == "graphiti"
        assert service["environment"]["NEO4J_URI"] == "bolt://neo4j:7687"
        assert service["depends_on"]["neo4j"]["condition"] == "service_healthy"


def test_compose_uses_mirofishplus_container_and_volume_names():
    main = _load("docker-compose.yml")
    local = _load("docker-compose.local.yml")

    gateway = main["services"]["chatgpt-oauth-gateway"]
    assert gateway["container_name"] == "mirofishplus-chatgpt-oauth-gateway"
    assert gateway["image"] == "mirofishplus-chatgpt-oauth-gateway:latest"
    assert main["services"]["hf-prefetch"]["container_name"] == "mirofishplus-hf-prefetch"
    assert main["volumes"]["chatgpt_oauth_credentials"]["name"] == "mirofishplus_chatgpt_oauth_credentials"
    assert main["volumes"]["huggingface_cache"]["name"] == "mirofishplus_huggingface_cache"
    assert local["volumes"]["neo4j_data"]["name"] == "mirofishplus_neo4j_data"
    assert local["volumes"]["neo4j_logs"]["name"] == "mirofishplus_neo4j_logs"


def test_production_compose_uses_mirofishplus_names():
    production = _load("docker-compose.production.yml")

    assert production["name"] == "mirofishplus"
    assert {
        service["container_name"]
        for service in production["services"].values()
    } == {
        "mirofishplus-chatgpt-oauth-gateway",
        "mirofishplus-web",
        "mirofishplus-backend",
        "mirofishplus-neo4j",
        "mirofishplus-embedding",
    }
    assert {volume["name"] for volume in production["volumes"].values()} == {
        "mirofishplus_chatgpt_oauth_credentials",
        "mirofishplus_neo4j_data",
        "mirofishplus_neo4j_logs",
        "mirofishplus_embedding_cache",
        "mirofishplus_uploads",
    }


def test_local_start_migrates_existing_oauth_credentials_to_new_volume():
    source = (ROOT / "scripts/start-local.sh").read_text(encoding="utf-8")

    assert "mirofishplus-direct-oauth-gateway" in source
    assert (
        'migrate-docker-volume.sh" mirofishplus_direct_oauth_credentials '
        "mirofishplus_chatgpt_oauth_credentials"
    ) in source
    assert '"${COMPOSE[@]}" up -d --build --remove-orphans' in source
