import hashlib
import io
import os
import sqlite3
import threading
from pathlib import Path

import app.services.uploaded_file_store as uploaded_file_store
import pytest
from werkzeug.datastructures import FileStorage

from app.config import Config
from app.models.project import ProjectManager
from app.services.uploaded_file_store import (
    FileInUseError,
    FileStorageError,
    UploadedFileStore,
)


def _upload(filename: str, content: bytes) -> FileStorage:
    return FileStorage(stream=io.BytesIO(content), filename=filename)


def _create_project(monkeypatch, tmp_path, name):
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path / "projects"))
    return ProjectManager.create_project(name)


def test_save_upload_persists_metadata_content_and_sha256(tmp_path):
    """若上传实现未写入元数据或内容，读取记录和物理文件都会失败。"""
    library_dir = tmp_path / "library"
    store = UploadedFileStore(tmp_path / "files.db", library_dir)

    saved = store.save_upload(_upload("original.txt", b"hello uploaded file"), "research.txt")

    assert saved["file_id"].startswith("file_")
    assert saved["display_name"] == "research.txt"
    assert saved["extension"] == "txt"
    assert saved["sha256"] == hashlib.sha256(b"hello uploaded file").hexdigest()
    assert saved["size"] == len(b"hello uploaded file")
    assert saved["legacy_source"] is None
    assert (library_dir / saved["stored_filename"]).read_bytes() == b"hello uploaded file"
    assert store.get_file(saved["file_id"]) == saved


def test_save_uploads_removes_all_files_when_later_stream_read_fails(tmp_path):
    """若批量上传第二个流读取失败，首个文件也不得留下记录或物理内容。"""
    library_dir = tmp_path / "library"
    store = UploadedFileStore(tmp_path / "files.db", library_dir)

    class FailingStream:
        def read(self, _size=-1):
            raise OSError("second upload read failed")

    failing_upload = FileStorage(stream=FailingStream(), filename="second.txt")
    with pytest.raises(OSError, match="second upload read failed"):
        store.save_uploads([
            (_upload("first.txt", b"first"), "first.txt"),
            (failing_upload, "second.txt"),
        ])

    assert store.list_files() == []
    assert list(library_dir.iterdir()) == []


def test_save_uploads_rolls_back_records_and_files_when_later_insert_fails(tmp_path):
    """若批量事务中的第二条 INSERT 失败，数据库和文件库都必须回滚为批次前状态。"""
    library_dir = tmp_path / "library"
    database_path = tmp_path / "files.db"
    store = UploadedFileStore(database_path, library_dir)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_second_upload
            BEFORE INSERT ON uploaded_files
            WHEN NEW.display_name='second.txt'
            BEGIN
                SELECT RAISE(ABORT, 'second upload rejected');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="second upload rejected"):
        store.save_uploads([
            (_upload("first.txt", b"first"), "first.txt"),
            (_upload("second.txt", b"second"), "second.txt"),
        ])

    assert store.list_files() == []
    assert list(library_dir.iterdir()) == []


def test_default_library_directory_uses_upload_folder_library(monkeypatch, tmp_path):
    """若默认目录偏离设计位置，按统一目录管理的文件无法被定位。"""
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))

    store = UploadedFileStore()

    assert store.library_dir == tmp_path / "library"


@pytest.mark.parametrize("display_name", ["", "../outside.txt", "nested\\inside.txt", "binary.exe"])
def test_save_upload_rejects_invalid_display_names(tmp_path, display_name):
    """若白名单或纯文件名校验失效，上传可写入不受支持或路径型名称。"""
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")

    with pytest.raises(ValueError):
        store.save_upload(_upload("source.txt", b"contents"), display_name)


def test_list_files_searches_display_names(tmp_path):
    """若搜索不按展示名称过滤，会返回无关文件。"""
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")
    matching = store.save_upload(_upload("source.txt", b"a"), "market-analysis.txt")
    store.save_upload(_upload("source.txt", b"b"), "meeting-notes.txt")

    assert [item["file_id"] for item in store.list_files(query="analysis")] == [
        matching["file_id"]
    ]


def test_rename_file_updates_display_name(tmp_path):
    """若重命名未持久化，后续读取仍会显示旧名称。"""
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")
    saved = store.save_upload(_upload("source.txt", b"contents"), "draft.txt")

    renamed = store.rename_file(saved["file_id"], "final.txt")

    assert renamed["display_name"] == "final.txt"
    assert store.get_file(saved["file_id"])["display_name"] == "final.txt"


