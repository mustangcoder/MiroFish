<template>
  <div class="picker-backdrop" @click.self="cancel">
    <section ref="pickerDialog" class="picker-dialog" role="dialog" aria-modal="true" aria-labelledby="picker-title" tabindex="-1">
      <header class="picker-header">
        <div>
          <p class="eyebrow">{{ $t('filePicker.eyebrow') }}</p>
          <h2 id="picker-title">{{ $t('filePicker.title') }}</h2>
        </div>
        <button type="button" class="close-button" :aria-label="$t('common.close')" @click="cancel">×</button>
      </header>

      <form class="search-form" role="search" @submit.prevent="handleSearch">
        <label for="library-picker-search">{{ $t('filePicker.searchLabel') }}</label>
        <div class="search-row">
          <input
            id="library-picker-search"
            ref="searchInput"
            v-model="searchQuery"
            type="search"
            :placeholder="$t('filePicker.searchPlaceholder')"
          >
          <button type="submit" :disabled="loading">{{ $t('filePicker.search') }}</button>
        </div>
      </form>

      <p v-if="error" class="feedback" role="alert">
        {{ error }}
        <button type="button" @click="loadFiles(false)">{{ $t('common.retry') }}</button>
      </p>

      <div class="selection-summary" aria-live="polite">
        {{ $t('filePicker.selectedCount', { count: draftIds.length }) }}
      </div>

      <div class="file-list" :aria-busy="loading">
        <p v-if="loading && availableFiles.length === 0" class="empty-state">{{ $t('common.loading') }}</p>
        <p v-else-if="availableFiles.length === 0" class="empty-state">{{ $t('filePicker.empty') }}</p>
        <template v-else>
          <label v-for="file in availableFiles" :key="file.file_id" class="file-option">
            <input v-model="draftIds" type="checkbox" :value="file.file_id">
            <span class="file-copy">
              <strong>{{ file.display_name }}</strong>
              <span>
                {{ formatFileSize(file.size) }} · {{ formatDate(file.created_at) }} ·
                {{ $t('filePicker.referenceCount', { count: file.reference_count }) }}
              </span>
            </span>
            <span class="extension">{{ file.extension.toUpperCase() }}</span>
          </label>
          <button v-if="hasMore" type="button" class="load-more-button" :disabled="loading" @click="loadMore">
            {{ loading ? $t('common.loading') : $t('filePicker.loadMore') }}
          </button>
        </template>
      </div>

      <footer class="picker-actions">
        <button type="button" class="secondary-button" @click="cancel">{{ $t('common.cancel') }}</button>
        <button type="button" class="primary-button" @click="confirm">
          {{ $t('filePicker.confirmSelection', { count: draftIds.length }) }}
        </button>
      </footer>
    </section>
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { listUploadedFiles } from '../api/files'
import { useDialogFocus } from '../composables/useDialogFocus'

const props = defineProps({
  modelValue: {
    type: Array,
    default: () => []
  },
  selectedFiles: {
    type: Array,
    default: () => []
  },
  backgroundElement: {
    type: Object,
    default: null
  }
})

const emit = defineEmits(['update:modelValue', 'confirm', 'cancel'])
const { locale, t } = useI18n()
const pageSize = 50
const searchQuery = ref('')
const activeSearch = ref('')
const searchInput = ref(null)
const pickerDialog = ref(null)
const availableFiles = ref([])
const knownFiles = new Map(props.selectedFiles.map(file => [file.file_id, file]))
const draftIds = ref(Array.from(new Set(props.modelValue)))
const loading = ref(false)
const error = ref('')
const offset = ref(0)
const hasMore = ref(false)
const { activateDialog, deactivateDialog } = useDialogFocus(() => props.backgroundElement)

watch(
  () => props.modelValue,
  value => {
    draftIds.value = Array.from(new Set(value))
  }
)

async function loadFiles(reset = false) {
  if (loading.value) return
  if (reset) {
    offset.value = 0
    availableFiles.value = []
    hasMore.value = false
  }
  loading.value = true
  error.value = ''
  try {
    const response = await listUploadedFiles({
      query: activeSearch.value,
      limit: pageSize + 1,
      offset: offset.value
    })
    const pageFiles = response.data.slice(0, pageSize)
    hasMore.value = response.data.length > pageSize
    availableFiles.value = [...availableFiles.value, ...pageFiles]
    offset.value += pageFiles.length
    pageFiles.forEach(file => knownFiles.set(file.file_id, file))
  } catch (requestError) {
    error.value = t('filePicker.loadFailed')
  } finally {
    loading.value = false
  }
}

async function handleSearch() {
  activeSearch.value = searchQuery.value.trim()
  await loadFiles(true)
}

async function loadMore() {
  await loadFiles()
}

