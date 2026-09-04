"""项目创建复用文件库资源的端到端契约测试。"""

from io import BytesIO
from pathlib import Path
import sqlite3

import pytest
from werkzeug.datastructures import FileStorage, MultiDict

from app import create_app
from app.api import graph as graph_api
from app.config import Config
from app.models.project import ProjectManager
from app.services.uploaded_file_store import UploadedFileStore


class TestConfig(Config):
    """避免应用工厂初始化生产依赖。"""

    TESTING = True


@pytest.fixture
def project_api(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(upload_dir / "projects"))

    generated_document_texts = []

    class RecordingGenerator:
        def generate(self, **kwargs):
            generated_document_texts.append(kwargs["document_texts"])
            return {
                "entity_types": [{"name": "Person"}],
                "edge_types": [],
                "analysis_summary": "生成成功",
            }

    monkeypatch.setattr(graph_api, "OntologyGenerator", RecordingGenerator)
    app = create_app(TestConfig)
    return app.test_client(), UploadedFileStore(), generated_document_texts


def _save_library_file(store, filename, content):
    upload = FileStorage(stream=BytesIO(content), filename=filename)
    return store.save_upload(upload, filename)


def _post_generate(client, fields):
    data = MultiDict([
        ("simulation_requirement", "模拟讨论"),
        *fields,
    ])
    return client.post(
        "/api/graph/ontology/generate",
        data=data,
        content_type="multipart/form-data",
    )


def test_generate_ontology_can_create_project_from_existing_file_only(project_api):
    """若接口忽略 file_ids，仅选择已有文件时会错误返回“请上传文件”。"""
    client, store, generated_document_texts = project_api
    existing = _save_library_file(store, "existing.txt", b"existing source")

    response = _post_generate(client, [("file_ids", existing["file_id"])])

    assert response.status_code == 200
    body = response.get_json()
    project_id = body["data"]["project_id"]
    assert body["data"]["files"] == [{
        "file_id": existing["file_id"],
        "filename": "existing.txt",
        "size": len(b"existing source"),
    }]
    assert generated_document_texts == [["existing source"]]
    assert ProjectManager.get_extracted_text(project_id) == (
        "\n\n=== existing.txt ===\nexisting source"
    )
    assert store.list_references(existing["file_id"]) == [{
        "project_id": project_id,
        "project_name": "Unnamed Project",
        "position": 0,
    }]


def test_generate_ontology_deduplicates_existing_ids_before_new_uploads(project_api):
    """若重复 ID 未按首次出现去重，快照、语料和引用位置会相互分叉。"""
    client, store, generated_document_texts = project_api
    first = _save_library_file(store, "first.txt", b"first existing")
    second = _save_library_file(store, "second.md", b"second existing")

    response = _post_generate(client, [
        ("file_ids", second["file_id"]),
        ("file_ids", second["file_id"]),
        ("file_ids", first["file_id"]),
        ("files", (BytesIO(b"first new"), "third.txt")),
        ("files", (BytesIO(b"second new"), "fourth.md")),
    ])

    assert response.status_code == 200
    body = response.get_json()
    project_id = body["data"]["project_id"]
    assert [item["filename"] for item in body["data"]["files"]] == [
        "second.md",
        "first.txt",
        "third.txt",
        "fourth.md",
    ]
    assert generated_document_texts == [[
        "second existing",
        "first existing",
        "first new",
        "second new",
    ]]
    assert [
        reference["position"]
        for file_info in body["data"]["files"]
        for reference in store.list_references(file_info["file_id"])
        if reference["project_id"] == project_id
    ] == [0, 1, 2, 3]


def test_generate_ontology_rejects_empty_sources_without_creating_project(project_api):
    """若空来源仍创建项目，会留下无法继续处理的空项目。"""
    client, _store, _generated_document_texts = project_api

    response = _post_generate(client, [])

    assert response.status_code == 400
    assert ProjectManager.list_projects(limit=None) == []


def test_generate_ontology_rejects_unknown_id_before_project_or_upload(project_api):
    """若未知 ID 校验过晚，会先创建项目或把同请求新文件写入文件库。"""
    client, store, generated_document_texts = project_api

    response = _post_generate(client, [
        ("file_ids", "file_missing"),
        ("files", (BytesIO(b"must not be saved"), "new.txt")),
    ])

    assert response.status_code == 404
    assert response.get_json()["success"] is False
    assert ProjectManager.list_projects(limit=None) == []
    assert store.list_files() == []
    assert generated_document_texts == []


def test_parse_failure_deletes_project_and_references_but_keeps_assets(
    project_api,
    monkeypatch,
):
    """若解析异常未清引用，失败项目会永久阻止库文件删除。"""
    client, store, _generated_document_texts = project_api
    existing = _save_library_file(store, "existing.txt", b"existing source")

    def fail_extract(_path):
        raise RuntimeError("parser internals must stay private")

    monkeypatch.setattr(graph_api.FileParser, "extract_text", fail_extract)
    response = _post_generate(client, [
        ("file_ids", existing["file_id"]),
        ("files", (BytesIO(b"new source"), "new.txt")),
    ])

    assert response.status_code == 500
    body = response.get_json()
    assert body == {
        "success": False,
        "error": "Ontology generation failed; check the server logs",
    }
    assert ProjectManager.list_projects(limit=None) == []
    assert list(Path(ProjectManager.PROJECTS_DIR).iterdir()) == []
    assert store.list_references(existing["file_id"]) == []
    files = store.list_files(limit=10)
    assert {item["display_name"] for item in files} == {"existing.txt", "new.txt"}
    assert all(item["reference_count"] == 0 for item in files)
    assert all((store.library_dir / item["stored_filename"]).is_file() for item in files)