def test_rename_file_updates_normalized_extension_with_display_name(tmp_path):
    """若跨允许扩展名重命名不更新规范化字段，文件类型元数据会自相矛盾。"""
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")
    saved = store.save_upload(_upload("source.txt", b"contents"), "draft.txt")

    renamed = store.rename_file(saved["file_id"], "final.pdf")

    assert renamed["display_name"] == "final.pdf"
    assert renamed["extension"] == "pdf"
    assert store.get_file(saved["file_id"])["extension"] == "pdf"


def test_add_project_references_is_idempotent_and_lists_references(monkeypatch, tmp_path):
    """若重复添加引用会写入重复关系，引用列表会出现重复项目。"""
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")
    saved = store.save_upload(_upload("source.txt", b"contents"), "source.txt")
    project = _create_project(monkeypatch, tmp_path, "项目一")

    store.add_project_references(project.project_id, [saved["file_id"], saved["file_id"]])
    store.add_project_references(project.project_id, [saved["file_id"]])

    assert store.list_references(saved["file_id"]) == [{
        "project_id": project.project_id,
        "project_name": "项目一",
        "position": 0,
    }]


def test_add_project_references_preserves_input_order_and_rolls_back_unknown_ids(
    monkeypatch,
    tmp_path,
):
    """若引用去重丢失顺序或未知 ID 部分写入，项目语料顺序和关系完整性都会损坏。"""
    database_path = tmp_path / "files.db"
    store = UploadedFileStore(database_path, tmp_path / "library")
    first = store.save_upload(_upload("first.txt", b"first"), "first.txt")
    second = store.save_upload(_upload("second.txt", b"second"), "second.txt")
    ordered_project = _create_project(monkeypatch, tmp_path, "有序项目")
    atomic_project = ProjectManager.create_project("原子项目")

    store.add_project_references(
        ordered_project.project_id,
        [second["file_id"], first["file_id"], second["file_id"]],
    )
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT file_id, position FROM project_files WHERE project_id=? ORDER BY position",
            (ordered_project.project_id,),
        ).fetchall()
    assert rows == [(second["file_id"], 0), (first["file_id"], 1)]

    with pytest.raises(sqlite3.IntegrityError):
        store.add_project_references(
            atomic_project.project_id,
            [first["file_id"], "file_missing"],
        )
    assert store.list_references(first["file_id"]) == [{
        "project_id": ordered_project.project_id,
        "project_name": "有序项目",
        "position": 1,
    }]


def test_delete_file_rejects_referenced_file_until_references_are_removed(
    monkeypatch,
    tmp_path,
):
    """若删除未检查引用，项目会保留指向已删除文件的关系。"""
    library_dir = tmp_path / "library"
    store = UploadedFileStore(tmp_path / "files.db", library_dir)
    saved = store.save_upload(_upload("source.txt", b"contents"), "source.txt")
    stored_path = library_dir / saved["stored_filename"]
    project = _create_project(monkeypatch, tmp_path, "引用项目")
    store.add_project_references(project.project_id, [saved["file_id"]])

    with pytest.raises(FileInUseError):
        store.delete_file(saved["file_id"])
    assert store.get_file(saved["file_id"]) is not None
    assert stored_path.exists()

    store.remove_project_references(project.project_id)
    store.delete_file(saved["file_id"])

    assert store.get_file(saved["file_id"]) is None
    assert not stored_path.exists()


def test_initialization_rolls_back_all_ddl_when_an_index_cannot_be_created(tmp_path):
    """若 DDL 未显式事务化，初始化失败会留下只创建了一部分的文件库表。"""
    database_path = tmp_path / "files.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE project_files (unrelated_value TEXT)")

    with pytest.raises(sqlite3.OperationalError):
        UploadedFileStore(database_path, tmp_path / "library")

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "uploaded_files" not in tables
    assert "project_files" in tables


