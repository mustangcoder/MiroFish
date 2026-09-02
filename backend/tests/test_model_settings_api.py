from app import create_app


def test_model_metadata_endpoint_returns_known_context():
    app = create_app()
    response = app.test_client().get("/api/settings/models/metadata?model=gpt-5.6-luna")
    assert response.status_code == 200
    assert response.get_json()["data"] == {
        "model": "gpt-5.6-luna",
        "context_window_tokens": 1_050_000,
    }


def test_model_metadata_endpoint_returns_null_for_unknown_model():
    app = create_app()
    response = app.test_client().get("/api/settings/models/metadata?model=custom-model")
    assert response.status_code == 200
    assert response.get_json()["data"]["context_window_tokens"] is None
