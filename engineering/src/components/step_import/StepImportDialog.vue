<template>
  <el-dialog
    v-model="visible"
    :title="$t('stepImport.dialogTitle')"
    width="700px"
    :close-on-click-modal="false"
    @close="handleClose"
  >
    <el-tabs
      v-model="activeTab"
      @tab-change="onTabChange"
    >
      <el-tab-pane
        :label="$t('stepImport.importTab')"
        name="import"
      >
        <div class="step-import-container">
          <StepImportUpload
            v-if="!store.isSuccess && !store.isError"
            v-model:output-format="outputFormat"
            v-model:precision="precision"
            @file-change="handleFileChange"
            @file-remove="handleFileRemove"
          />

          <!-- 上传/处理进度 -->
          <div
            v-if="store.isActive"
            class="progress-section"
          >
            <div class="progress-status">
              <el-icon
                v-if="store.isUploading"
                class="is-loading"
              >
                <Loading />
              </el-icon>
              <span>{{ store.isUploading ? $t('stepImport.uploading') : $t('stepImport.processing') }}</span>
            </div>
            <el-progress
              :percentage="store.isUploading ? store.uploadProgress : undefined"
              :indeterminate="store.isProcessing"
              :status="store.isSuccess ? 'success' : store.isError ? 'exception' : undefined"
              :stroke-width="8"
            />
            <div
              v-if="store.isProcessing"
              class="progress-detail"
            >
              {{ $t('stepImport.processingDetail') }}
            </div>
          </div>

          <StepImportResult
            v-if="store.isSuccess && store.currentResult"
            v-model:entity-index="entityIndex"
            :current-result="store.currentResult"
            :model-info="store.modelInfo"
            :warnings="store.warnings"
            :has-stl-files="store.hasStlFiles"
            :active-stl-files="store.activeStlFiles"
            :errors="store.currentResult.status.errors"
            @update:entity-index="handleEntityChange"
          />

          <!-- 错误状态 -->
          <div
            v-if="store.isError"
            class="error-section"
          >
            <el-result
              icon="error"
              :title="$t('stepImport.importFailed')"
              :sub-title="store.errorMessage"
            >
              <template #extra>
                <el-button
                  type="primary"
                  @click="handleRetry"
                >
                  {{ $t('common.retry') }}
                </el-button>
              </template>
            </el-result>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane
        :label="$t('stepImport.historyTab')"
        name="history"
      >
        <StepImportHistory
          :history-loading="historyLoading"
          :import-history="store.importHistory"
          @view="handlePreviewHistory"
          @delete="handleDeleteHistory"
        />
      </el-tab-pane>
    </el-tabs>

    <template #footer>
      <div class="dialog-footer">
        <el-button
          v-if="!store.isSuccess && !store.isError && selectedFile && activeTab === 'import'"
          type="primary"
          :loading="store.isActive"
          :disabled="!selectedFile"
          @click="handleImport"
        >
          {{ $t('stepImport.startImport') }}
        </el-button>
        <el-button
          v-if="store.isSuccess && activeTab === 'import'"
          type="primary"
          @click="handleLoadInViewer"
        >
          {{ $t('stepImport.loadToViewer') }}
        </el-button>
        <el-button @click="handleClose">
          {{ store.isSuccess ? $t('common.done') : $t('common.cancel') }}
        </el-button>
      </div>
    </template>
  </el-dialog>

  <StepModelViewer
    v-model="showViewer"
    :model-url="viewerModelUrl"
    :model-name="viewerModelName"
    :face-count="viewerFaceCount"
    :vertex-count="viewerVertexCount"
    :file-size="viewerFileSize"
    @close="showViewer = false"
  />
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useStepImportStore } from '@/stores/stepImport'
import StepImportUpload from '@/components/step_import/StepImportUpload.vue'
import StepImportResult from '@/components/step_import/StepImportResult.vue'
import StepImportHistory from '@/components/step_import/StepImportHistory.vue'
import StepModelViewer from '@/components/step_import/StepModelViewer.vue'
import { Loading } from '@element-plus/icons-vue'
import type { ImportHistoryEntry } from '@/types'

const store = useStepImportStore()

const visible = computed({
  get: () => store.showDialog,
  set: (val: boolean) => { store.showDialog = val },
})

const selectedFile = ref<File | null>(null)
const outputFormat = ref('stl')
const precision = ref('medium')
const entityIndex = ref(0)
const activeTab = ref('import')
const historyLoading = ref(false)

const showViewer = ref(false)
const viewerModelUrl = ref('')
const viewerModelName = ref('')
const viewerFaceCount = ref(0)
const viewerVertexCount = ref(0)
const viewerFileSize = ref(0)

watch(() => store.showDialog, (val) => {
  if (val) {
    resetLocalState()
    store.fetchImportHistory()
  }
})

function resetLocalState() {
  selectedFile.value = null
  outputFormat.value = 'stl'
  precision.value = 'medium'
  entityIndex.value = 0
}

function handleFileChange(file: File) {
  selectedFile.value = file
}

function handleFileRemove() {
  selectedFile.value = null
}

async function handleImport() {
  if (!selectedFile.value) return
  await store.importStepFile(selectedFile.value, precision.value, outputFormat.value)
}

function handleEntityChange(index: number) {
  store.selectEntity(index)
}

function handleLoadInViewer() {
  viewerModelUrl.value = store.activeStlUrl
  viewerModelName.value = store.currentResult?.file_name ?? ''
  viewerFaceCount.value = store.modelInfo?.face_count ?? 0
  viewerVertexCount.value = store.modelInfo?.vertex_count ?? 0
  viewerFileSize.value = store.currentResult?.file_size ?? 0
  showViewer.value = true
}

function handleRetry() {
  store.reset()
  resetLocalState()
}

function handleClose() {
  visible.value = false
}

function onTabChange(tabName: string | number) {
  if (tabName === 'history') {
    historyLoading.value = true
    store.fetchImportHistory().finally(() => { historyLoading.value = false })
  }
}

function handlePreviewHistory(row: ImportHistoryEntry) {
  viewerModelUrl.value = row.stl_url
  viewerModelName.value = row.original_name
  viewerFaceCount.value = 0
  viewerVertexCount.value = 0
  viewerFileSize.value = row.file_size
  showViewer.value = true
}

async function handleDeleteHistory(row: ImportHistoryEntry) {
  await store.deleteHistoryFile(row.file_name)
}

</script>

<style scoped>
.step-import-container { min-height: 200px; }
.progress-section { padding: 24px 0; text-align: center; }
.progress-status { margin-bottom: 16px; font-size: 14px; color: var(--text-secondary); display: flex; align-items: center; justify-content: center; gap: 8px; }
.progress-detail { margin-top: 12px; font-size: 12px; color: var(--text-tertiary); }
.error-section { padding: 24px 0; }
.dialog-footer { display: flex; justify-content: flex-end; gap: 8px; }
</style>
