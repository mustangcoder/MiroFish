"""模型配置中心 API。"""

from dataclasses import asdict
from enum import Enum
import json
import http.client
import os
import urllib.error
import urllib.request

from flask import jsonify, request

from . import model_settings_bp
from ..services.model_config_service import ConnectionProtocolInUseError, ModelConfigService
from ..services.model_connection_tester import ModelConnectionTester
from ..services.model_discovery import ModelDiscovery
from ..services.draft_connection_tester import DraftConnectionTester
from ..services.memory_backend_config_service import MemoryBackendConfigService
from ..models.model_config import ModelRole
from ..services.provider_catalog import list_provider_specs, protocol_capability
from ..services.model_metadata import known_context_window


def _json(value):
    if isinstance(value, Enum): return value.value
    if isinstance(value, dict): return {str(k.value if isinstance(k, Enum) else k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_json(v) for v in value]
    return value


def _service():
    service = ModelConfigService()
    service.initialize_from_environment()
    return service


def _memory_service():
    service = MemoryBackendConfigService()
    service.initialize_from_environment()
    return service


@model_settings_bp.get('/metadata')
def model_metadata():
    model = request.args.get('model', '').strip()
    return jsonify({
        "success": True,
        "data": {
            "model": model,
            "context_window_tokens": known_context_window(model),
        },
    })


@model_settings_bp.get('/memory-backend')
def get_memory_backend():
    return jsonify({"success": True, "data": _memory_service().get_config()})


@model_settings_bp.put('/memory-backend')
def update_memory_backend():
    service = _memory_service()
    try:
        config = service.save_config(request.get_json() or {})
        service.apply_runtime_config()
        return jsonify({"success": True, "data": config})
    except ValueError as error:
        return jsonify({"success": False, "error": str(error)}), 400


@model_settings_bp.post('/memory-backend/test')
def test_memory_backend():
    try:
        result = _memory_service().test_connection(request.get_json() or {})
        return jsonify({"success": True, "data": result})
    except ValueError as error:
        return jsonify({"success": False, "error": str(error)}), 400
    except Exception as error:
        config = request.get_json() or {}
        message = "连接测试失败，请检查地址和凭据"
        if config.get("backend") == "graphiti" and "localhost" in config.get("neo4j_uri", ""):
            message = "Docker 部署中 localhost 指向应用容器，请改用 bolt://neo4j:7687"
        return jsonify({
            "success": False,
            "error": message,
            "error_code": type(error).__name__,
        }), 422


def _gateway_request(path, method='GET'):
    base_url = os.environ.get('DIRECT_OAUTH_GATEWAY_URL', 'http://chatgpt-oauth-gateway:8090')
    token = os.environ.get('DIRECT_GATEWAY_TOKEN', '')
    request_value = urllib.request.Request(base_url + path, data=b'{}' if method == 'POST' else None, method=method, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request_value, timeout=20) as response:
            return json.load(response), response.status
    except urllib.error.HTTPError as error:
        return json.load(error), error.code
    except (urllib.error.URLError, http.client.RemoteDisconnected, TimeoutError):
        return {"authenticated": False, "error": "OAuth Gateway 不可用"}, 503


@model_settings_bp.get('/connections')
def list_connections():
    return jsonify({"success": True, "data": [_json(asdict(item)) for item in _service().store.list_connections()]})


@model_settings_bp.get('/provider-catalog')
def provider_catalog():
    data = []
    for spec in list_provider_specs():
        data.append({
            "vendor": spec.vendor.value,
            "label": spec.label,
            "default_base_url": spec.default_base_url,
            "protocols": [protocol.value for protocol in spec.protocols],
            "default_protocol": spec.default_protocol.value,
            "default_auth_type": spec.default_auth_type.value,
            "capabilities": sorted({protocol_capability(protocol).value for protocol in spec.protocols}),
        })
    return jsonify({"success": True, "data": data})


@model_settings_bp.post('/connections')
def create_connection():
    data = request.get_json() or {}
    try:
        item = _service().create_connection(data)
        return jsonify({"success": True, "data": _json(asdict(item))}), 201
    except (KeyError, ValueError) as error:
        return jsonify({"success": False, "error": str(error)}), 400


@model_settings_bp.post('/connections/test-draft')
def test_draft_connection():
    data = request.get_json() or {}
    try:
        _service().validate_connection_data(data)
        result = DraftConnectionTester().test(data)
        success = result["status"] == "passed"
        return jsonify({
            "success": success,
            "data": result,
            "error": None if success else "连接测试失败",
        }), 200 if success else 422
    except (KeyError, ValueError) as error:
        return jsonify({"success": False, "error": str(error)}), 400


@model_settings_bp.patch('/connections/<connection_id>')
def update_connection(connection_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "请求体必须是对象"}), 400
    try:
        item = _service().update_connection(connection_id, data)
        return jsonify({"success": True, "data": _json(asdict(item))})
    except ConnectionProtocolInUseError as error:
        return jsonify({"success": False, "error": str(error)}), 409
    except KeyError:
        return jsonify({"success": False, "error": "Provider 连接不存在"}), 404
    except ValueError as error:
        return jsonify({"success": False, "error": str(error)}), 400


