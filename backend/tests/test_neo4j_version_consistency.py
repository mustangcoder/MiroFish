from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"


def test_neo4j_version_is_consistent_across_dependencies_and_containers():
    pyproject = tomllib.loads((BACKEND / "pyproject.toml").read_text())
    lock = tomllib.loads((BACKEND / "uv.lock").read_text())

    dependency_versions = {
        dependency.removeprefix("neo4j==")
        for dependency in pyproject["project"]["dependencies"]
        if dependency.startswith("neo4j==")
    }
    lock_versions = {
        package["version"]
        for package in lock["package"]
        if package["name"] == "neo4j"
    }
    container_versions = set()
    for compose_name in ("docker-compose.local.yml", "docker-compose.production.yml"):
        compose = (ROOT / compose_name).read_text()
        match = re.search(r"image:\s*neo4j:([^\s]+)", compose)
        assert match is not None, f"{compose_name} 缺少 Neo4j 镜像版本"
        container_versions.add(match.group(1))

    assert len(dependency_versions) == 1
    assert lock_versions == dependency_versions
    assert container_versions == dependency_versions


def test_simulation_uses_the_backend_python_environment():
    runner = (BACKEND / "app/services/simulation_runner.py").read_text()

    assert "_get_simulation_python" not in runner
    assert ".venv-simulation" not in runner
    assert "sys.executable" in runner
