from app.services.protocol_detector import ProtocolDetector


class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


def test_custom_service_detects_text_and_embedding_protocols_from_models():
    calls = []

    def request(method, url, **_kwargs):
        calls.append((method, url))
        if url.endswith("/models"):
            return Response(200, {"data": [
                {"id": "qwen/text-model"},
                {"id": "qwen-embedding-model"},
            ]})
        if url.endswith("/responses"):
            return Response(400, {"error": {"message": "model not found"}})
        return Response(404, {"error": "Unexpected endpoint"})

    result = ProtocolDetector(request=request).detect({
        "vendor": "custom", "auth_type": "none",
        "base_url": "http://model.internal/v1", "api_key": "",
    })

    states = {item["protocol"]: item["verification_status"] for item in result}
    assert states["openai_chat_completions"] == "passed"
    assert states["openai_embeddings"] == "passed"
    assert states["openai_responses"] == "passed"
    assert states["anthropic_messages"] == "failed"
    assert all("api_key" not in item for item in result)
