import io
import os
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage

from app.config import Config
from app.models.project import ProjectManager
from app.services import uploaded_file_store
from app.services.uploaded_file_store import UploadedFileStore


def _create_referenced_project(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(upload_dir / "projects"))
    store = UploadedFileStore()
    saved = store.save_upload(
        FileStorage(stream=io.BytesIO(b"shared source"), filename="shared.txt"),
        "shared.txt",
    )
    project = ProjectManager.create_project("待删除项目")
    project.files = [{
        "file_id": saved["file_id"],
        "filename": saved["display_name"],
        "size": saved["size"],
    }]
    ProjectManager.save_project(project)
    store.add_project_references(project.project_id, [saved["file_id"]])
    return store, saved, project


def test_project_cleanup_failure_keeps_project_invisible_and_is_retried(
    monkeypatch,
    tmp_path,
):
    """若提交后的目录清理失败又恢复项目，会出现无引用但可见的活项目。"""
    store, saved, project = _create_referenced_project(monkeypatch, tmp_path)
    project_dir = Path(ProjectManager._get_project_dir(project.project_id))
    real_rmtree = shutil.rmtree

    def fail_cleanup(path, *args, **kwargs):
        if ".deleting-" in Path(path).name:
            raise OSError("project tombstone is busy")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", fail_cleanup)

    assert ProjectManager.delete_project(project.project_id) is True
    assert ProjectManager.get_project(project.project_id) is None
    assert ProjectManager.list_projects(limit=None) == []
    assert store.list_references(saved["file_id"]) == []
    assert not project_dir.exists()
    tombstones = list(project_dir.parent.glob(f".{project.project_id}.deleting-*"))
    assert len(tombstones) == 1
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT state FROM project_delete_operations WHERE project_id=?",
            (project.project_id,),
        ).fetchone() == ("cleanup_failed",)

    monkeypatch.setattr(shutil, "rmtree", real_rmtree)
    UploadedFileStore()

    assert not tombstones[0].exists()
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM project_delete_operations WHERE project_id=?",
            (project.project_id,),
        ).fetchone() is None


