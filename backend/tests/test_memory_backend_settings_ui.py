from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_model_settings_page_exposes_memory_backend_configuration():
    view = (ROOT / "frontend/src/views/ModelSettingsView.vue").read_text()
    api = (ROOT / "frontend/src/api/modelSettings.js").read_text()
    router = (ROOT / "frontend/src/router/index.js").read_text()
    home = (ROOT / "frontend/src/views/Home.vue").read_text()

    assert "知识图谱服务" in view
    assert "选择知识图谱的存储与检索方式" in view
    assert "应用图谱配置" in view
    assert "记忆后端" not in view
    assert "Zep Cloud" in view
    assert "自定义 Neo4j" in view
    assert "testMemoryBackend" in view
    assert "saveMemoryBackend" in view
    assert "getMemoryBackend" in api
    assert "/api/settings/models/memory-backend/test" in api
    assert "path: '/settings'" in router
    assert "path: '/settings/models'" not in router
    assert 'to="/settings"' in home
    assert "await Promise.all(roles.map(role=>refreshModels(role.key)))" not in view
    assert ".memory-form label{align-content:start" in view
    assert "厂商或接入方式" in view
    assert "接口协议" in view
    assert "认证方式" in view
    assert "在线 OpenAI-compatible" not in view
    assert "本地文本模型" not in view
    assert "is_local" not in view


def test_provider_connection_is_created_from_accessible_modal():
    view = (ROOT / "frontend/src/views/ModelSettingsView.vue").read_text()

    assert 'class="add-connection-trigger"' in view
    assert 'role="dialog"' in view
    assert 'aria-modal="true"' in view
    assert 'aria-labelledby="connection-dialog-title"' in view
    assert '@keydown.esc="closeConnectionModal"' in view
    assert 'ref="connectionNameInput"' in view
    assert "connectionModalOpen.value=true" in view
    assert "connectionModalError.value" in view
    assert "新增连接失败" in view
    assert "正在提交…" in view


def test_managed_oauth_provider_locks_auth_and_base_url():
    view = (ROOT / "frontend/src/views/ModelSettingsView.vue").read_text()

    assert "const managedOAuthProvider = computed" in view
    assert ':disabled="managedOAuthProvider"' in view
    assert ':readonly="managedOAuthProvider"' in view
    assert "OAuth Gateway 地址由系统管理，不可修改" in view


def test_api_key_and_no_auth_connections_require_draft_test_before_creation():
    view = (ROOT / "frontend/src/views/ModelSettingsView.vue").read_text()
    api = (ROOT / "frontend/src/api/modelSettings.js").read_text()

    assert "testDraftModelConnection" in api
    assert "/api/settings/models/connections/test-draft" in api
    assert "connectionTestRequired" in view
    assert "testDraftConnection" in view
    assert "invalidateConnectionTest" in view
    assert "探测协议" in view
    assert "探测中…" in view
    assert "protocolSelectionReady" in view


def test_role_connection_change_clears_stale_model_before_loading_options():
    view = (ROOT / "frontend/src/views/ModelSettingsView.vue").read_text()

    assert '@change="selectRoleConnection(role.key)"' in view
    assert "draft[role].protocol=''" in view
    assert "draft[role].model=''" in view
    assert "selectRoleProtocol" in view
    assert "await refreshModels(role)" in view
