import io
import json
import sqlite3
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage

from app.models import project as project_module
from app.config import Config
from app.models.project import ProjectManager
from app.services.uploaded_file_store import FileStorageError, UploadedFileStore


def _create_legacy_project(monkeypatch, tmp_path, files, physical_files):
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path / "projects"))
    project = ProjectManager.create_project("旧项目")
    files_dir = Path(ProjectManager._get_project_files_dir(project.project_id))
    for saved_filename, content in physical_files.items():
        (files_dir / saved_filename).write_bytes(content)
    project.files = files
    ProjectManager.save_project(project)
    return project


def test_migration_copies_legacy_file_links_it_and_is_idempotent(monkeypatch, tmp_path):
    """缺少迁移时，旧项目文件无法进入共享库，也无法安全地重复执行迁移。"""
    project = _create_legacy_project(
        monkeypatch,
        tmp_path,
        [{"filename": "展示名称.txt", "size": 14}],
        {"legacy-saved.txt": b"legacy content"},
    )
    source_path = Path(ProjectManager._get_project_files_dir(project.project_id)) / "legacy-saved.txt"
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")

    first = store.migrate_legacy_projects()

    assert first == {"migrated": 1, "linked": 1, "skipped": 0}
    migrated = store.list_files()
    assert len(migrated) == 1
    assert migrated[0]["display_name"] == "展示名称.txt"
    assert migrated[0]["legacy_source"] == f"{project.project_id}:legacy-saved.txt"
    assert (store.library_dir / migrated[0]["stored_filename"]).read_bytes() == b"legacy content"
    assert source_path.read_bytes() == b"legacy content"
    assert store.list_references(migrated[0]["file_id"]) == [
        {"project_id": project.project_id, "project_name": "旧项目", "position": 0}
    ]
    assert ProjectManager.get_project(project.project_id).files[0]["file_id"] == migrated[0]["file_id"]

    second = store.migrate_legacy_projects()

    assert second == {"migrated": 0, "linked": 0, "skipped": 0}
    assert len(store.list_files()) == 1
    assert store.list_references(migrated[0]["file_id"]) == [
        {"project_id": project.project_id, "project_name": "旧项目", "position": 0}
    ]


def test_migration_skips_missing_file_and_continues_with_other_legacy_files(monkeypatch, tmp_path):
    """单个旧物理文件缺失时，不得阻止同一项目其他文件迁移或改写缺失快照。"""
    project = _create_legacy_project(
        monkeypatch,
        tmp_path,
        [
            {"filename": "可迁移.txt", "size": 2, "saved_filename": "present.txt"},
            {"filename": "缺失.txt", "size": 0, "saved_filename": "missing.txt"},
        ],
        {"present.txt": b"ok"},
    )
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")

    result = store.migrate_legacy_projects()

    assert result == {"migrated": 1, "linked": 1, "skipped": 1}
    refreshed = ProjectManager.get_project(project.project_id)
    assert "file_id" in refreshed.files[0]
    assert "file_id" not in refreshed.files[1]
    assert len(store.list_files()) == 1


def test_migration_only_adds_reference_for_snapshot_with_existing_file_id(monkeypatch, tmp_path):
    """已经指向文件库记录的旧快照只需补项目引用，不能复制或新增文件记录。"""
    project = _create_legacy_project(
        monkeypatch,
        tmp_path,
        [{"filename": "已有文件.txt", "size": 8}],
        {},
    )
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")
    existing = store.save_upload(
        FileStorage(stream=io.BytesIO(b"existing"), filename="已有文件.txt"),
        "已有文件.txt",
    )
    project.files[0]["file_id"] = existing["file_id"]
    ProjectManager.save_project(project)

    result = store.migrate_legacy_projects()

    assert result == {"migrated": 0, "linked": 1, "skipped": 0}
    assert [item["file_id"] for item in store.list_files()] == [existing["file_id"]]
    assert store.list_references(existing["file_id"]) == [
        {"project_id": project.project_id, "project_name": "旧项目", "position": 0}
    ]