@model_settings_bp.delete('/connections/<connection_id>')
def delete_connection(connection_id):
    try:
        _service().store.delete_connection(connection_id)
        return jsonify({"success": True})
    except ValueError as error:
        return jsonify({"success": False, "error": str(error)}), 409


@model_settings_bp.route('/draft', methods=['GET', 'PUT'])
def draft():
    service = _service()
    if request.method == 'PUT':
        try: service.save_draft(request.get_json() or {})
        except ValueError as error: return jsonify({"success": False, "error": str(error)}), 400
    return jsonify({"success": True, "data": _json(service.store.get_draft())})


@model_settings_bp.post('/apply')
def apply():
    try: version = _service().apply_draft()
    except ValueError as error: return jsonify({"success": False, "error": str(error)}), 400
    return jsonify({"success": True, "data": _json(asdict(version))})


@model_settings_bp.post('/test')
def test_connection():
    connection_id = (request.get_json() or {}).get('connection_id')
    if not connection_id:
        return jsonify({"success": False, "error": "缺少 connection_id"}), 400
    result = ModelConnectionTester(_service().store).test(connection_id)
    return jsonify({"success": result["status"] == "passed", "data": result, "error": None if result["status"] == "passed" else "连接测试失败"}), 200 if result["status"] == "passed" else 422


@model_settings_bp.get('/connections/<connection_id>/models')
def connection_models(connection_id):
    try:
        service = _service()
        role = ModelRole(request.args.get('role', 'high_capability'))
        protocol = request.args.get('protocol')
        if not protocol:
            return jsonify({"success": False, "error": "缺少 protocol"}), 400
        connection = service.store.get_connection(connection_id)
        result = ModelDiscovery().list_models(connection, service.store.get_connection_secret(connection_id), role, protocol)
        return jsonify({"success": True, "data": result["models"], "manual_entry": result["manual_entry"]})
    except (KeyError, ValueError) as error:
        return jsonify({"success": False, "error": str(error)}), 400
    except Exception as error:
        return jsonify({"success": True, "data": [], "manual_entry": True, "warning_code": type(error).__name__})


@model_settings_bp.get('/active')
def active():
    value = _service().store.get_active_version()
    return jsonify({"success": True, "data": _json(asdict(value)) if value else None})


@model_settings_bp.get('/oauth/account')
def oauth_account():
    data, status = _gateway_request('/account')
    if status == 503:
        return jsonify({"success": True, "data": data}), 200
    return jsonify({"success": status < 400, "data": data}), status


@model_settings_bp.post('/oauth/device/start')
def oauth_device_start():
    data, status = _gateway_request('/oauth/device/start', 'POST')
    return jsonify({"success": status < 400, "data": data}), status


@model_settings_bp.get('/oauth/device/<login_id>')
def oauth_device_status(login_id):
    data, status = _gateway_request(f'/oauth/device/{login_id}')
    return jsonify({"success": status < 400, "data": data}), status


@model_settings_bp.post('/oauth/device/<login_id>/cancel')
def oauth_device_cancel(login_id):
    data, status = _gateway_request(f'/oauth/device/{login_id}/cancel', 'POST')
    return jsonify({"success": status < 400, "data": data}), status


@model_settings_bp.post('/oauth/logout')
def oauth_logout():
    data, status = _gateway_request('/oauth/logout', 'POST')
    return jsonify({"success": status < 400, "data": data}), status