def test_delete_restores_original_file_when_database_delete_fails(monkeypatch, tmp_path):
    """若数据库删除失败，tombstone 协议必须恢复原文件名并保留记录。"""
    library_dir = tmp_path / "library"
    database_path = tmp_path / "files.db"
    store = UploadedFileStore(database_path, library_dir)
    saved = store.save_upload(_upload("source.txt", b"contents"), "source.txt")
    stored_path = library_dir / saved["stored_filename"]
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_uploaded_file_delete
            BEFORE DELETE ON uploaded_files
            BEGIN
                SELECT RAISE(ABORT, 'database deletion rejected');
            END
            """
        )

    rename_calls = []
    real_replace = os.replace

    def track_replace(source, destination):
        rename_calls.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(uploaded_file_store.os, "replace", track_replace)

    with pytest.raises(sqlite3.IntegrityError, match="database deletion rejected"):
        store.delete_file(saved["file_id"])

    assert len(rename_calls) == 2
    assert rename_calls[0][0] == stored_path
    assert rename_calls[1][1] == stored_path
    assert stored_path.exists()
    assert store.get_file(saved["file_id"]) is not None
    assert not list(library_dir.glob(f".{saved['stored_filename']}.deleting-*"))


def test_delete_cleanup_failure_restores_reusable_record_and_cancels_operation(monkeypatch, tmp_path):
    """若原路径已恢复但删除意图仍保留，列表中的文件会变成无法再次引用的死选项。"""
    library_dir = tmp_path / "library"
    database_path = tmp_path / "files.db"
    store = UploadedFileStore(database_path, library_dir)
    saved = store.save_upload(_upload("source.txt", b"contents"), "source.txt")
    real_unlink = Path.unlink

    def fail_tombstone_unlink(path, *args, **kwargs):
        if ".deleting-" in path.name:
            raise OSError("tombstone is busy")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_tombstone_unlink)

    with pytest.raises(FileStorageError, match="物理文件删除失败"):
        store.delete_file(saved["file_id"])

    assert store.get_file(saved["file_id"]) == saved
    assert [item["file_id"] for item in store.list_files()] == [saved["file_id"]]
    assert (library_dir / saved["stored_filename"]).read_bytes() == b"contents"
    assert not list(library_dir.glob(f".{saved['stored_filename']}.deleting-*"))
    with sqlite3.connect(database_path) as connection:
        operation = connection.execute(
            "SELECT state FROM uploaded_file_delete_operations WHERE file_id=?",
            (saved["file_id"],),
        ).fetchone()
    assert operation is None
    project = _create_project(monkeypatch, tmp_path, "恢复项目")
    store.add_project_references(project.project_id, [saved["file_id"]])
    assert store.list_references(saved["file_id"])[0]["project_id"] == project.project_id


def test_initialization_keeps_a_restored_delete_failure_reusable(monkeypatch, tmp_path):
    """若已恢复的失败删除在初始化时重试，用户刚恢复可用的文件会被悄悄删除。"""
    library_dir = tmp_path / "library"
    database_path = tmp_path / "files.db"
    store = UploadedFileStore(database_path, library_dir)
    saved = store.save_upload(_upload("source.txt", b"contents"), "source.txt")
    real_unlink = Path.unlink

    def fail_tombstone_unlink(path, *args, **kwargs):
        if ".deleting-" in path.name:
            raise OSError("tombstone is busy")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_tombstone_unlink)
    with pytest.raises(FileStorageError):
        store.delete_file(saved["file_id"])
    monkeypatch.undo()

    recovered = UploadedFileStore(database_path, library_dir)

    assert recovered.get_file(saved["file_id"]) == saved
    assert (library_dir / saved["stored_filename"]).read_bytes() == b"contents"
    assert not list(library_dir.glob(f".{saved['stored_filename']}.deleting-*"))
    with sqlite3.connect(database_path) as connection:
        operation = connection.execute(
            "SELECT 1 FROM uploaded_file_delete_operations WHERE file_id=?",
            (saved["file_id"],),
        ).fetchone()
    assert operation is None


def test_restored_delete_failure_can_gain_new_project_references(monkeypatch, tmp_path):
    """若原路径恢复后仍禁止引用，列表会宣传一个实际不可复用的文件。"""
    library_dir = tmp_path / "library"
    database_path = tmp_path / "files.db"
    store = UploadedFileStore(database_path, library_dir)
    saved = store.save_upload(_upload("source.txt", b"contents"), "source.txt")
    real_unlink = Path.unlink

    def fail_tombstone_unlink(path, *args, **kwargs):
        if ".deleting-" in path.name:
            raise OSError("tombstone is busy")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_tombstone_unlink)
    with pytest.raises(FileStorageError):
        store.delete_file(saved["file_id"])

    project = _create_project(monkeypatch, tmp_path, "晚到项目")
    store.add_project_references(project.project_id, [saved["file_id"]])

    assert store.list_references(saved["file_id"])[0]["project_id"] == project.project_id


def test_unrestored_delete_failure_is_excluded_from_reusable_file_list(monkeypatch, tmp_path):
    """若无法恢复原路径的记录仍出现在列表，选择器会提供一个必然失败的文件。"""
    library_dir = tmp_path / "library"
    database_path = tmp_path / "files.db"
    store = UploadedFileStore(database_path, library_dir)
    saved = store.save_upload(_upload("source.txt", b"contents"), "source.txt")
    stored_path = library_dir / saved["stored_filename"]
    real_unlink = Path.unlink
    real_replace = os.replace

    def fail_tombstone_unlink(path, *args, **kwargs):
        if ".deleting-" in path.name:
            raise OSError("tombstone is busy")
        return real_unlink(path, *args, **kwargs)

    def fail_restore(source, destination):
        if ".deleting-" in Path(source).name and Path(destination) == stored_path:
            raise OSError("restore path is busy")
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "unlink", fail_tombstone_unlink)
    monkeypatch.setattr(uploaded_file_store.os, "replace", fail_restore)

    with pytest.raises(FileStorageError, match="无法恢复"):
        store.delete_file(saved["file_id"])

    assert store.get_file(saved["file_id"]) == saved
    assert store.list_files() == []
    project = _create_project(monkeypatch, tmp_path, "不可引用项目")
    with pytest.raises(sqlite3.IntegrityError, match="不存在或正在删除"):
        store.add_project_references(project.project_id, [saved["file_id"]])
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT state FROM uploaded_file_delete_operations WHERE file_id=?",
            (saved["file_id"],),
        ).fetchone() == ("failed",)


def test_initialization_idempotently_cleans_tombstone_left_after_committed_delete(tmp_path):
    """若进程在提交删除后退出，后续初始化必须清理遗留 tombstone。"""
    library_dir = tmp_path / "library"
    database_path = tmp_path / "files.db"
    store = UploadedFileStore(database_path, library_dir)
    saved = store.save_upload(_upload("source.txt", b"contents"), "source.txt")
    stored_path = library_dir / saved["stored_filename"]
    tombstone_path = library_dir / f".{saved['stored_filename']}.deleting-interrupted"
    os.replace(stored_path, tombstone_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM uploaded_files WHERE file_id=?", (saved["file_id"],))

    reloaded = UploadedFileStore(database_path, library_dir)
    UploadedFileStore(database_path, library_dir)

    assert reloaded.get_file(saved["file_id"]) is None
    assert not tombstone_path.exists()


def test_recovery_scan_waits_for_active_delete_transaction(monkeypatch, tmp_path):
    """另一个 store 的恢复不得介入已持有 SQLite 写锁的物理删除窗口。"""
    library_dir = tmp_path / "library"
    database_path = tmp_path / "files.db"
    deleting_store = UploadedFileStore(database_path, library_dir)
    recovering_store = UploadedFileStore(database_path, library_dir)
    saved = deleting_store.save_upload(_upload("source.txt", b"contents"), "source.txt")
    stored_path = library_dir / saved["stored_filename"]
    real_replace = os.replace
    tombstone_created = threading.Event()
    allow_delete_to_continue = threading.Event()
    recovery_finished = threading.Event()
    delete_errors = []

    def pause_after_tombstone_rename(source, destination):
        real_replace(source, destination)
        if source == stored_path and ".deleting-" in Path(destination).name:
            tombstone_created.set()
            allow_delete_to_continue.wait(timeout=2)

    monkeypatch.setattr(uploaded_file_store.os, "replace", pause_after_tombstone_rename)

    def delete_file():
        try:
            deleting_store.delete_file(saved["file_id"])
        except Exception as error:
            delete_errors.append(error)

    def recover_tombstones():
        recovering_store._recover_delete_operations()
        recovery_finished.set()

    delete_thread = threading.Thread(target=delete_file)
    delete_thread.start()
    assert tombstone_created.wait(timeout=1)
    recovery_thread = threading.Thread(target=recover_tombstones)
    recovery_thread.start()
    recovery_intervened_before_commit = recovery_finished.wait(timeout=0.2)
    allow_delete_to_continue.set()
    delete_thread.join(timeout=2)
    recovery_thread.join(timeout=2)

    assert not recovery_intervened_before_commit
    assert not delete_thread.is_alive()
    assert not recovery_thread.is_alive()
    assert delete_errors == []
    assert deleting_store.get_file(saved["file_id"]) is None
    assert not stored_path.exists()
    assert not list(library_dir.glob(f".{saved['stored_filename']}.deleting-*"))
    with sqlite3.connect(database_path) as connection:
        assert connection.execute(
            "SELECT 1 FROM uploaded_file_delete_operations WHERE file_id=?",
            (saved["file_id"],),
        ).fetchone() is None
