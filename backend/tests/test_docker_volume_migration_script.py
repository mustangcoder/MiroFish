import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _fake_docker(tmp_path):
    executable = tmp_path / "docker"
    executable.write_text(
        """#!/usr/bin/env python3
import os, pathlib, shutil, sys
root = pathlib.Path(os.environ['FAKE_VOLUME_ROOT'])
args = sys.argv[1:]
if args[:2] == ['volume', 'inspect']:
    raise SystemExit(0 if (root / args[2]).is_dir() else 1)
if args[:2] == ['volume', 'create']:
    (root / args[-1]).mkdir(parents=True, exist_ok=True)
    print(args[-1])
    raise SystemExit(0)
if args and args[0] == 'run':
    mounts = [args[i + 1] for i, value in enumerate(args) if value == '-v']
    source = root / mounts[0].split(':')[0] if len(mounts) > 1 else None
    target = root / mounts[-1].split(':')[0]
    command = args[-1]
    marker = target / '.mirofishplus_migration_complete'
    if 'test -f' in command:
        raise SystemExit(0 if marker.exists() else 1)
    if 'find /target' in command and 'cp -a' not in command:
        entries = [item for item in target.iterdir() if item.name != marker.name]
        raise SystemExit(0 if not entries else 1)
    if 'cp -a' in command:
        shutil.copytree(source, target, dirs_exist_ok=True)
        marker.touch()
        raise SystemExit(0)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _run(tmp_path, old, new):
    docker = _fake_docker(tmp_path)
    environment = {**os.environ, "DOCKER_BIN": str(docker), "FAKE_VOLUME_ROOT": str(tmp_path / "volumes")}
    return subprocess.run(
        ["bash", str(ROOT / "scripts/migrate-docker-volume.sh"), old, new],
        env=environment,
        text=True,
        capture_output=True,
    )


def test_missing_source_is_a_noop(tmp_path):
    (tmp_path / "volumes").mkdir()

    result = _run(tmp_path, "old", "new")

    assert result.returncode == 0
    assert not (tmp_path / "volumes/new").exists()


def test_existing_source_is_copied_and_retained(tmp_path):
    source = tmp_path / "volumes/old"
    (source / "nested").mkdir(parents=True)
    (source / "nested/data.txt").write_text("graph data")
    (source / ".hidden").write_text("secret state")

    result = _run(tmp_path, "old", "new")

    assert result.returncode == 0, result.stderr
    assert (source / "nested/data.txt").read_text() == "graph data"
    assert (tmp_path / "volumes/new/nested/data.txt").read_text() == "graph data"
    assert (tmp_path / "volumes/new/.hidden").read_text() == "secret state"
    assert (tmp_path / "volumes/new/.mirofishplus_migration_complete").exists()


def test_unmarked_nonempty_target_is_not_overwritten(tmp_path):
    (tmp_path / "volumes/old").mkdir(parents=True)
    target = tmp_path / "volumes/new"
    target.mkdir()
    (target / "unexpected.txt").write_text("keep")

    result = _run(tmp_path, "old", "new")

    assert result.returncode != 0
    assert (target / "unexpected.txt").read_text() == "keep"


def test_marked_target_is_reused_without_copying_again(tmp_path):
    source = tmp_path / "volumes/old"
    target = tmp_path / "volumes/new"
    source.mkdir(parents=True)
    target.mkdir()
    (source / "value").write_text("old")
    (target / "value").write_text("new")
    (target / ".mirofishplus_migration_complete").touch()

    result = _run(tmp_path, "old", "new")

    assert result.returncode == 0
    assert (target / "value").read_text() == "new"