def test_reference_transaction_failure_restores_project_directory_and_reference(
    monkeypatch,
    tmp_path,
):
    """若解除引用失败却未恢复 tombstone，项目会从界面消失但仍占用文件。"""
    store, saved, project = _create_referenced_project(monkeypatch, tmp_path)
    project_dir = Path(ProjectManager._get_project_dir(project.project_id))
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_project_reference_delete
            BEFORE DELETE ON project_files
            WHEN OLD.project_id = '%s'
            BEGIN
                SELECT RAISE(ABORT, 'reference deletion rejected');
            END
            """ % project.project_id
        )

    replace_calls = []
    real_replace = os.replace

    def track_replace(source, destination):
        replace_calls.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(uploaded_file_store.os, "replace", track_replace)

    with pytest.raises(sqlite3.IntegrityError, match="reference deletion rejected"):
        ProjectManager.delete_project(project.project_id)

    project_moves = [
        call for call in replace_calls if project_dir in call
    ]
    assert len(project_moves) == 2
    assert project_moves[0][0] == project_dir
    assert ".deleting-" in project_moves[0][1].name
    assert project_moves[1] == (project_moves[0][1], project_dir)
    assert ProjectManager.get_project(project.project_id) is not None
    assert store.list_references(saved["file_id"])[0]["project_id"] == project.project_id
    assert not list(project_dir.parent.glob(f".{project.project_id}.deleting-*"))


def test_file_delete_waits_until_project_is_hidden_and_references_are_committed(
    monkeypatch,
    tmp_path,
):
    """若项目删除未持有同一写锁，文件可在仍可见项目的解除引用窗口被删除。"""
    store, saved, project = _create_referenced_project(monkeypatch, tmp_path)
    project_dir = Path(ProjectManager._get_project_dir(project.project_id))
    project_hidden = threading.Event()
    allow_project_delete = threading.Event()
    file_delete_finished = threading.Event()
    errors = []
    real_replace = os.replace

    def pause_after_project_is_hidden(source, destination):
        real_replace(source, destination)
        if Path(source) == project_dir and ".deleting-" in Path(destination).name:
            project_hidden.set()
            allow_project_delete.wait(timeout=2)

    monkeypatch.setattr(
        uploaded_file_store.os,
        "replace",
        pause_after_project_is_hidden,
    )

    def delete_project():
        try:
            ProjectManager.delete_project(project.project_id)
        except Exception as error:
            errors.append(error)

    def delete_file():
        try:
            store.delete_file(saved["file_id"])
        except Exception as error:
            errors.append(error)
        finally:
            file_delete_finished.set()

    project_thread = threading.Thread(target=delete_project)
    project_thread.start()
    assert project_hidden.wait(timeout=1)
    assert ProjectManager.get_project(project.project_id) is None

    file_thread = threading.Thread(target=delete_file)
    file_thread.start()
    assert not file_delete_finished.wait(timeout=0.2)

    allow_project_delete.set()
    project_thread.join(timeout=2)
    file_thread.join(timeout=2)

    assert not project_thread.is_alive()
    assert not file_thread.is_alive()
    assert errors == []
    assert ProjectManager.get_project(project.project_id) is None
    assert store.get_file(saved["file_id"]) is None


def test_late_public_reference_write_is_rejected_after_project_delete(
    monkeypatch,
    tmp_path,
):
    """若公共引用写只校验文件，等待删除事务后会为已消失项目重建孤儿引用。"""
    store, saved, project = _create_referenced_project(monkeypatch, tmp_path)
    project_dir = Path(ProjectManager._get_project_dir(project.project_id))
    project_hidden = threading.Event()
    allow_project_delete = threading.Event()
    reference_write_finished = threading.Event()
    delete_errors = []
    reference_errors = []
    real_replace = os.replace

    def pause_after_project_is_hidden(source, destination):
        real_replace(source, destination)
        if Path(source) == project_dir and ".deleting-" in Path(destination).name:
            project_hidden.set()
            allow_project_delete.wait(timeout=2)

    monkeypatch.setattr(
        uploaded_file_store.os,
        "replace",
        pause_after_project_is_hidden,
    )

    def delete_project():
        try:
            store.delete_project_directory(project.project_id, project_dir)
        except Exception as error:
            delete_errors.append(error)

    def add_reference():
        try:
            store.add_project_references(project.project_id, [saved["file_id"]])
        except Exception as error:
            reference_errors.append(error)
        finally:
            reference_write_finished.set()

    delete_thread = threading.Thread(target=delete_project)
    delete_thread.start()
    assert project_hidden.wait(timeout=1)

    reference_thread = threading.Thread(target=add_reference)
    reference_thread.start()
    assert not reference_write_finished.wait(timeout=0.2)

    allow_project_delete.set()
    delete_thread.join(timeout=2)
    reference_thread.join(timeout=2)

    assert not delete_thread.is_alive()
    assert not reference_thread.is_alive()
    assert delete_errors == []
    assert len(reference_errors) == 1
    assert isinstance(reference_errors[0], sqlite3.IntegrityError)
    assert "项目不存在或正在删除" in str(reference_errors[0])
    assert store.list_references(saved["file_id"]) == []


def test_late_existing_file_migration_cannot_relink_deleted_project(
    monkeypatch,
    tmp_path,
):
    """若迁移捕获旧项目后晚到提交，已有 file_id 分支不得重建孤儿引用。"""
    store, saved, project = _create_referenced_project(monkeypatch, tmp_path)
    project_dir = Path(ProjectManager._get_project_dir(project.project_id))
    projects_captured = threading.Event()
    allow_migration = threading.Event()
    migration_results = []
    migration_errors = []
    original_list_projects = ProjectManager.list_projects

    def capture_projects(_cls, limit=50):
        projects = original_list_projects(limit=limit)
        projects_captured.set()
        allow_migration.wait(timeout=2)
        return projects

    monkeypatch.setattr(
        ProjectManager,
        "list_projects",
        classmethod(capture_projects),
    )

    def migrate():
        try:
            migration_results.append(store.migrate_legacy_projects())
        except Exception as error:
            migration_errors.append(error)

    migration_thread = threading.Thread(target=migrate)
    migration_thread.start()
    assert projects_captured.wait(timeout=1)

    assert store.delete_project_directory(project.project_id, project_dir) is True
    allow_migration.set()
    migration_thread.join(timeout=2)

    assert not migration_thread.is_alive()
    assert migration_errors == []
    assert migration_results == [{"migrated": 0, "linked": 0, "skipped": 1}]
    assert ProjectManager.get_project(project.project_id) is None
    assert store.list_references(saved["file_id"]) == []


def test_late_new_file_migration_rolls_back_file_and_reference_after_project_delete(
    monkeypatch,
    tmp_path,
):
    """若迁移复制完成后项目才删除，新文件分支不得留下记录或孤儿引用。"""
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(upload_dir / "projects"))
    store = UploadedFileStore()
    project = ProjectManager.create_project("迁移中删除")
    project_dir = Path(ProjectManager._get_project_dir(project.project_id))
    source_path = Path(ProjectManager._get_project_files_dir(project.project_id)) / "legacy.txt"
    source_path.write_bytes(b"legacy")
    project.files = [{
        "filename": "历史资料.txt",
        "saved_filename": "legacy.txt",
        "size": len(b"legacy"),
    }]
    ProjectManager.save_project(project)

    library_copy_finished = threading.Event()
    allow_migration = threading.Event()
    migration_results = []
    migration_errors = []
    real_replace = os.replace

    def pause_after_library_copy(source, destination):
        real_replace(source, destination)
        if (
            Path(source).name.endswith(".migrating")
            and Path(destination).parent == store.library_dir
        ):
            library_copy_finished.set()
            allow_migration.wait(timeout=2)

    monkeypatch.setattr(
        uploaded_file_store.os,
        "replace",
        pause_after_library_copy,
    )

    def migrate():
        try:
            migration_results.append(store.migrate_legacy_projects())
        except Exception as error:
            migration_errors.append(error)

    migration_thread = threading.Thread(target=migrate)
    migration_thread.start()
    assert library_copy_finished.wait(timeout=1)

    assert store.delete_project_directory(project.project_id, project_dir) is True
    allow_migration.set()
    migration_thread.join(timeout=2)

    assert not migration_thread.is_alive()
    assert migration_errors == []
    assert migration_results == [{"migrated": 0, "linked": 0, "skipped": 1}]
    assert store.list_files() == []
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM project_files WHERE project_id=?",
            (project.project_id,),
        ).fetchone() is None


def test_zero_reference_restore_failed_operation_restores_project(monkeypatch, tmp_path):
    """restore_failed 表示引用事务已回滚，零引用项目也必须恢复而不能清理。"""
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(upload_dir / "projects"))
    store = UploadedFileStore()
    project = ProjectManager.create_project("零引用恢复")
    project_dir = Path(ProjectManager._get_project_dir(project.project_id))
    tombstone_path = project_dir.parent / f".{project.project_id}.deleting-restore_failed"
    os.replace(project_dir, tombstone_path)
    now = "2026-09-04T00:00:00+00:00"
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            INSERT INTO project_delete_operations (
                operation_id, project_id, original_path, tombstone_path,
                state, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'restore_failed', ?, ?, ?)
            """,
            (
                "project_delete_restore_failed",
                project.project_id,
                str(project_dir),
                str(tombstone_path),
                "restore was interrupted",
                now,
                now,
            ),
        )

    UploadedFileStore()

    assert ProjectManager.get_project(project.project_id) is not None
    assert not tombstone_path.exists()
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM project_delete_operations WHERE project_id=?",
            (project.project_id,),
        ).fetchone() is None


def test_zero_reference_untracked_tombstone_defaults_to_restore(monkeypatch, tmp_path):
    """无 operation 代表删除事务未提交，零引用 tombstone 默认也必须恢复。"""
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(upload_dir / "projects"))
    store = UploadedFileStore()
    project = ProjectManager.create_project("零引用未提交")
    project_dir = Path(ProjectManager._get_project_dir(project.project_id))
    tombstone_path = project_dir.parent / f".{project.project_id}.deleting-interrupted"
    os.replace(project_dir, tombstone_path)

    UploadedFileStore()

    assert ProjectManager.get_project(project.project_id) is not None
    assert not tombstone_path.exists()
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM project_delete_operations WHERE project_id=?",
            (project.project_id,),
        ).fetchone() is None