def test_delete_project_releases_file_reference_without_deleting_asset(project_api):
    """若删除项目不解除引用，仍存在的库文件将无法被用户删除。"""
    client, store, _generated_document_texts = project_api
    existing = _save_library_file(store, "existing.txt", b"existing source")
    created = _post_generate(client, [("file_ids", existing["file_id"])])
    project_id = created.get_json()["data"]["project_id"]

    response = client.delete(f"/api/graph/project/{project_id}")

    assert response.status_code == 200
    assert ProjectManager.get_project(project_id) is None
    assert store.list_references(existing["file_id"]) == []
    assert store.get_file(existing["file_id"]) is not None
    assert (store.library_dir / existing["stored_filename"]).is_file()


def test_generate_ontology_rejects_mixed_invalid_uploads_before_any_write(project_api):
    """若创建接口静默过滤非法项，用户会用缺失语料创建一个看似成功的项目。"""
    client, store, generated_document_texts = project_api

    response = _post_generate(client, [
        ("files", (BytesIO(b"valid"), "valid.txt")),
        ("files", (BytesIO(b"invalid"), "invalid.exe")),
    ])

    assert response.status_code == 400
    assert response.get_json()["success"] is False
    assert ProjectManager.list_projects(limit=None) == []
    assert store.list_files() == []
    assert generated_document_texts == []


def test_generate_ontology_rejects_path_filename_as_400_before_any_write(project_api):
    """若路径型名称只在保存阶段抛异常，API 会把客户端错误错误映射为 500。"""
    client, store, generated_document_texts = project_api

    response = _post_generate(client, [
        ("files", (BytesIO(b"invalid"), "../invalid.txt")),
    ])

    assert response.status_code == 400
    assert response.get_json()["success"] is False
    assert ProjectManager.list_projects(limit=None) == []
    assert store.list_files() == []
    assert generated_document_texts == []


def test_generate_ontology_runs_legacy_migration_before_new_upload(project_api):
    """若 ontology 写入口绕过共享迁移门禁，旧项目文件只能等用户打开文件页才出现。"""
    client, store, _generated_document_texts = project_api
    legacy = ProjectManager.create_project("历史项目")
    legacy_path = Path(ProjectManager._get_project_files_dir(legacy.project_id)) / "legacy.txt"
    legacy_path.write_bytes(b"legacy")
    legacy.files = [{
        "filename": "历史资料.txt",
        "saved_filename": "legacy.txt",
        "size": len(b"legacy"),
    }]
    ProjectManager.save_project(legacy)

    response = _post_generate(client, [
        ("files", (BytesIO(b"new"), "new.txt")),
    ])

    assert response.status_code == 200
    refreshed = ProjectManager.get_project(legacy.project_id)
    assert "file_id" in refreshed.files[0]
    library_files = {item["display_name"]: item for item in store.list_files()}
    assert set(library_files) == {"历史资料.txt", "new.txt"}
    assert (
        store.library_dir / library_files["历史资料.txt"]["stored_filename"]
    ).read_bytes() == b"legacy"


def test_generate_ontology_rejects_unreferenceable_file_before_project_creation(project_api):
    """若预校验只看元数据，删除失败状态会在建项目后才变成通用 500。"""
    client, store, generated_document_texts = project_api
    existing = _save_library_file(store, "deleting.txt", b"deleting")
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            """
            INSERT INTO uploaded_file_delete_operations (
                operation_id, file_id, stored_filename, tombstone_filename,
                state, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'failed', ?, ?, ?)
            """,
            (
                "delete_unreferenceable",
                existing["file_id"],
                existing["stored_filename"],
                f".{existing['stored_filename']}.deleting-delete_unreferenceable",
                "restore failed",
                "9999-01-01T00:00:00+00:00",
                "9999-01-01T00:00:00+00:00",
            ),
        )

    response = _post_generate(client, [("file_ids", existing["file_id"])])

    assert response.status_code == 409
    assert response.get_json()["success"] is False
    assert ProjectManager.list_projects(limit=None) == []
    assert generated_document_texts == []


def test_generate_ontology_maps_reference_race_to_409_and_removes_temporary_project(
    project_api,
    monkeypatch,
):
    """若文件在预查后被并发删除，建立引用失败不能泄漏项目或退化为通用 500。"""
    client, store, generated_document_texts = project_api
    existing = _save_library_file(store, "racing.txt", b"racing")

    def lose_reference_race(_store, _project_id, _file_ids):
        raise sqlite3.IntegrityError("文件不存在或正在删除")

    monkeypatch.setattr(
        graph_api.UploadedFileStore,
        "add_project_references",
        lose_reference_race,
    )

    response = _post_generate(client, [("file_ids", existing["file_id"])])

    assert response.status_code == 409
    assert response.get_json()["success"] is False
    assert ProjectManager.list_projects(limit=None) == []
    assert generated_document_texts == []
