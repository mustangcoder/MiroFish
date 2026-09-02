import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _fixture(tmp_path, *, existing_env=None):
    for name in ("docker-compose.yml", "docker-compose.local.yml", ".env.example"):
        shutil.copy(ROOT / name, tmp_path / name)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    if (ROOT / "scripts/start-local.sh").exists():
        shutil.copy(ROOT / "scripts/start-local.sh", scripts / "start-local.sh")
    if existing_env is not None:
        (tmp_path / ".env").write_bytes(existing_env)
    return tmp_path


def _fake_tools(tmp_path, *, docker_ready=True, curl_ready=True):
    calls = tmp_path / "calls.log"
    docker = tmp_path / "fake-docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$CALLS_FILE\"\n"
        + ("exit 1\n" if not docker_ready else "")
        + "if [[ \"$1\" == \"inspect\" ]]; then printf '%s\\n' \"${FAKE_BOOTSTRAP_EXIT:-0}\"; fi\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    curl = tmp_path / "fake-curl"
    curl.write_text(
        "#!/usr/bin/env bash\n" + ("exit 0\n" if curl_ready else "exit 1\n"),
        encoding="utf-8",
    )
    curl.chmod(0o755)
    return docker, curl, calls


def _run(project, docker, curl, calls, *, bootstrap_exit="0"):
    environment = {
        **os.environ,
        "DOCKER_BIN": str(docker),
        "CURL_BIN": str(curl),
        "CALLS_FILE": str(calls),
        "STARTUP_HEALTH_ATTEMPTS": "1",
        "STARTUP_HEALTH_INTERVAL": "0",
        "FAKE_BOOTSTRAP_EXIT": bootstrap_exit,
        "SKIP_LEGACY_DOCKER_MIGRATION": "1",
    }
    return subprocess.run(
        ["bash", "scripts/start-local.sh"],
        cwd=project,
        env=environment,
        text=True,
        capture_output=True,
    )


def test_missing_env_is_created_and_compose_stack_is_started(tmp_path):
    project = _fixture(tmp_path)
    docker, curl, calls = _fake_tools(tmp_path)

    result = _run(project, docker, curl, calls)

    assert result.returncode == 0, result.stderr
    assert (project / ".env").read_bytes() == (project / ".env.example").read_bytes()
    recorded = calls.read_text()
    assert "compose -f docker-compose.yml -f docker-compose.local.yml up -d --build" in recorded
    assert "inspect -f {{.State.ExitCode}} mirofishplus-bootstrap" in recorded


def test_existing_env_is_not_overwritten(tmp_path):
    original = b"CUSTOM_VALUE=keep-me\n"
    project = _fixture(tmp_path, existing_env=original)
    docker, curl, calls = _fake_tools(tmp_path)

    result = _run(project, docker, curl, calls)

    assert result.returncode == 0
    assert (project / ".env").read_bytes() == original


def test_unavailable_docker_fails_before_compose(tmp_path):
    project = _fixture(tmp_path)
    docker, curl, calls = _fake_tools(tmp_path, docker_ready=False)

    result = _run(project, docker, curl, calls)

    assert result.returncode != 0
    assert "Docker" in result.stderr
    assert "compose" not in calls.read_text()


def test_bootstrap_failure_returns_nonzero_and_prints_logs_command(tmp_path):
    project = _fixture(tmp_path)
    docker, curl, calls = _fake_tools(tmp_path)

    result = _run(project, docker, curl, calls, bootstrap_exit="7")

    assert result.returncode != 0
    assert "数据库初始化失败" in result.stderr
    assert "logs --tail=200 bootstrap" in calls.read_text()


def test_health_timeout_returns_nonzero_and_preserves_diagnostics(tmp_path):
    project = _fixture(tmp_path)
    docker, curl, calls = _fake_tools(tmp_path, curl_ready=False)

    result = _run(project, docker, curl, calls)

    assert result.returncode != 0
    assert "健康检查超时" in result.stderr
    assert "compose -f docker-compose.yml -f docker-compose.local.yml ps -a" in calls.read_text()
