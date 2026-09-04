<template>
  <div class="library-page">
    <div ref="pageContent">
      <nav class="navbar" :aria-label="$t('fileLibrary.navigationLabel')">
      <router-link to="/" class="brand-link">MIROFISHPLUS</router-link>
      <div class="nav-actions">
        <router-link to="/" class="nav-link">{{ $t('fileLibrary.backHome') }} <span aria-hidden="true">→</span></router-link>
        <LanguageSwitcher />
      </div>
      </nav>

      <main class="library-main">
      <header class="page-header">
        <div>
          <p class="eyebrow">{{ $t('fileLibrary.eyebrow') }}</p>
          <h1>{{ $t('fileLibrary.title') }}</h1>
          <p class="subtitle">{{ $t('fileLibrary.subtitle') }}</p>
        </div>
        <div>
          <input
            ref="uploadInput"
            type="file"
            multiple
            accept=".pdf,.md,.markdown,.txt"
            style="display: none"
            @change="handleUpload"
          >
          <button ref="uploadButton" class="primary-button" :disabled="uploading" @click="uploadInput?.click()">
            {{ uploading ? $t('fileLibrary.uploading') : $t('fileLibrary.upload') }}
          </button>
        </div>
      </header>

      <form class="toolbar" role="search" @submit.prevent="handleSearch">
        <label for="file-search">{{ $t('fileLibrary.searchLabel') }}</label>
        <div class="search-row">
          <input
            id="file-search"
            v-model="queryDraft"
            type="search"
            :placeholder="$t('fileLibrary.searchPlaceholder')"
          >
          <button type="submit" class="secondary-button" :disabled="loading">
            {{ $t('fileLibrary.search') }}
          </button>
        </div>
      </form>

      <p v-if="error" class="feedback error" role="alert">
        {{ error }}
        <button type="button" class="inline-button" @click="loadFiles">{{ $t('fileLibrary.refreshList') }}</button>
      </p>
      <p v-if="successMessage" class="feedback success" aria-live="polite">{{ successMessage }}</p>

      <section class="file-panel" :aria-busy="loading">
        <div v-if="loading" class="empty-state">{{ $t('common.loading') }}</div>
        <div v-else-if="files.length === 0" class="empty-state">
          <strong>{{ $t('fileLibrary.emptyTitle') }}</strong>
          <span>{{ $t('fileLibrary.emptyHint') }}</span>
        </div>
        <div v-else class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{{ $t('fileLibrary.name') }}</th>
                <th>{{ $t('fileLibrary.type') }}</th>
                <th>{{ $t('fileLibrary.size') }}</th>
                <th>{{ $t('fileLibrary.uploadedAt') }}</th>
                <th>{{ $t('fileLibrary.referenceCount') }}</th>
                <th class="actions-heading">{{ $t('fileLibrary.actions') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="file in files" :key="file.file_id">
                <td class="file-name-cell">{{ file.display_name }}</td>
                <td><span class="type-badge">{{ file.extension.toUpperCase() }}</span></td>
                <td>{{ formatFileSize(file.size) }}</td>
                <td>{{ formatDate(file.created_at) }}</td>
                <td>
                  <button
                    type="button"
                    class="count-button"
                    :aria-label="$t('fileLibrary.viewReferenceCount', { name: file.display_name, count: file.reference_count })"
                    @click="showReferences(file)"
                  >
                    {{ file.reference_count }}
                  </button>
                </td>
                <td>
                  <div class="row-actions">
                    <button type="button" @click="requestRename(file)">{{ $t('fileLibrary.rename') }}</button>
                    <button type="button" @click="downloadFile(file)">{{ $t('fileLibrary.download') }}</button>
                    <button type="button" @click="showReferences(file)">{{ $t('fileLibrary.references') }}</button>
                    <button type="button" class="danger-button" @click="requestDelete(file)">{{ $t('fileLibrary.delete') }}</button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <footer class="pagination" :aria-label="$t('fileLibrary.paginationLabel')">
        <button type="button" class="secondary-button" :disabled="page === 0 || loading" @click="changePage(-1)">
          {{ $t('fileLibrary.previousPage') }}
        </button>
        <span>{{ $t('fileLibrary.page', { page: page + 1 }) }}</span>
        <button type="button" class="secondary-button" :disabled="!hasNextPage || loading" @click="changePage(1)">
          {{ $t('fileLibrary.nextPage') }}
        </button>
      </footer>
      </main>
    </div>

    <div v-if="renameTarget" class="modal-backdrop" @click.self="closeRename">
      <section ref="renameDialog" class="modal" role="dialog" aria-modal="true" aria-labelledby="rename-dialog-title" tabindex="-1">
        <h2 id="rename-dialog-title">{{ $t('fileLibrary.renameTitle') }}</h2>
        <label for="rename-input">{{ $t('fileLibrary.renameLabel') }}</label>
        <div class="rename-row">
          <input id="rename-input" ref="renameInput" v-model="renameBase" @keydown.enter.prevent="confirmRename">
          <span>.{{ renameTarget.extension }}</span>
        </div>
        <p v-if="modalError" class="feedback error" role="alert">{{ modalError }}</p>
        <div class="modal-actions">
          <button type="button" class="secondary-button" @click="closeRename">{{ $t('common.cancel') }}</button>
          <button type="button" class="primary-button" :disabled="actionLoading || !renameBase.trim()" @click="confirmRename">
            {{ $t('common.confirm') }}
          </button>
        </div>
      </section>
    </div>

    <div v-if="deleteTarget" class="modal-backdrop" @click.self="closeDelete">
      <section ref="deleteDialog" class="modal" role="dialog" aria-modal="true" aria-labelledby="delete-dialog-title" tabindex="-1">
        <h2 id="delete-dialog-title">{{ $t('fileLibrary.deleteTitle') }}</h2>
        <p>{{ $t('fileLibrary.deleteConfirm', { name: deleteTarget.display_name }) }}</p>
        <div class="modal-actions">
          <button ref="deleteInitialFocus" type="button" class="secondary-button" @click="closeDelete">{{ $t('common.cancel') }}</button>
          <button type="button" class="danger-confirm" :disabled="actionLoading" @click="confirmDelete">
            {{ $t('fileLibrary.delete') }}
          </button>
        </div>
      </section>
    </div>

    <div v-if="referencesOpen" class="modal-backdrop" @click.self="closeReferences">
      <section ref="referencesDialog" class="modal" role="dialog" aria-modal="true" aria-labelledby="references-dialog-title" tabindex="-1">
        <h2 id="references-dialog-title">{{ $t('fileLibrary.referencesTitle') }}</h2>
        <p v-if="referenceBlocked" class="feedback error" role="alert">{{ $t('fileLibrary.deleteBlocked') }}</p>
        <p v-if="referencesLoading">{{ $t('common.loading') }}</p>
        <p v-else-if="referencesError" class="feedback error" role="alert">
          {{ referencesError }}
          <button type="button" class="inline-button" @click="retryReferences">{{ $t('fileLibrary.retryReferences') }}</button>
        </p>
        <p v-else-if="referencesLoaded && referenceProjects.length === 0">{{ $t('fileLibrary.noReferences') }}</p>
        <ul v-else-if="referencesLoaded" class="reference-list">
          <li v-for="project in referenceProjects" :key="project.project_id">
            <strong>{{ project.project_name || project.project_id }}</strong>
            <span v-if="project.project_name">{{ project.project_id }}</span>
          </li>
        </ul>
        <div class="modal-actions">
          <button ref="referencesInitialFocus" type="button" class="primary-button" @click="closeReferences">{{ $t('common.close') }}</button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import LanguageSwitcher from '../components/LanguageSwitcher.vue'
import { useDialogFocus } from '../composables/useDialogFocus'
import {
  deleteUploadedFile,
  getUploadedFileReferences,
  listUploadedFiles,
  renameUploadedFile,
  uploadedFileDownloadUrl,
  uploadFiles
} from '../api/files'

const { locale, t } = useI18n()
const pageSize = 20
const files = ref([])
const queryDraft = ref('')
const activeQuery = ref('')
const page = ref(0)
const loading = ref(false)
const uploading = ref(false)
const actionLoading = ref(false)
const error = ref('')
const successMessage = ref('')
const pageContent = ref(null)
const uploadInput = ref(null)
const uploadButton = ref(null)
const renameInput = ref(null)
const renameDialog = ref(null)
const renameTarget = ref(null)
const renameBase = ref('')
const deleteDialog = ref(null)
const deleteInitialFocus = ref(null)
const deleteTarget = ref(null)
const modalError = ref('')
const referencesDialog = ref(null)
const referencesInitialFocus = ref(null)
const referencesOpen = ref(false)
const referencesLoading = ref(false)
const referenceBlocked = ref(false)
const referenceTarget = ref(null)
const referenceProjects = ref([])
const referencesError = ref('')
const referencesLoaded = ref(false)
let referenceRequestToken = 0

const hasNextPage = ref(false)
const { activateDialog, deactivateDialog } = useDialogFocus(() => pageContent.value)

async function loadFiles() {
  loading.value = true
  error.value = ''
  try {
    const response = await listUploadedFiles({
      query: activeQuery.value,
      limit: pageSize + 1,
      offset: page.value * pageSize
    })
    hasNextPage.value = response.data.length > pageSize
    files.value = response.data.slice(0, pageSize)
  } catch (requestError) {
    error.value = t('fileLibrary.loadFailed')
  } finally {
    loading.value = false
  }
}

async function handleSearch() {
  activeQuery.value = queryDraft.value.trim()
  page.value = 0
  await loadFiles()
}

async function handleUpload(event) {
  const selectedFiles = Array.from(event.target.files || [])
  if (selectedFiles.length === 0) return

  uploading.value = true
  error.value = ''
  successMessage.value = ''
  try {
    const formData = new FormData()
    selectedFiles.forEach(file => formData.append('files', file))
    await uploadFiles(formData)
    successMessage.value = t('fileLibrary.uploadSuccess', { count: selectedFiles.length })
    await loadFiles()
  } catch (requestError) {
    error.value = t('fileLibrary.uploadFailed')
  } finally {
    uploading.value = false
    event.target.value = ''
  }
}

function requestRename(file) {
  renameTarget.value = file
  renameBase.value = file.display_name.replace(new RegExp(`\\.${file.extension}$`, 'i'), '')
  modalError.value = ''
  activateDialog(() => renameDialog.value, () => renameInput.value, closeRename)
}

function closeRename() {
  if (actionLoading.value) return
  renameTarget.value = null
  modalError.value = ''
  deactivateDialog()
}

async function confirmRename() {
  const baseName = renameBase.value.trim()
  if (!baseName || !renameTarget.value) return

  actionLoading.value = true
  modalError.value = ''
  try {
    await renameUploadedFile(renameTarget.value.file_id, {
      display_name: `${baseName}.${renameTarget.value.extension}`
    })
    successMessage.value = t('fileLibrary.renameSuccess')
    await loadFiles()
    renameTarget.value = null
    await nextTick()
    deactivateDialog(() => uploadButton.value)
  } catch (requestError) {
    modalError.value = t('fileLibrary.renameFailed')
  } finally {
    actionLoading.value = false
  }
}

function downloadFile(file) {
  window.location.assign(uploadedFileDownloadUrl(file.file_id))
}

async function showReferences(file, blocked = false, suppliedReferences = null) {
  const requestToken = ++referenceRequestToken
  referenceTarget.value = file
  referencesOpen.value = true
  referenceBlocked.value = blocked
  referencesError.value = ''
  referencesLoaded.value = false
  referencesLoading.value = suppliedReferences === null
  referenceProjects.value = suppliedReferences || []
  activateDialog(
    () => referencesDialog.value,
    () => referencesInitialFocus.value,
    closeReferences
  )
  if (suppliedReferences !== null) {
    referencesLoading.value = false
    referencesLoaded.value = true
    return
  }

  try {
    const response = await getUploadedFileReferences(file.file_id)
    if (requestToken !== referenceRequestToken || referenceTarget.value?.file_id !== file.file_id) return
    referenceProjects.value = response.data
    referencesLoaded.value = true
  } catch (requestError) {
    if (requestToken !== referenceRequestToken || referenceTarget.value?.file_id !== file.file_id) return
    referenceProjects.value = []
    referencesError.value = t('fileLibrary.referencesFailed')
  } finally {
    if (requestToken === referenceRequestToken && referenceTarget.value?.file_id === file.file_id) {
      referencesLoading.value = false
    }
  }
}

async function retryReferences() {
  if (!referenceTarget.value) return
  await showReferences(referenceTarget.value, referenceBlocked.value)
}

function closeReferences() {
  referenceRequestToken += 1
  referencesOpen.value = false
  referencesLoading.value = false
  referenceBlocked.value = false
  referenceTarget.value = null
  referenceProjects.value = []
  referencesError.value = ''
  referencesLoaded.value = false
  deactivateDialog()
}

function requestDelete(file) {
  deleteTarget.value = file
  activateDialog(() => deleteDialog.value, () => deleteInitialFocus.value, closeDelete)
}

function closeDelete() {
  if (actionLoading.value) return
  deleteTarget.value = null
  deactivateDialog()
}

async function confirmDelete() {
  if (!deleteTarget.value) return
  const file = deleteTarget.value
  actionLoading.value = true
  error.value = ''
  try {
    await deleteUploadedFile(file.file_id)
    successMessage.value = t('fileLibrary.deleteSuccess')
    if (files.value.length === 1 && page.value > 0) page.value -= 1
    await loadFiles()
    deleteTarget.value = null
    await nextTick()
    deactivateDialog(() => uploadButton.value)
  } catch (requestError) {
    deleteTarget.value = null
    if (requestError.response?.status === 409) {
      const references = requestError.response?.data?.data?.references || []
      await showReferences(file, true, references)
    } else {
      deactivateDialog()
      error.value = t('fileLibrary.deleteFailed')
    }
  } finally {
    actionLoading.value = false
  }
}

async function changePage(direction) {
  page.value += direction
  await loadFiles()
}

function formatFileSize(size) {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(value) {
  if (!value) return '-'
  return new Intl.DateTimeFormat(locale.value, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  }).format(new Date(value))
}

onMounted(loadFiles)
</script>

<style scoped>
.library-page {
  min-height: 100vh;
  color: #111;
  background: #fff;
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
}

.navbar {
  min-height: 60px;
  padding: 0 40px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  background: #000;
}

.brand-link,
.nav-link {
  color: #fff;
  text-decoration: none;
  font-family: 'JetBrains Mono', monospace;
}

.brand-link {
  font-weight: 800;
  letter-spacing: 1px;
}

.nav-actions {
  display: flex;
  align-items: center;
  gap: 20px;
}

.brand-link:focus-visible,
.nav-link:focus-visible,
button:focus-visible,
input:focus-visible {
  outline: 2px solid #ff4500;
  outline-offset: 3px;
}

.library-main {
  width: min(1280px, calc(100% - 48px));
  margin: 0 auto;
  padding: 56px 0 72px;
}

.page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 32px;
  padding-bottom: 32px;
  border-bottom: 1px solid #e5e5e5;
}

.eyebrow {
  margin: 0 0 12px;
  color: #ff4500;
  font-family: 'JetBrains Mono', monospace;
  font-size: .78rem;
  font-weight: 700;
  letter-spacing: .12em;
  text-transform: uppercase;
}

h1 {
  margin: 0;
  font-size: clamp(2.2rem, 6vw, 4.5rem);
  font-weight: 520;
  letter-spacing: -.04em;
}

.subtitle {
  max-width: 680px;
  margin: 14px 0 0;
  color: #555;
  line-height: 1.65;
}

button,
input {
  font: inherit;
}

button {
  min-height: 44px;
  cursor: pointer;
}

button:disabled {
  cursor: not-allowed;
  opacity: .45;
}

.primary-button,
.secondary-button,
.danger-confirm {
  padding: 10px 18px;
  border: 1px solid #000;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
}

.primary-button {
  color: #fff;
  background: #000;
}

.primary-button:hover:not(:disabled) {
  border-color: #ff4500;
  background: #ff4500;
}

.secondary-button {
  color: #111;
  background: #fff;
}

.secondary-button:hover:not(:disabled) {
  border-color: #ff4500;
  color: #c73500;
}

.toolbar {
  margin: 32px 0 20px;
}

.toolbar label,
.modal label {
  display: block;
  margin-bottom: 8px;
  color: #555;
  font-family: 'JetBrains Mono', monospace;
  font-size: .78rem;
}

.search-row,
.rename-row {
  display: flex;
  align-items: stretch;
}

.search-row input,
.rename-row input {
  width: 100%;
  min-height: 44px;
  padding: 10px 12px;
  border: 1px solid #bbb;
  border-right: 0;
  background: #fafafa;
}

.search-row .secondary-button {
  flex: 0 0 auto;
}

.feedback {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 16px;
  border-left: 3px solid;
}

.feedback.error {
  color: #8b1a12;
  border-color: #c52b1a;
  background: #fff3f1;
}

.feedback.success {
  color: #185b37;
  border-color: #248a52;
  background: #f0faf4;
}

.inline-button {
  min-height: auto;
  padding: 0;
  border: 0;
  text-decoration: underline;
  color: inherit;
  background: transparent;
}

.file-panel {
  min-height: 280px;
  border: 1px solid #d5d5d5;
}

.table-scroll {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th,
td {
  padding: 16px;
  border-bottom: 1px solid #e8e8e8;
  text-align: left;
  vertical-align: middle;
}

th {
  color: #555;
  background: #fafafa;
  font-family: 'JetBrains Mono', monospace;
  font-size: .72rem;
  letter-spacing: .04em;
  white-space: nowrap;
}

.file-name-cell {
  min-width: 220px;
  font-weight: 650;
  overflow-wrap: anywhere;
}

.type-badge {
  display: inline-block;
  padding: 4px 7px;
  color: #8c2800;
  border: 1px solid #ffb79c;
  background: #fff3ee;
  font-family: 'JetBrains Mono', monospace;
  font-size: .7rem;
}

.count-button {
  min-width: 44px;
  border: 1px solid #ccc;
  background: #fff;
}

.row-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  min-width: 320px;
}

.row-actions button {
  padding: 8px 10px;
  border: 1px solid #ccc;
  background: #fff;
}

.row-actions button:hover {
  border-color: #111;
}

.row-actions .danger-button {
  color: #a32016;
  border-color: #df9f99;
}

.actions-heading {
  text-align: right;
}

.empty-state {
  min-height: 280px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 32px;
  color: #666;
  text-align: center;
}

.pagination {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
  margin-top: 20px;
  font-family: 'JetBrains Mono', monospace;
  font-size: .82rem;
}

.modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgb(0 0 0 / 55%);
}

