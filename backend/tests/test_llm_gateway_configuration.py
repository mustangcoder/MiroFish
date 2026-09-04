from pathlib import Path


def _read_env_template():
    path = Path(__file__).resolve().parents[2] / ".env.production.example"
    values = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def test_text_llm_uses_direct_gateway_while_embedding_stays_local():
    values = _read_env_template()

    assert values["LLM_BASE_URL"] == "http://chatgpt-oauth-gateway:8090/v1"
    assert values["LLM_MODEL_NAME"] == values["DIRECT_CODEX_MODEL"]
    assert values["GRAPHITI_LLM_MODEL"] == values["DIRECT_CODEX_MODEL"]
    assert values["GRAPHITI_EMBEDDING_BASE_URL"] == "http://embedding:80/v1"
    assert not any(key.startswith("CODEX_GATEWAY") or key.startswith("FALLBACK_LLM") for key in values)


def test_backend_depends_on_healthy_chatgpt_gateway():
    path = Path(__file__).resolve().parents[2] / "docker-compose.production.yml"
    compose = path.read_text()
    backend_section = compose.split("\n  backend:\n", 1)[1].split("\n  neo4j:", 1)[0]

    assert "chatgpt-oauth-gateway:" in backend_section
    assert "condition: service_healthy" in backend_section


def test_local_compose_runs_gateway_with_shared_internal_token():
    path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    compose = path.read_text()

    assert "chatgpt-oauth-gateway:" in compose
    assert "DIRECT_OAUTH_GATEWAY_URL: http://chatgpt-oauth-gateway:8090" in compose
    assert compose.count("DIRECT_GATEWAY_TOKEN: ${DIRECT_GATEWAY_TOKEN:-mirofish-local-only}") == 2
    assert "condition: service_healthy" in compose


def test_local_compose_persists_and_prefetches_huggingface_models():
    path = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    compose = path.read_text()

    assert "hf-prefetch:" in compose
    assert "huggingface_cache:/var/lib/mirofish/huggingface" in compose
    assert "HF_HOME: /var/lib/mirofish/huggingface" in compose
    assert "HF_HUB_DISABLE_XET: \"1\"" in compose
    assert "HF_MODEL_DOWNLOAD_TIMEOUT_SECONDS: 900" in compose
    assert "TRANSFORMERS_CACHE: /var/lib/mirofish/huggingface/hub" in compose
    assert 'command: ["timeout", "900"' in compose


def test_graphiti_operation_timeout_supports_long_running_builds():
    root = Path(__file__).resolve().parents[2]
    values = _read_env_template()

    assert values["GRAPHITI_OPERATION_TIMEOUT_SECONDS"] == "3600"


def test_codex_gateway_is_not_a_runtime_provider():
    root = Path(__file__).resolve().parents[2]
    runtime_text = "\n".join([
        (root / "frontend/src/views/ModelSettingsView.vue").read_text(),
        (root / "backend/app/models/model_config.py").read_text(),
        (root / "backend/app/services/model_connection_tester.py").read_text(),
        (root / "docker-compose.production.yml").read_text(),
        (root / ".env.production.example").read_text(),
    ])

    assert "codex_gateway" not in runtime_text
    assert "codex-gateway" not in runtime_text
    assert "Codex Gateway" not in runtime_text


def test_obsolete_codex_gateway_source_and_deployment_docs_are_removed():
    root = Path(__file__).resolve().parents[2]

    assert not (root / "codex_gateway").exists()
    assert not (root / "docs/deployment/codex-subscription.md").exists()
    assert not (root / "docs/superpowers/specs/2026-08-27-codex-subscription-provider-design.md").exists()
    assert not (root / "docs/superpowers/plans/2026-08-27-codex-subscription-provider.md").exists()


def test_graph_build_batch_size_restores_three_way_throughput():
    root = Path(__file__).resolve().parents[2]
    graph_api = (root / "backend" / "app" / "api" / "graph.py").read_text()
    values = _read_env_template()

    assert "batch_size=Config.GRAPH_BUILD_BATCH_SIZE" in graph_api
    assert values["GRAPH_BUILD_BATCH_SIZE"] == "3"
