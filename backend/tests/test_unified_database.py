import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from werkzeug.datastructures import FileStorage

from app.models import database as database_module
from app.models.database import (
    initialize_unified_database,
    migrate_legacy_unified_database,
    unified_database_path,
)
from app.models.model_config import APIProtocol, AuthType, ModelCapability, ProviderVendor
from app.models.project import ProjectManager
from app.models.task_store import TaskStore
from app.services.credential_cipher import CredentialCipher
from app.services.model_config_store import ModelConfigStore
from app.services.uploaded_file_store import UploadedFileStore


def test_legacy_model_and_task_databases_migrate_idempotently(tmp_path):
    legacy_models = tmp_path / "model-config" / "models.db"
    legacy_tasks = tmp_path / "tasks" / "tasks.db"
    destination = tmp_path / "mirofish.db"
    source_store = ModelConfigStore(legacy_models, CredentialCipher(tmp_path / "master.key"))
    source_store.create_connection("Text", ProviderVendor.CUSTOM, APIProtocol.OPENAI_RESPONSES, AuthType.NONE, ModelCapability.TEXT_GENERATION, "https://example.com/v1", "")
    TaskStore(legacy_tasks).save([{
        "task_id": "task-1", "task_type": "prepare", "status": "completed",
        "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
        "progress": 100, "message": "done", "result": {}, "error": None,
        "metadata": {}, "progress_detail": {},
    }])
    ModelConfigStore(destination, CredentialCipher(tmp_path / "master.key"))
    TaskStore(destination)

    initialize_unified_database(destination, legacy_models, legacy_tasks)
    initialize_unified_database(destination, legacy_models, legacy_tasks)

    assert len(ModelConfigStore(destination, CredentialCipher(tmp_path / "master.key")).list_connections()) == 1
    assert [item["task_id"] for item in TaskStore(destination).load()] == ["task-1"]
    assert legacy_models.exists()
    assert legacy_tasks.exists()


def test_legacy_file_library_tables_and_delete_operations_are_copied(monkeypatch, tmp_path):
    legacy_files = tmp_path / "legacy-files.db"
    destination = tmp_path / "mirofishplus.db"
    library_dir = tmp_path / "library"
    target_store = UploadedFileStore(destination, library_dir)
    source_store = UploadedFileStore(legacy_files, library_dir)
    referenced = source_store.save_upload(
        FileStorage(stream=io.BytesIO(b"legacy contents"), filename="legacy.txt"),
        "legacy.txt",
    )
    pending = source_store.save_upload(
        FileStorage(stream=io.BytesIO(b"pending contents"), filename="pending.txt"),
        "pending.txt",
    )
    failed = source_store.save_upload(
        FileStorage(stream=io.BytesIO(b"failed contents"), filename="failed.txt"),
        "failed.txt",
    )
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path / "projects"))
    late_project = ProjectManager.create_project("晚到项目")
    pending_tombstone = f".{pending['stored_filename']}.deleting-delete_pending"
    failed_tombstone = f".{failed['stored_filename']}.deleting-delete_failed"
    os.replace(
        library_dir / pending["stored_filename"],
        library_dir / pending_tombstone,
    )
    with sqlite3.connect(legacy_files) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO project_files (project_id, file_id, position)
            VALUES ('legacy-project', ?, 0)
            """,
            (referenced["file_id"],),
        )
        connection.executemany(
            """
            INSERT INTO uploaded_file_delete_operations (
                operation_id, file_id, stored_filename, tombstone_filename,
                state, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "delete_pending",
                    pending["file_id"],
                    pending["stored_filename"],
                    pending_tombstone,
                    "pending",
                    None,
                    "2026-09-04T00:00:00+00:00",
                    "2026-09-04T00:00:00+00:00",
                ),
                (
                    "delete_failed",
                    failed["file_id"],
                    failed["stored_filename"],
                    failed_tombstone,
                    "failed",
                    "tombstone is busy",
                    "2026-09-04T00:00:00+00:00",
                    "2026-09-04T00:00:01+00:00",
                ),
            ],
        )

    initialize_unified_database(destination, legacy_files=legacy_files)

    assert target_store.get_file(referenced["file_id"])["display_name"] == "legacy.txt"
    assert target_store.list_references(referenced["file_id"]) == [
        {"project_id": "legacy-project", "project_name": None, "position": 0}
    ]
    with sqlite3.connect(destination) as connection:
        operations = connection.execute(
            """
            SELECT operation_id, state
            FROM uploaded_file_delete_operations
            ORDER BY operation_id
            """
        ).fetchall()
    assert operations == [("delete_failed", "failed"), ("delete_pending", "pending")]
    for deleting_file in (pending, failed):
        with pytest.raises(sqlite3.IntegrityError, match="不存在或正在删除"):
            target_store.add_project_references(
                late_project.project_id,
                [deleting_file["file_id"]],
            )

    recovered_store = UploadedFileStore(destination, library_dir)

    assert recovered_store.get_file(pending["file_id"]) is None
    assert recovered_store.get_file(failed["file_id"]) is None
    assert not (library_dir / pending["stored_filename"]).exists()
    assert not (library_dir / pending_tombstone).exists()
    assert not (library_dir / failed["stored_filename"]).exists()
    assert not (library_dir / failed_tombstone).exists()
    with sqlite3.connect(destination) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM uploaded_file_delete_operations"
        ).fetchone()[0] == 0


def test_default_services_reference_unified_database_path():
    root = Path(__file__).resolve().parents[1] / "app"
    service = (root / "services/model_config_service.py").read_text()
    router = (root / "services/model_router.py").read_text()
    memory = (root / "services/memory_backend_config_service.py").read_text()
    task = (root / "models/task.py").read_text()

    for source in (service, router, memory, task):
        assert "mirofishplus.db" in source or "unified_database_path" in source


def test_default_database_uses_mirofishplus_brand(monkeypatch, tmp_path):
    monkeypatch.setattr(database_module.Config, "UPLOAD_FOLDER", str(tmp_path))

    assert unified_database_path() == tmp_path / "mirofishplus.db"


def test_legacy_unified_database_is_copied_once_and_retained(tmp_path):
    source = tmp_path / "mirofish.db"
    destination = tmp_path / "mirofishplus.db"
    with __import__("sqlite3").connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO records(value) VALUES ('legacy')")

    assert migrate_legacy_unified_database(destination, source) is True
    assert source.exists()
    with __import__("sqlite3").connect(destination) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM records").fetchone()[0] == "legacy"
        assert connection.execute(
            "SELECT 1 FROM app_schema_migrations WHERE migration_key='legacy_mirofish_database_v1'"
        ).fetchone()
        connection.execute("UPDATE records SET value='new-database-wins'")

    assert migrate_legacy_unified_database(destination, source) is False
    with __import__("sqlite3").connect(destination) as connection:
        assert connection.execute("SELECT value FROM records").fetchone()[0] == "new-database-wins"


def test_importing_database_module_does_not_create_the_new_database(tmp_path):
    code = """
import sys
from app.config import Config
Config.UPLOAD_FOLDER = sys.argv[1]
import app.models.database
assert 'app.models.task' not in sys.modules
"""

    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "mirofishplus.db").exists()