function confirm() {
  const selectedIds = Array.from(new Set(draftIds.value))
  const selected = selectedIds.map(fileId => knownFiles.get(fileId)).filter(Boolean)
  deactivateDialog()
  emit('update:modelValue', selectedIds)
  emit('confirm', selected)
}

function cancel() {
  deactivateDialog()
  emit('cancel')
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
    day: 'numeric'
  }).format(new Date(value))
}

onMounted(async () => {
  activateDialog(() => pickerDialog.value, () => searchInput.value, cancel)
  await loadFiles(true)
})
</script>

<style scoped>
.picker-backdrop {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgb(0 0 0 / 55%);
}

.picker-dialog {
  width: min(720px, 100%);
  max-height: min(760px, calc(100vh - 48px));
  display: flex;
  flex-direction: column;
  border-top: 4px solid #ff4500;
  background: #fff;
  box-shadow: 0 20px 60px rgb(0 0 0 / 24%);
  font-family: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
}

.picker-header,
.picker-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 24px;
}

.picker-header {
  border-bottom: 1px solid #e5e5e5;
}

.picker-header h2,
.eyebrow {
  margin: 0;
}

.picker-header h2 {
  margin-top: 6px;
  font-size: 1.65rem;
}

.eyebrow {
  color: #c73500;
  font-family: 'JetBrains Mono', monospace;
  font-size: .72rem;
  font-weight: 700;
  letter-spacing: .12em;
  text-transform: uppercase;
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

button:focus-visible,
input:focus-visible {
  outline: 2px solid #ff4500;
  outline-offset: 3px;
}

.close-button {
  width: 44px;
  padding: 0;
  border: 1px solid #ccc;
  background: #fff;
  font-size: 1.5rem;
}

.search-form {
  padding: 20px 24px 0;
}

.search-form label {
  display: block;
  margin-bottom: 8px;
  color: #555;
  font-family: 'JetBrains Mono', monospace;
  font-size: .76rem;
}

.search-row {
  display: flex;
}

.search-row input {
  flex: 1;
  min-width: 0;
  min-height: 44px;
  padding: 10px 12px;
  border: 1px solid #bbb;
  border-right: 0;
  background: #fafafa;
}

.search-row button,
.secondary-button,
.primary-button {
  padding: 10px 18px;
  border: 1px solid #000;
  background: #fff;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
}

.primary-button {
  color: #fff;
  background: #000;
}

.primary-button:hover,
.search-row button:hover,
.secondary-button:hover {
  border-color: #ff4500;
}

.primary-button:hover {
  background: #ff4500;
}

.feedback {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin: 16px 24px 0;
  padding: 12px;
  color: #8b1a12;
  border-left: 3px solid #c52b1a;
  background: #fff3f1;
}

.feedback button {
  min-height: auto;
  padding: 0;
  border: 0;
  color: inherit;
  text-decoration: underline;
  background: transparent;
}

.selection-summary {
  padding: 16px 24px 8px;
  color: #666;
  font-family: 'JetBrains Mono', monospace;
  font-size: .76rem;
}

.file-list {
  min-height: 240px;
  overflow-y: auto;
  padding: 8px 24px 20px;
}

.file-option {
  display: grid;
  grid-template-columns: 24px minmax(0, 1fr) auto;
  align-items: center;
  gap: 14px;
  min-height: 68px;
  padding: 10px 12px;
  border-bottom: 1px solid #e5e5e5;
  cursor: pointer;
}

.file-option:hover {
  background: #fafafa;
}

.file-option input {
  width: 18px;
  height: 18px;
  accent-color: #ff4500;
}

.file-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.file-copy strong {
  overflow-wrap: anywhere;
}

.file-copy span {
  color: #666;
  font-size: .78rem;
}

.extension {
  padding: 4px 7px;
  color: #8c2800;
  border: 1px solid #ffb79c;
  background: #fff3ee;
  font-family: 'JetBrains Mono', monospace;
  font-size: .68rem;
}

.empty-state {
  min-height: 220px;
  display: grid;
  place-items: center;
  color: #666;
  text-align: center;
}

.load-more-button {
  width: 100%;
  margin-top: 12px;
  padding: 10px 18px;
  border: 1px solid #bbb;
  color: #111;
  background: #fff;
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
}

.load-more-button:hover:not(:disabled) {
  border-color: #ff4500;
  color: #c73500;
}

.picker-actions {
  justify-content: flex-end;
  border-top: 1px solid #e5e5e5;
}

@media (max-width: 560px) {
  .picker-backdrop {
    padding: 12px;
  }

  .picker-header,
  .picker-actions {
    padding: 18px;
  }

  .search-form {
    padding: 18px 18px 0;
  }

  .selection-summary,
  .file-list {
    padding-left: 18px;
    padding-right: 18px;
  }

  .picker-actions button {
    flex: 1;
  }
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    transition-duration: .01ms !important;
    animation-duration: .01ms !important;
    animation-iteration-count: 1 !important;
  }
}
</style>
