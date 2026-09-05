from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_provider_list_and_modal_support_editing():
    source = (ROOT / "frontend/src/views/ModelSettingsView.vue").read_text(
        encoding="utf-8"
    )

    assert '@click="openEditConnectionModal(item, $event)"' in source
    assert "const editingConnectionId = ref(null)" in source
    assert "updateModelConnection" in source
    assert "editingConnectionId ? '编辑连接' : '新增连接'" in source
    assert "api_key:''" in source
    assert "API Key 留空将保留现有密钥" in source


def test_non_chatgpt_provider_cannot_select_oauth_gateway_in_ui():
    source = (ROOT / "frontend/src/views/ModelSettingsView.vue").read_text(
        encoding="utf-8"
    )

    assert ':disabled="newConnection.vendor !== \'chatgpt_subscription\'"' in source