.modal {
  width: min(520px, 100%);
  max-height: min(680px, calc(100vh - 48px));
  overflow-y: auto;
  padding: 28px;
  border-top: 4px solid #ff4500;
  background: #fff;
  box-shadow: 0 20px 60px rgb(0 0 0 / 24%);
}

.modal h2 {
  margin: 0 0 20px;
  font-size: 1.5rem;
}

.rename-row span {
  display: grid;
  place-items: center;
  padding: 0 12px;
  border: 1px solid #bbb;
  background: #f4f4f4;
  font-family: 'JetBrains Mono', monospace;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 28px;
}

.danger-confirm {
  color: #fff;
  border-color: #a32016;
  background: #a32016;
}

.reference-list {
  margin: 20px 0 0;
  padding: 0;
  list-style: none;
}

.reference-list li {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 0;
  border-bottom: 1px solid #e5e5e5;
}

.reference-list span {
  color: #666;
  font-family: 'JetBrains Mono', monospace;
  font-size: .74rem;
}

@media (max-width: 720px) {
  .navbar,
  .page-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .navbar {
    padding: 18px 24px;
  }

  .nav-actions {
    width: 100%;
    justify-content: space-between;
  }

  .library-main {
    width: min(100% - 32px, 1280px);
    padding-top: 36px;
  }

  .page-header .primary-button,
  .page-header > div:last-child {
    width: 100%;
  }

  .pagination {
    justify-content: space-between;
  }

  .modal-actions > button {
    flex: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    scroll-behavior: auto !important;
    transition-duration: .01ms !important;
    animation-duration: .01ms !important;
    animation-iteration-count: 1 !important;
  }
}
</style>
