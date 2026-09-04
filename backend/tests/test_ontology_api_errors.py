import io
from pathlib import Path

from app import create_app
from app.api import graph as graph_api
from app.config import Config
from app.models.project import ProjectManager, ProjectStatus
from app.services.uploaded_file_store import UploadedFileStore
from app.utils.llm_client import LLMResponseError


class TestConfig(Config):
    TESTING = True


def _post_ontology(client):
    return client.post(
        "/api/graph/ontology/generate",
        data={
            "simulation_requirement": "Simulate the discussion.",
            "files": (io.BytesIO(b"A short source document."), "source.md"),
        },
        content_type="multipart/form-data",
    )


def test_ontology_api_returns_safe_truncation_error_and_deletes_project(
    tmp_path,
    monkeypatch,
):
    class FailingGenerator:
        def generate(self, **kwargs):
            raise LLMResponseError(
                "LLM JSON output was truncated at the token limit",
                finish_reason="length",
            )

    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(upload_dir / "projects"))
    monkeypatch.setattr(graph_api, "OntologyGenerator", FailingGenerator)

    app = create_app(TestConfig)
    response = _post_ontology(app.test_client())

    assert response.status_code == 502
    assert response.json["success"] is False
    assert "token limit" in response.json["error"]
    assert "traceback" not in response.json
    assert "data" not in response.json
    assert ProjectManager.list_projects(limit=None) == []
    assert list(Path(ProjectManager.PROJECTS_DIR).iterdir()) == []
    files = UploadedFileStore().list_files()
    assert [file["display_name"] for file in files] == ["source.md"]
    assert [file["reference_count"] for file in files] == [0]


def test_ontology_api_does_not_expose_provider_error_body(tmp_path, monkeypatch):
    class ProviderError(RuntimeError):
        status_code = 401
        request_id = "request-safe-id"
        body = {"error": {"message": "SECRET-PROVIDER-BODY"}}

    class FailingGenerator:
        def generate(self, **kwargs):
            raise ProviderError("SECRET-PROVIDER-BODY")

    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(upload_dir))
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(upload_dir / "projects"))
    monkeypatch.setattr(graph_api, "OntologyGenerator", FailingGenerator)

    app = create_app(TestConfig)
    response = _post_ontology(app.test_client())

    assert response.status_code == 502
    assert "HTTP 401" in response.json["error"]
    assert "request-safe-id" in response.json["error"]
    assert "SECRET-PROVIDER-BODY" not in response.get_data(as_text=True)
    assert "traceback" not in response.json
    assert "data" not in response.json
    assert ProjectManager.list_projects(limit=None) == []
    assert list(Path(ProjectManager.PROJECTS_DIR).iterdir()) == []
    files = UploadedFileStore().list_files()
    assert [file["display_name"] for file in files] == ["source.md"]
    assert [file["reference_count"] for file in files] == [0]


def test_interrupted_ontology_generation_resumes_from_persisted_text(monkeypatch):
    project = type("Project", (), {
        "project_id": "proj-1",
        "simulation_requirement": "requirement",
        "ontology_additional_context": "context",
        "ontology_generation_pending": True,
        "ontology": None,
        "status": ProjectStatus.CREATED,
        "error": None,
    })()
    saved = []

    class Generator:
        def generate(self, **kwargs):
            assert kwargs["document_texts"] == ["persisted text"]
            assert kwargs["additional_context"] == "context"
            return {"entity_types": [], "edge_types": [], "analysis_summary": "done"}

    monkeypatch.setattr(graph_api, "OntologyGenerator", Generator)
    monkeypatch.setattr(
        graph_api.ProjectManager,
        "list_projects",
        classmethod(lambda cls, limit=None: [project]),
    )
    monkeypatch.setattr(
        graph_api.ProjectManager,
        "get_extracted_text",
        classmethod(lambda cls, project_id: "persisted text"),
    )
    monkeypatch.setattr(
        graph_api.ProjectManager,
        "save_project",
        classmethod(lambda cls, value: saved.append(value.status)),
    )

    assert graph_api.recover_interrupted_ontology_generations() == 1
    assert project.status == ProjectStatus.ONTOLOGY_GENERATED
    assert project.ontology_generation_pending is False
    assert saved == [ProjectStatus.ONTOLOGY_GENERATED]