def test_migration_skips_ambiguous_files_without_saved_filename(monkeypatch, tmp_path, caplog):
    """无法以旧目录顺序唯一对应的快照必须保留，以免错误绑定另一份物理内容。"""
    project = _create_legacy_project(
        monkeypatch,
        tmp_path,
        [{"filename": "展示名称.txt", "size": 1}],
        {"first.txt": b"1", "second.txt": b"2"},
    )
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")

    result = store.migrate_legacy_projects()

    assert result == {"migrated": 0, "linked": 0, "skipped": 1}
    assert "无法唯一匹配" in caplog.text
    assert "file_id" not in ProjectManager.get_project(project.project_id).files[0]
    assert store.list_files() == []


def test_partial_migration_never_reuses_an_already_migrated_source_for_another_snapshot(
    monkeypatch, tmp_path
):
    """若已迁移条目的旧源未被消费，重试会将其内容错误绑定给另一个旧快照。"""
    project = _create_legacy_project(
        monkeypatch,
        tmp_path,
        [{"filename": "甲.txt", "size": 1}, {"filename": "乙.txt", "size": 1}],
        {"a-saved.txt": b"a"},
    )
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")
    migrated_a = store.save_upload(
        FileStorage(stream=io.BytesIO(b"a"), filename="甲.txt"),
        "甲.txt",
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE uploaded_files SET legacy_source=? WHERE file_id=?",
            (f"{project.project_id}:a-saved.txt", migrated_a["file_id"]),
        )
    project.files[0]["file_id"] = migrated_a["file_id"]
    ProjectManager.save_project(project)

    missing_result = store.migrate_legacy_projects()

    assert missing_result == {"migrated": 0, "linked": 1, "skipped": 1}
    assert "file_id" not in ProjectManager.get_project(project.project_id).files[1]
    assert len(store.list_files()) == 1

    files_dir = Path(ProjectManager._get_project_files_dir(project.project_id))
    (files_dir / "b-saved.txt").write_bytes(b"b")

    recovered_result = store.migrate_legacy_projects()

    assert recovered_result == {"migrated": 1, "linked": 1, "skipped": 0}
    assert ProjectManager.get_project(project.project_id).files[1]["file_id"] != migrated_a["file_id"]


def test_mixed_snapshots_reject_fallback_when_an_existing_file_has_no_safe_source_mapping(
    monkeypatch, tmp_path
):
    """若已有 file_id 无法对应旧源，不能靠数量把其遗留内容猜测分配给其他快照。"""
    project = _create_legacy_project(
        monkeypatch,
        tmp_path,
        [{"filename": "甲.txt", "size": 1}, {"filename": "乙.txt", "size": 1}],
        {"unknown-source.txt": b"a"},
    )
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")
    existing = store.save_upload(
        FileStorage(stream=io.BytesIO(b"a"), filename="甲.txt"),
        "甲.txt",
    )
    project.files[0]["file_id"] = existing["file_id"]
    ProjectManager.save_project(project)

    result = store.migrate_legacy_projects()

    assert result == {"migrated": 0, "linked": 1, "skipped": 1}
    assert "file_id" not in ProjectManager.get_project(project.project_id).files[1]
    assert len(store.list_files()) == 1


def test_migration_reports_snapshot_write_failure_and_later_retry_converges(monkeypatch, tmp_path):
    """若 file_id 回写失败，返回计数必须要求重试，而不是伪装成完整迁移。"""
    project = _create_legacy_project(
        monkeypatch,
        tmp_path,
        [{"filename": "待回写.txt", "size": 1}],
        {"pending.txt": b"p"},
    )
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")
    original_save_project = ProjectManager.save_project

    def fail_snapshot_write(project_to_save):
        if project_to_save.project_id == project.project_id:
            raise OSError("project.json is read-only")
        original_save_project(project_to_save)

    monkeypatch.setattr(ProjectManager, "save_project", fail_snapshot_write)

    failed_result = store.migrate_legacy_projects()

    assert failed_result == {"migrated": 0, "linked": 0, "skipped": 1}
    assert "file_id" not in ProjectManager.get_project(project.project_id).files[0]
    assert len(store.list_files()) == 1

    monkeypatch.setattr(ProjectManager, "save_project", original_save_project)
    recovered_result = store.migrate_legacy_projects()

    assert recovered_result == {"migrated": 0, "linked": 0, "skipped": 0}
    assert "file_id" in ProjectManager.get_project(project.project_id).files[0]


