"""新推演复用文件库文件的前端静态契约测试。"""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path):
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_file_library_picker_supports_search_multiselect_and_deduplicated_confirmation():
    source = _read("frontend/src/components/FileLibraryPicker.vue")

    assert "listUploadedFiles" in source
    assert 'type="search"' in source
    assert 'type="checkbox"' in source
    assert "v-model=\"searchQuery\"" in source
    assert "draftIds" in source
    assert "new Set" in source
    assert "emit('update:modelValue'" in source
    assert "emit('confirm'" in source
    assert "reference_count" in source
    assert "formatFileSize" in source
    assert "formatDate" in source


def test_file_library_picker_pages_without_losing_cross_page_selections():
    source = _read("frontend/src/components/FileLibraryPicker.vue")

    assert "const pageSize" in source
    assert "const offset" in source
    assert "hasMore" in source
    assert "loadMore" in source
    assert "offset: offset.value" in source
    assert "availableFiles.value = [...availableFiles.value" in source
    assert "knownFiles.set" in source
    assert "draftIds.value" in source


def test_picker_establishes_focus_boundary_before_loading_and_localizes_errors():
    source = _read("frontend/src/components/FileLibraryPicker.vue")

    assert "useDialogFocus" in source
    assert 'ref="pickerDialog"' in source
    assert ':background-element="homeContent"' in _read("frontend/src/views/Home.vue")
    assert "activateDialog" in source
    mounted = source[source.index("onMounted(async () => {") :]
    assert mounted.index("activateDialog") < mounted.index("await loadFiles")
    assert "deactivateDialog" in source
    assert "t('filePicker.loadFailed')" in source
    assert "requestError.message || t('filePicker.loadFailed')" not in source
    assert '@click="loadFiles(false)"' in source
    assert '@click="loadFiles"' not in source


def test_home_combines_local_and_library_files_with_independent_removal():
    source = _read("frontend/src/views/Home.vue")

    assert "FileLibraryPicker" in source
    assert "selectedLibraryFiles" in source
    assert "selectedLibraryFileIds" in source
    assert "removeLocalFile" in source
    assert "removeLibraryFile" in source
    assert "files.value.length > 0 || selectedLibraryFileIds.value.length > 0" in source
    assert "Array.from(new Set" in source
    assert "setPendingUpload(" in source
    assert "selectedLibraryFileIds.value" in source


def test_pending_upload_persists_uploaded_file_ids_and_clears_all_sources():
    source = _read("frontend/src/store/pendingUpload.js")

    assert "uploadedFileIds: []" in source
    assert "setPendingUpload(files, requirement, uploadedFileIds" in source
    assert "state.uploadedFileIds = [...uploadedFileIds]" in source
    assert "uploadedFileIds: state.uploadedFileIds" in source
    assert "state.uploadedFileIds = []" in source


def test_both_project_creation_views_append_reused_and_local_files():
    for relative_path in (
        "frontend/src/views/MainView.vue",
        "frontend/src/views/Process.vue",
    ):
        source = _read(relative_path)

        assert "pending.files.length === 0 && pending.uploadedFileIds.length === 0" in source
        assert "pending.files.forEach" in source
        assert "formData" in source
        assert ".append('files'," in source
        assert "pending.uploadedFileIds.forEach" in source
        assert ".append('file_ids', fileId)" in source
        assert "clearPendingUpload()" in source


def test_home_rejects_an_invalid_local_batch_with_a_localized_alert():
    """若首页静默过滤非法文件，用户无法知道提交语料已不完整。"""
    source = _read("frontend/src/views/Home.vue")
    zh = json.loads(_read("locales/zh.json"))
    en = json.loads(_read("locales/en.json"))

    assert "fileValidationError" in source
    assert 'role="alert"' in source
    assert "t('home.invalidFileSelection'" in source
    assert "invalidFiles.length > 0" in source
    assert "files.value.push(...newFiles)" in source
    assert "files.value.push(...validFiles)" not in source
    assert zh["home"]["invalidFileSelection"]
    assert en["home"]["invalidFileSelection"]
