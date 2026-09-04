"""文件管理页前端静态契约测试。"""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_uploaded_file_api_exposes_all_management_operations_and_base_aware_download():
    source = _read("frontend/src/api/files.js")

    for export_name in (
        "listUploadedFiles",
        "uploadFiles",
        "renameUploadedFile",
        "deleteUploadedFile",
        "getUploadedFileReferences",
        "uploadedFileDownloadUrl",
    ):
        assert f"export function {export_name}" in source

    assert "service.defaults.baseURL" in source
    assert "/api/files/${encodeURIComponent(fileId)}/download" in source


def test_file_library_page_supports_every_management_action_and_reference_conflicts():
    source = _read("frontend/src/views/FileLibraryView.vue")

    for handler_name in (
        "loadFiles",
        "handleSearch",
        "handleUpload",
        "requestRename",
        "confirmRename",
        "downloadFile",
        "showReferences",
        "requestDelete",
        "confirmDelete",
    ):
        assert handler_name in source

    assert 'type="file"' in source
    assert 'style="display: none"' in source
    assert "uploadedFileDownloadUrl" in source
    assert ".response?.status === 409" in source
    assert ".response?.data?.data?.references" in source
    assert "referenceProjects" in source


def test_reference_modal_distinguishes_failures_from_successful_empty_results():
    source = _read("frontend/src/views/FileLibraryView.vue")

    assert "referencesError" in source
    assert "referencesLoaded" in source
    assert "referenceTarget" in source
    assert "retryReferences" in source
    assert "v-else-if=\"referencesError\"" in source
    assert "v-else-if=\"referencesLoaded && referenceProjects.length === 0\"" in source
    assert "t('fileLibrary.referencesFailed')" in source
    assert "requestError.message || t('fileLibrary.referencesFailed')" not in source
    assert "requestError.message || t(" not in source


def test_reference_requests_ignore_stale_responses_and_supplied_conflicts_clear_loading():
    source = _read("frontend/src/views/FileLibraryView.vue")

    assert "referenceRequestToken" in source
    assert "const requestToken = ++referenceRequestToken" in source
    assert "requestToken !== referenceRequestToken" in source
    assert "referenceTarget.value?.file_id !== file.file_id" in source
    assert "referenceRequestToken += 1" in source
    assert "referencesLoading.value = false" in source


def test_successful_mutations_refresh_before_restoring_focus_with_a_connected_fallback():
    view = _read("frontend/src/views/FileLibraryView.vue")
    composable = _read("frontend/src/composables/useDialogFocus.js")

    assert 'ref="uploadButton"' in view
    assert view.count("deactivateDialog(() => uploadButton.value)") == 2
    rename_success = view[view.index("async function confirmRename") : view.index("function downloadFile")]
    delete_success = view[view.index("async function confirmDelete") : view.index("async function changePage")]
    assert rename_success.index("await loadFiles()") < rename_success.index("deactivateDialog(() => uploadButton.value)")
    assert delete_success.index("await loadFiles()") < delete_success.index("deactivateDialog(() => uploadButton.value)")
    assert "restoreTarget?.isConnected" in composable
    assert "fallbackFocusGetter" in composable


def test_file_library_uses_committed_search_state_and_accurate_refresh_action():
    source = _read("frontend/src/views/FileLibraryView.vue")

    assert 'v-model="queryDraft"' in source
    assert "const activeQuery = ref('')" in source
    assert "activeQuery.value = queryDraft.value.trim()" in source
    assert "query: activeQuery.value" in source
    assert "$t('fileLibrary.refreshList')" in source
    assert "$t('common.retry')" not in source


def test_new_dialogs_share_a_complete_focus_lifecycle():
    composable = _read("frontend/src/composables/useDialogFocus.js")
    view = _read("frontend/src/views/FileLibraryView.vue")

    assert "document.addEventListener('keydown'" in composable
    assert "document.removeEventListener('keydown'" in composable
    assert "event.key === 'Escape'" in composable
    assert "event.key !== 'Tab'" in composable
    assert "event.shiftKey" in composable
    assert ".inert = true" in composable
    assert "setAttribute('aria-hidden', 'true')" in composable
    assert "previousActiveElement" in composable
    assert "?.focus()" in composable
    assert "useDialogFocus" in view
    assert "activateDialog" in view
    assert "deactivateDialog" in view
    assert 'ref="pageContent"' in view
    assert 'ref="renameDialog"' in view
    assert 'ref="deleteDialog"' in view
    assert 'ref="referencesDialog"' in view


def test_file_library_route_navigation_and_translations_are_registered():
    router = _read("frontend/src/router/index.js")
    home = _read("frontend/src/views/Home.vue")
    zh = json.loads(_read("locales/zh.json"))
    en = json.loads(_read("locales/en.json"))

    assert "import FileLibraryView from '../views/FileLibraryView.vue'" in router
    assert "path: '/files'" in router
    assert "component: FileLibraryView" in router
    assert 'to="/files"' in home
    assert "$t('nav.fileLibrary')" in home

    for messages in (zh, en):
        assert messages["nav"]["fileLibrary"]
        for key in (
            "title",
            "searchPlaceholder",
            "upload",
            "rename",
            "download",
            "references",
            "delete",
            "deleteBlocked",
            "refreshList",
            "retryReferences",
        ):
            assert messages["fileLibrary"][key]


def test_reference_count_button_has_a_localized_accessible_name():
    """若引用数按钮只有可见数字，按钮列表模式无法说明它会打开哪个文件的引用。"""
    source = _read("frontend/src/views/FileLibraryView.vue")
    zh = json.loads(_read("locales/zh.json"))
    en = json.loads(_read("locales/en.json"))

    assert ":aria-label=\"$t('fileLibrary.viewReferenceCount'" in source
    assert "name: file.display_name" in source
    assert "count: file.reference_count" in source
    assert zh["fileLibrary"]["viewReferenceCount"]
    assert en["fileLibrary"]["viewReferenceCount"]
