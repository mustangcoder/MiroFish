from __future__ import annotations

import hmac
import time
import uuid

from flask import Flask, jsonify, request

from .provider import CircuitOpenError
from .responses_client import ProviderResponseError


def _error(message, status, code=None):
    payload = {"message": message, "type": "gateway_error"}
    if code:
        payload["code"] = code
    return jsonify({"error": payload}), status


def _provider_error(error):
    if isinstance(error, ProviderResponseError):
        return _error(
            "LLM provider is temporarily unavailable" if error.retryable else "LLM provider rejected the request",
            503 if error.retryable else 400,
            error.code,
        )
    status = getattr(error, "status_code", None) or getattr(getattr(error, "response", None), "status_code", None)
    if status == 429:
        return _error("LLM provider rate limited the request", 429, "rate_limited")
    if status == 400:
        return _error("LLM provider rejected the request", 400, "upstream_invalid_request")
    if status and status >= 500:
        return _error("LLM provider is temporarily unavailable", 503, "upstream_error")
    return _error("LLM provider request failed", 502)


def _responses_messages(payload):
    messages = []
    instructions = payload.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": str(instructions)})
    input_value = payload.get("input")
    if isinstance(input_value, str):
        messages.append({"role": "user", "content": input_value})
    elif isinstance(input_value, list):
        for item in input_value:
            if not isinstance(item, dict):
                raise ValueError("input items must be messages")
            if item.get("type") == "function_call":
                messages.append({
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": item.get("call_id") or item.get("id", ""),
                        "type": "function",
                        "function": {"name": item.get("name", ""), "arguments": item.get("arguments", "{}")},
                    }],
                })
                continue
            if item.get("type") == "function_call_output":
                messages.append({"role": "tool", "tool_call_id": item.get("call_id", ""), "content": str(item.get("output", ""))})
                continue
            if item.get("role") not in {"system", "developer", "user", "assistant"}:
                raise ValueError("input items must be messages or function calls")
            content = item.get("content", "")
            if isinstance(content, list):
                content = "".join(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") in {"input_text", "output_text", "text"}
                )
            messages.append({"role": item["role"], "content": str(content)})
    else:
        raise ValueError("input must be a string or list")
    return messages


def _chat_request_from_response(payload):
    request_value = {
        "model": payload.get("model"),
        "messages": _responses_messages(payload),
    }
    text_format = (payload.get("text") or {}).get("format")
    if text_format:
        request_value["response_format"] = (
            {"type": "json_schema", "json_schema": text_format}
            if text_format.get("type") == "json_schema"
            else text_format
        )
    for key in ("temperature", "tools"):
        if payload.get(key) is not None:
            request_value[key] = payload[key]
    if payload.get("max_output_tokens") is not None:
        request_value["max_tokens"] = payload["max_output_tokens"]
    if payload.get("truncation") is not None:
        if payload["truncation"] not in {"auto", "disabled"}:
            raise ValueError("truncation must be auto or disabled")
        request_value["truncation"] = payload["truncation"]
    return request_value


def create_app(*, router, config, account_reader=None, device_logins=None, logout=None):
    app = Flask(__name__)

    def authorized():
        return hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {config.internal_token}")

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "mirofish-direct-oauth-gateway"}

    @app.get("/account")
    def account():
        if not authorized():
            return _error("unauthorized", 401)
        return account_reader() if account_reader else {"authenticated": False}

    @app.get("/v1/models")
    def models():
        if not authorized(): return _error("unauthorized", 401)
        return {"object": "list", "data": [
            {"id": model, "object": "model", "owned_by": "chatgpt-subscription"}
            for model in getattr(config, "models", (config.model,))
        ]}

    @app.post("/oauth/device/start")
    def oauth_start():
        if not authorized(): return _error("unauthorized", 401)
        return device_logins.start() if device_logins else _error("oauth unavailable", 503)

    @app.get("/oauth/device/<login_id>")
    def oauth_status(login_id):
        if not authorized(): return _error("unauthorized", 401)
        try: return device_logins.status(login_id)
        except KeyError: return _error("login not found", 404)

    @app.post("/oauth/device/<login_id>/cancel")
    def oauth_cancel(login_id):
        if not authorized(): return _error("unauthorized", 401)
        try: return device_logins.cancel(login_id)
        except KeyError: return _error("login not found", 404)

    @app.post("/oauth/logout")
    def oauth_logout():
        if not authorized(): return _error("unauthorized", 401)
        if logout: logout()
        return {"authenticated": False}

    @app.post("/v1/chat/completions")
    def completions():
        if not authorized():
            return _error("unauthorized", 401)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            return _error("messages must be a list", 400)
        if payload.get("stream") is True:
            return _error("stream=true is not supported", 400)
        try:
            result = router.complete(payload)
        except CircuitOpenError:
            return _error("provider circuit is open", 503)
        except ValueError as error:
            return _error(str(error), 400)
        except Exception as error:
            response = getattr(error, "response", None)
            app.logger.error("direct provider failed error_type=%s status_code=%s", type(error).__name__, getattr(error, "status_code", None) or getattr(response, "status_code", None))
            return _provider_error(error)
        tool_calls = [
            {
                "id": item.get("call_id") or item.get("id", ""),
                "type": "function",
                "function": {"name": item.get("name", ""), "arguments": item.get("arguments", "{}")},
            }
            for item in (result.output or [])
            if item.get("type") == "function_call"
        ]
        message = {"role": "assistant", "content": result.content or None}
        if tool_calls:
            message["tool_calls"] = tool_calls
        response = jsonify({"id": f"chatcmpl-{uuid.uuid4().hex}", "object": "chat.completion", "created": int(time.time()), "model": result.model, "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}], "usage": result.usage})
        response.headers["X-MiroFish-Provider"] = result.provider
        return response

    @app.post("/v1/responses")
    def responses():
        if not authorized():
            return _error("unauthorized", 401)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return _error("request body must be an object", 400)
        if payload.get("stream") is True:
            return _error("stream=true is not supported", 400)
        try:
            result = router.complete(_chat_request_from_response(payload))
        except CircuitOpenError:
            return _error("provider circuit is open", 503)
        except ValueError as error:
            return _error(str(error), 400)
        except Exception as error:
            response = getattr(error, "response", None)
            app.logger.error("direct provider failed error_type=%s status_code=%s", type(error).__name__, getattr(error, "status_code", None) or getattr(response, "status_code", None))
            return _provider_error(error)
        response_id = f"resp_{uuid.uuid4().hex}"
        output = result.output or [{
            "id": f"msg_{uuid.uuid4().hex}",
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": result.content, "annotations": []}],
        }]
        response = jsonify({
            "id": response_id,
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "model": result.model,
            "output": output,
            "usage": result.usage,
        })
        response.headers["X-MiroFish-Provider"] = result.provider
        return response

    return app
