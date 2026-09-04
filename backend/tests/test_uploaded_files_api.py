"""已上传文件库 HTTP API 契约测试。"""

from io import BytesIO
import os
from pathlib import Path
import sqlite3

import pytest

from app import create_app
from app.api import files as files_api
from app.config import Config
from app.models.project import ProjectManager
from app.services import uploaded_file_store
from app.services.uploaded_file_store import FileStorageError, UploadedFileStore


class TestConfig(Config):
    """避免应用工厂在测试中初始化生产依赖。"""

    TESTING = True


@pytest.fixture
def client(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(upload_dir / "projects"))
    app = create_app(TestConfig)
    return app.test_client()


def _upload(client, filename, contents="内容".encode()):
    response = client.post(
        "/api/files",
        data={"files": (BytesIO(contents), filename)},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    body = response.get_json()
    assert body["success"] is True
    assert body["error"] is None
    assert len(body["data"]) == 1
    return body["data"][0]


def test_list_clamps_pagination_and_searches_uploaded_files(client):
    _upload(client, "market-analysis.txt")
    _upload(client, "meeting-notes.md")

    response = client.get("/api/files?query=market&limit=999&offset=-5")

    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["limit"] == 200
    assert body["offset"] == 0
    assert len(body["data"]) == 1
    assert body["data"][0]["display_name"] == "market-analysis.txt"
    assert body["data"][0]["extension"] == "txt"
    assert body["data"][0]["size"] == len("内容".encode())
    assert body["data"][0]["reference_count"] == 0


def test_first_file_api_read_migrates_legacy_projects(client):
    project = ProjectManager.create_project("历史项目")
    files_dir = ProjectManager._get_project_files_dir(project.project_id)
    (Path(files_dir) / "legacy.txt").write_bytes(b"legacy content")
    project.files = [{
        "filename": "历史资料.txt",
        "size": len(b"legacy content"),
        "saved_filename": "legacy.txt",
    }]
    ProjectManager.save_project(project)

    response = client.get("/api/files")

    assert response.status_code == 200
    body = response.get_json()
    assert [file["display_name"] for file in body["data"]] == ["历史资料.txt"]
    assert body["data"][0]["reference_count"] == 1
    refreshed = ProjectManager.get_project(project.project_id)
    assert refreshed.files[0]["file_id"] == body["data"][0]["file_id"]


def test_partial_legacy_migration_is_retried_until_it_completes_then_cached(client, monkeypatch):
    project = ProjectManager.create_project("待重试历史项目")
    files_dir = Path(ProjectManager._get_project_files_dir(project.project_id))
    (files_dir / "legacy.txt").write_bytes(b"legacy content")
    project.files = [{"filename": "待回写.txt", "size": 14}]
    ProjectManager.save_project(project)

    original_migrate = files_api.UploadedFileStore.migrate_legacy_projects
    migration_calls = []

    def count_migration(store):
        migration_calls.append(store.database_path)
        return original_migrate(store)

    original_save_project = ProjectManager.save_project

    def fail_snapshot_write(project_to_save):
        if project_to_save.project_id == project.project_id:
            raise OSError("project.json is read-only")
        original_save_project(project_to_save)

    monkeypatch.setattr(files_api.UploadedFileStore, "migrate_legacy_projects", count_migration)
    monkeypatch.setattr(ProjectManager, "save_project", fail_snapshot_write)

    first = client.get("/api/files")

    assert first.status_code == 200
    assert "file_id" not in ProjectManager.get_project(project.project_id).files[0]
    assert len(migration_calls) == 1

    monkeypatch.setattr(ProjectManager, "save_project", original_save_project)
    second = client.get("/api/files")
    third = client.get("/api/files")

    assert second.status_code == 200
    assert third.status_code == 200
    assert "file_id" in ProjectManager.get_project(project.project_id).files[0]
    assert len(migration_calls) == 2


def test_file_api_migrates_once_for_same_storage_configuration(client, monkeypatch):
    original_migrate = files_api.UploadedFileStore.migrate_legacy_projects
    calls = []

    def count_migration(store):
        calls.append(store.database_path)
        return original_migrate(store)

    monkeypatch.setattr(files_api.UploadedFileStore, "migrate_legacy_projects", count_migration)

    assert client.get("/api/files").status_code == 200
    assert client.get("/api/files").status_code == 200

    assert len(calls) == 1


def test_failed_file_api_migration_is_retried_on_next_request(client, monkeypatch):
    original_migrate = files_api.UploadedFileStore.migrate_legacy_projects
    calls = []

    def fail_once(store):
        calls.append(store.database_path)
        if len(calls) == 1:
            raise sqlite3.OperationalError("migration unavailable")
        return original_migrate(store)

    monkeypatch.setattr(files_api.UploadedFileStore, "migrate_legacy_projects", fail_once)

    failed = client.get("/api/files")
    recovered = client.get("/api/files")

    assert failed.status_code == 500
    assert failed.get_json() == {
        "success": False,
        "data": None,
        "error": "文件存储失败，请稍后重试",
    }
    assert recovered.status_code == 200
    assert len(calls) == 2


def test_list_returns_sql_reference_counts_without_per_file_queries(client, monkeypatch):
    uploaded = _upload(client, "referenced.txt")
    store = UploadedFileStore()
    project = ProjectManager.create_project("引用项目")
    store.add_project_references(project.project_id, [uploaded["file_id"]])

    def fail_per_file_lookup(_store, _file_id):
        raise AssertionError("列表不得逐项查询引用")

    monkeypatch.setattr(files_api.UploadedFileStore, "list_references", fail_per_file_lookup)
    response = client.get("/api/files")

    assert response.status_code == 200
    assert response.get_json()["data"][0]["reference_count"] == 1


def test_upload_accepts_multiple_files_and_rejects_disallowed_extensions(client):
    response = client.post(
        "/api/files",
        data={
            "files": [
                (BytesIO(b"first"), "first.txt"),
                (BytesIO(b"second"), "second.md"),
            ],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    assert [file["display_name"] for file in response.get_json()["data"]] == [
        "first.txt",
        "second.md",
    ]
    assert response.get_json()["error"] is None

    invalid = client.post(
        "/api/files",
        data={"files": (BytesIO(b"binary"), "malware.exe")},
        content_type="multipart/form-data",
    )

    assert invalid.status_code == 400
    assert invalid.get_json() == {
        "success": False,
        "data": None,
        "error": "不支持的文件扩展名",
    }


def test_upload_rejects_mixed_invalid_batch_without_persisting_valid_files(client):
    response = client.post(
        "/api/files",
        data={
            "files": [
                (BytesIO(b"valid"), "valid.txt"),
                (BytesIO(b"invalid"), "invalid.exe"),
            ],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "success": False,
        "data": None,
        "error": "不支持的文件扩展名",
    }
    assert client.get("/api/files").get_json()["data"] == []


def test_upload_too_large_returns_json_error(client):
    client.application.config["MAX_CONTENT_LENGTH"] = 1

    response = client.post(
        "/api/files",
        data={"files": (BytesIO(b"too large"), "large.txt")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert response.get_json() == {
        "success": False,
        "data": None,
        "error": "上传文件总大小不能超过50MB",
    }


def test_rename_rejects_empty_name_and_returns_404_for_missing_file(client):
    uploaded = _upload(client, "draft.txt")

    empty_name = client.patch(f"/api/files/{uploaded['file_id']}", json={"display_name": ""})
    missing = client.patch("/api/files/file_missing", json={"display_name": "renamed.txt"})

    assert empty_name.status_code == 400
    assert empty_name.get_json() == {
        "success": False,
        "data": None,
        "error": "展示名称必须是非空文件名",
    }
    assert missing.status_code == 404
    assert missing.get_json() == {
        "success": False,
        "data": None,
        "error": "文件不存在: file_missing",
    }


@pytest.mark.parametrize("payload", [[], "new-name.txt", None])
def test_rename_rejects_non_object_json_payloads(client, payload):
    uploaded = _upload(client, "draft.txt")

    response = client.patch(f"/api/files/{uploaded['file_id']}", json=payload)

    assert response.status_code == 400
    assert response.get_json() == {
        "success": False,
        "data": None,
        "error": "请求体必须是 JSON 对象",
    }


def test_download_uses_display_name_and_missing_resources_return_json_404(client):
    uploaded = _upload(client, "downloadable.md", b"downloadable")

    download = client.get(f"/api/files/{uploaded['file_id']}/download")
    missing_download = client.get("/api/files/file_missing/download")
    missing_references = client.get("/api/files/file_missing/references")

    assert download.status_code == 200
    assert download.data == b"downloadable"
    assert "filename=downloadable.md" in download.headers["Content-Disposition"]
    assert missing_download.status_code == 404
    assert missing_download.get_json() == {
        "success": False,
        "data": None,
        "error": "文件不存在: file_missing",
    }
    assert missing_references.status_code == 404
    assert missing_references.get_json() == {
        "success": False,
        "data": None,
        "error": "文件不存在: file_missing",
    }


def test_download_missing_physical_content_returns_json_error(client):
    uploaded = _upload(client, "missing-content.txt")
    store = UploadedFileStore()
    (store.library_dir / uploaded["stored_filename"]).unlink()

    response = client.get(f"/api/files/{uploaded['file_id']}/download")

    assert response.status_code == 404
    assert response.get_json() == {
        "success": False,
        "data": None,
        "error": "文件内容不存在",
    }


def test_referenced_file_delete_returns_references_and_unreferenced_file_is_removed(client):
    uploaded = _upload(client, "shared.txt")
    store = UploadedFileStore()
    projects = [
        ProjectManager.create_project("项目 A"),
        ProjectManager.create_project("项目 B"),
    ]
    for project in projects:
        store.add_project_references(project.project_id, [uploaded["file_id"]])

    referenced = client.delete(f"/api/files/{uploaded['file_id']}")
    references = client.get(f"/api/files/{uploaded['file_id']}/references")

    assert referenced.status_code == 409
    expected_references = {
        project.project_id: {
            "project_id": project.project_id,
            "project_name": project.name,
            "position": 0,
        }
        for project in projects
    }
    referenced_body = referenced.get_json()
    assert referenced_body["success"] is False
    assert referenced_body["error"] == f"文件 {uploaded['file_id']} 仍被项目引用"
    assert {
        item["project_id"]: item
        for item in referenced_body["data"]["references"]
    } == expected_references
    references_body = references.get_json()
    assert references_body["success"] is True
    assert references_body["error"] is None
    assert {
        item["project_id"]: item for item in references_body["data"]
    } == expected_references

    for project in projects:
        store.remove_project_references(project.project_id)
    removed = client.delete(f"/api/files/{uploaded['file_id']}")

    assert removed.status_code == 200
    assert removed.get_json() == {"success": True, "data": None, "error": None}
    assert client.get(f"/api/files/{uploaded['file_id']}/references").status_code == 404


def test_referenced_delete_returns_transaction_snapshot_without_second_query(client, monkeypatch):
    uploaded = _upload(client, "transactional-reference.txt")
    store = UploadedFileStore()
    project = ProjectManager.create_project("事务项目")
    store.add_project_references(project.project_id, [uploaded["file_id"]])

    def fail_second_query(_store, _file_id):
        raise AssertionError("API must use FileInUseError.references")

    monkeypatch.setattr(files_api.UploadedFileStore, "list_references", fail_second_query)
    response = client.delete(f"/api/files/{uploaded['file_id']}")

    assert response.status_code == 409
    assert response.get_json() == {
        "success": False,
        "data": {"references": [{
            "project_id": project.project_id,
            "project_name": "事务项目",
            "position": 0,
        }]},
        "error": f"文件 {uploaded['file_id']} 仍被项目引用",
    }


def test_file_storage_error_returns_json_error(client, monkeypatch):
    uploaded = _upload(client, "undeletable.txt")

    def fail_delete(_store, _file_id):
        raise FileStorageError("disk unavailable")

    monkeypatch.setattr(files_api.UploadedFileStore, "delete_file", fail_delete)
    client.application.config["PROPAGATE_EXCEPTIONS"] = False
    response = client.delete(f"/api/files/{uploaded['file_id']}")

    assert response.status_code == 500
    assert response.get_json() == {
        "success": False,
        "data": None,
        "error": "文件存储失败，请稍后重试",
    }


def test_reference_api_includes_project_names_and_keeps_missing_project_ids(client):
    """若引用摘要没有项目名，删除阻塞弹窗只能展示不透明 ID。"""
    uploaded = _upload(client, "named-reference.txt")
    store = UploadedFileStore()
    project = ProjectManager.create_project("可识别项目")
    store.add_project_references(project.project_id, [uploaded["file_id"]])
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            INSERT INTO project_files (project_id, file_id, position)
            VALUES ('missing-project', ?, 0)
            """,
            (uploaded["file_id"],),
        )

    response = client.get(f"/api/files/{uploaded['file_id']}/references")

    assert response.status_code == 200
    references = {
        item["project_id"]: item for item in response.get_json()["data"]
    }
    assert references[project.project_id] == {
        "project_id": project.project_id,
        "project_name": "可识别项目",
        "position": 0,
    }
    assert references["missing-project"] == {
        "project_id": "missing-project",
        "project_name": None,
        "position": 0,
    }


def test_unrestored_delete_failure_is_not_returned_by_file_list(client, monkeypatch):
    """若无法恢复的删除失败仍出现在 API 列表，文件选择器会把它当作可复用文件。"""
    uploaded = _upload(client, "unrestored.txt", b"contents")
    stored_path = Path(Config.UPLOAD_FOLDER) / "library" / uploaded["stored_filename"]
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

    failed_delete = client.delete(f"/api/files/{uploaded['file_id']}")
    listed = client.get("/api/files")

    assert failed_delete.status_code == 500
    assert listed.status_code == 200
    assert listed.get_json()["data"] == []