@pytest.mark.parametrize("state", ["pending", "failed"])
def test_migration_does_not_relink_a_file_in_persistent_delete_state(
    monkeypatch, tmp_path, state
):
    """若删除中的记录重新获得引用，Task 1 的持久删除恢复会产生悬空关系。"""
    project = _create_legacy_project(
        monkeypatch,
        tmp_path,
        [{"filename": "正在删除.txt", "size": 1}],
        {},
    )
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")
    deleting = store.save_upload(
        FileStorage(stream=io.BytesIO(b"x"), filename="正在删除.txt"),
        "正在删除.txt",
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            INSERT INTO uploaded_file_delete_operations (
                operation_id, file_id, stored_filename, tombstone_filename,
                state, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (
                f"delete_{state}",
                deleting["file_id"],
                deleting["stored_filename"],
                f".{deleting['stored_filename']}.deleting-delete_{state}",
                state,
                "2026-09-04T00:00:00+00:00",
                "2026-09-04T00:00:00+00:00",
            ),
        )
    project.files[0]["file_id"] = deleting["file_id"]
    ProjectManager.save_project(project)

    result = store.migrate_legacy_projects()

    assert result == {"migrated": 0, "linked": 0, "skipped": 1}
    assert store.list_references(deleting["file_id"]) == []


def test_migration_skips_one_project_when_existing_source_lookup_fails(monkeypatch, tmp_path):
    """若已迁移来源查询暂时失败，批次必须以可观察的跳过结果继续返回。"""
    project = _create_legacy_project(
        monkeypatch,
        tmp_path,
        [{"filename": "查询失败.txt", "size": 1}],
        {},
    )
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")
    existing = store.save_upload(
        FileStorage(stream=io.BytesIO(b"x"), filename="查询失败.txt"),
        "查询失败.txt",
    )
    project.files[0]["file_id"] = existing["file_id"]
    ProjectManager.save_project(project)
    original_connect = store._connect

    def fail_lookup_connection():
        raise sqlite3.OperationalError("temporary database failure")

    monkeypatch.setattr(store, "_connect", fail_lookup_connection)

    result = store.migrate_legacy_projects()

    monkeypatch.setattr(store, "_connect", original_connect)
    assert result == {"migrated": 0, "linked": 0, "skipped": 1}
    assert store.list_references(existing["file_id"]) == []


def test_migration_cleans_copied_library_file_when_database_write_fails(monkeypatch, tmp_path):
    """若文件记录事务失败，本次复制的库文件不能遗留为无数据库记录的孤儿。"""
    project = _create_legacy_project(
        monkeypatch,
        tmp_path,
        [{"filename": "数据库失败.txt", "size": 1}],
        {"database-failure.txt": b"x"},
    )
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_legacy_upload_insert
            BEFORE INSERT ON uploaded_files
            BEGIN
                SELECT RAISE(ABORT, 'legacy upload rejected');
            END
            """
        )

    result = store.migrate_legacy_projects()

    assert result == {"migrated": 0, "linked": 0, "skipped": 1}
    assert store.list_files() == []
    assert list(store.library_dir.glob("*.txt")) == []
    assert (Path(ProjectManager._get_project_files_dir(project.project_id)) / "database-failure.txt").exists()


def test_broken_project_metadata_does_not_block_other_projects(monkeypatch, tmp_path):
    """若一个 project.json 损坏，其他合法项目仍必须完成自己的文件迁移。"""
    normal = _create_legacy_project(
        monkeypatch,
        tmp_path,
        [{"filename": "正常.txt", "size": 1}],
        {"normal.txt": b"n"},
    )
    broken_dir = Path(ProjectManager.PROJECTS_DIR) / "broken-project"
    broken_dir.mkdir()
    (broken_dir / "project.json").write_text("not json", encoding="utf-8")
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")

    result = store.migrate_legacy_projects()

    assert result["migrated"] == 1
    assert "file_id" in ProjectManager.get_project(normal.project_id).files[0]


def test_unreadable_project_file_directory_does_not_block_other_projects(monkeypatch, tmp_path):
    """若某项目目录枚举失败，迁移必须继续处理另一个项目。"""
    broken = _create_legacy_project(
        monkeypatch,
        tmp_path,
        [{"filename": "坏目录.txt", "size": 1}],
        {"broken.txt": b"b"},
    )
    normal = _create_legacy_project(
        monkeypatch,
        tmp_path,
        [{"filename": "正常.txt", "size": 1}],
        {"normal.txt": b"n"},
    )
    original_get_project_files = ProjectManager.get_project_files

    def fail_one_directory(project_id):
        if project_id == broken.project_id:
            raise OSError("files directory is unavailable")
        return original_get_project_files(project_id)

    monkeypatch.setattr(ProjectManager, "get_project_files", fail_one_directory)
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")

    result = store.migrate_legacy_projects()

    assert result["migrated"] == 1
    assert result["skipped"] >= 1
    assert "file_id" in ProjectManager.get_project(normal.project_id).files[0]


def test_explicit_saved_filename_wins_over_an_earlier_display_name_match(monkeypatch, tmp_path):
    """若展示名先抢占确定的 saved_filename，迁移会把唯一物理内容绑定到错误快照。"""
    project = _create_legacy_project(
        monkeypatch,
        tmp_path,
        [
            {"filename": "shared.txt", "size": 1},
            {"filename": "强标识.txt", "size": 1, "saved_filename": "shared.txt"},
        ],
        {"shared.txt": b"shared"},
    )
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")

    result = store.migrate_legacy_projects()

    refreshed = ProjectManager.get_project(project.project_id)
    assert result == {"migrated": 1, "linked": 1, "skipped": 1}
    assert "file_id" not in refreshed.files[0]
    assert "file_id" in refreshed.files[1]
    assert store.get_file(refreshed.files[1]["file_id"])["display_name"] == "强标识.txt"


def test_duplicate_display_names_fall_back_to_the_existing_directory_order(monkeypatch, tmp_path):
    """重复展示名不唯一，必须放弃展示名匹配并保持旧目录顺序，不得按快照顺序抢占。"""
    project = _create_legacy_project(
        monkeypatch,
        tmp_path,
        [{"filename": "同名.txt", "size": 1}, {"filename": "同名.txt", "size": 1}],
        {"同名.txt": b"same", "other.txt": b"other"},
    )
    original_get_project_files = ProjectManager.get_project_files
    files_dir = Path(ProjectManager._get_project_files_dir(project.project_id))

    def reverse_legacy_order(project_id):
        if project_id == project.project_id:
            return [str(files_dir / "other.txt"), str(files_dir / "同名.txt")]
        return original_get_project_files(project_id)

    monkeypatch.setattr(ProjectManager, "get_project_files", reverse_legacy_order)
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")

    result = store.migrate_legacy_projects()

    refreshed = ProjectManager.get_project(project.project_id)
    assert result == {"migrated": 2, "linked": 2, "skipped": 0}
    first = store.get_file(refreshed.files[0]["file_id"])
    second = store.get_file(refreshed.files[1]["file_id"])
    assert (store.library_dir / first["stored_filename"]).read_bytes() == b"other"
    assert (store.library_dir / second["stored_filename"]).read_bytes() == b"same"


def test_partial_project_metadata_write_keeps_original_snapshot_and_migration_retries(
    monkeypatch, tmp_path
):
    """若 project.json 临时写入中断，原快照必须仍可读，随后迁移能收敛。"""
    project = _create_legacy_project(
        monkeypatch,
        tmp_path,
        [{"filename": "原子写入.txt", "size": 1}],
        {"atomic.txt": b"a"},
    )
    store = UploadedFileStore(tmp_path / "files.db", tmp_path / "library")
    meta_path = Path(ProjectManager._get_project_meta_path(project.project_id))
    original_metadata = meta_path.read_bytes()
    original_dump = project_module.json.dump

    def write_partial_json(data, target, **kwargs):
        target.write('{"project_id":')
        target.flush()
        raise OSError("disk full")

    monkeypatch.setattr(project_module.json, "dump", write_partial_json)

    failed_result = store.migrate_legacy_projects()

    assert failed_result == {"migrated": 0, "linked": 0, "skipped": 1}
    assert meta_path.read_bytes() == original_metadata
    assert "file_id" not in ProjectManager.get_project(project.project_id).files[0]
    assert not list(meta_path.parent.glob(".project.json.*.tmp"))

    monkeypatch.setattr(project_module.json, "dump", original_dump)
    recovered_result = store.migrate_legacy_projects()

    assert recovered_result == {"migrated": 0, "linked": 0, "skipped": 0}
    assert "file_id" in ProjectManager.get_project(project.project_id).files[0]


def test_list_projects_skips_valid_json_with_non_string_created_at(monkeypatch, tmp_path):
    """可解析但排序字段畸形的项目不能在全局排序时阻断正常项目发现。"""
    normal = _create_legacy_project(monkeypatch, tmp_path, [], {})
    malformed = _create_legacy_project(monkeypatch, tmp_path, [], {})
    malformed_path = Path(ProjectManager._get_project_meta_path(malformed.project_id))
    metadata = json.loads(malformed_path.read_text(encoding="utf-8"))
    metadata["created_at"] = []
    malformed_path.write_text(json.dumps(metadata), encoding="utf-8")

    projects = ProjectManager.list_projects(limit=None)

    assert [project.project_id for project in projects] == [normal.project_id]


def test_deleting_legacy_project_before_files_api_migrates_the_only_source(
    monkeypatch,
    tmp_path,
):
    """若删除入口未执行迁移，尚未访问文件页的旧项目会连同唯一源文件一起消失。"""
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(upload_dir / "projects"))
    project = ProjectManager.create_project("未访问文件页的旧项目")
    source_path = Path(ProjectManager._get_project_files_dir(project.project_id)) / "only.txt"
    source_path.write_bytes(b"the only legacy source")
    project.files = [{
        "filename": "唯一资料.txt",
        "saved_filename": "only.txt",
        "size": len(b"the only legacy source"),
    }]
    ProjectManager.save_project(project)

    assert ProjectManager.delete_project(project.project_id) is True

    store = UploadedFileStore()
    files = store.list_files()
    assert len(files) == 1
    assert files[0]["display_name"] == "唯一资料.txt"
    assert (store.library_dir / files[0]["stored_filename"]).read_bytes() == (
        b"the only legacy source"
    )
    assert store.list_references(files[0]["file_id"]) == []


def test_deleting_project_is_blocked_when_legacy_migration_skips_a_source(
    monkeypatch,
    tmp_path,
):
    """若迁移无法唯一匹配旧源，删除必须停下并保留项目目录中的全部候选内容。"""
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(upload_dir / "projects"))
    project = ProjectManager.create_project("迁移不完整的旧项目")
    files_dir = Path(ProjectManager._get_project_files_dir(project.project_id))
    (files_dir / "first.txt").write_bytes(b"first")
    (files_dir / "second.txt").write_bytes(b"second")
    project.files = [{"filename": "无法匹配.txt", "size": 5}]
    ProjectManager.save_project(project)

    with pytest.raises(FileStorageError, match="迁移"):
        ProjectManager.delete_project(project.project_id)

    assert ProjectManager.get_project(project.project_id) is not None
    assert {path.name for path in files_dir.iterdir()} == {"first.txt", "second.txt"}
