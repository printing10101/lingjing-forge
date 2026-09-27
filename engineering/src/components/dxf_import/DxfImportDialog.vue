<!--
  DXF 文件导入对话框
  - 支持拖拽和点击两种方式选择 .dxf 文件
  - 上传进度可视化
  - 解析结果统计展示（线段、圆弧、圆、特征）
  - 内置 2D/3D 预览（基于 Three.js 在 XY 平面渲染几何）
  - 解析成功后可一键导入到当前工程
-->
<template>
  <el-dialog
    v-model="visible"
    :title="$t('dxfImportDialog.dialogTitle')"
    width="900px"
    top="4vh"
    :close-on-click-modal="false"
    destroy-on-close
    @close="handleClose"
  >
    <div class="dxf-import-container">
      <!-- 阶段 1：文件选择区 -->
      <DxfFileUpload
        v-if="store.isIdle || store.isError"
        @file-selected="handleFileSelected"
      />

      <!-- 阶段 2：上传/解析进度 -->
      <DxfImportProgress
        v-else-if="store.isActive"
        :is-uploading="store.isUploading"
        :is-error="store.isError"
        :current-file-name="store.currentFileName"
        :overall-progress="store.overallProgress"
        :upload-progress="store.uploadProgress"
        :parse-progress="store.parseProgress"
      />

      <!-- 阶段 3：解析成功 + 结果展示 -->
      <div
        v-else-if="store.isSuccess && store.parseResult"
        class="result-section"
      >
        <el-alert
          :title="$t('dxfImportDialog.importSuccess')"
          type="success"
          :closable="false"
          show-icon
        />

        <DxfParseStats
          :parse-result="store.parseResult"
          :features-count="featuresCount"
        />

        <DxfPreview :parse-result="store.parseResult" />

        <div
          v-if="store.parseResult.warnings && store.parseResult.warnings.length > 0"
          class="warning-section"
        >
          <el-alert
            v-for="(w, i) in store.parseResult.warnings"
            :key="`warn-${i}`"
            :title="w"
            type="warning"
            :closable="false"
            show-icon
            style="margin-bottom: 4px;"
          />
        </div>
      </div>
    </div>

    <template #footer>
      <DxfFooterActions
        :is-idle="store.isIdle"
        :is-error="store.isError"
        :is-active="store.isActive"
        :is-success="store.isSuccess"
        :importing="importing"
        @close="handleClose"
        @retry="handleRetry"
        @import-to-project="handleImportToProject"
      />
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { useDxfImportStore } from '@/stores/dxfImport'
import DxfFileUpload from './DxfFileUpload.vue'
import DxfImportProgress from './DxfImportProgress.vue'
import DxfParseStats from './DxfParseStats.vue'
import DxfPreview from './DxfPreview.vue'
import DxfFooterActions from './DxfFooterActions.vue'

const { t } = useI18n()

const store = useDxfImportStore()

const visible = computed({
  get: () => store.showDialog,
  set: (v: boolean) => {
    store.showDialog = v
  },
})

/** 正在导入到工程 */
const importing = ref(false)

/** 识别到的特征数量（孔+平面+其他） */
const featuresCount = computed(() => {
  const f = store.featureResult
  if (!f) return 0
  return (f.hole_count ?? 0) + (f.plane_count ?? 0)
})

// 文件选择

async function handleFileSelected(file: File) {
  const ok = await store.importDxfFile(file)
  if (!ok) {
    ElMessage.error(store.errorMessage || t('dxfImportDialog.dxfImportFailed'))
  }
}

// 业务操作

function handleRetry() {
  store.reset()
}

function handleClose() {
  store.closeDialog()
}

async function handleImportToProject() {
  importing.value = true
  try {
    const { useProjectStore } = await import('@/stores/project')
    const projectStore = useProjectStore()
    if (!projectStore.manifest) {
      ElMessage.error(t('dxfImportDialog.noOpenProject'))
      return
    }
    const parseResult = store.parseResult
    if (parseResult) {
      projectStore.manifest.resources.push({
        id: store.currentFileId || `dxf-${Date.now()}`,
        type: 'drawing',
        path: `dxf/${parseResult.file_name}`,
        original_name: parseResult.file_name,
        mime_type: 'application/dxf',
        added_at: new Date().toISOString(),
        metadata: {
          source: 'dxf-import',
          file_id: store.currentFileId,
          lines_count: parseResult.lines_count,
          arcs_count: parseResult.arcs_count,
          circles_count: parseResult.circles_count,
          features_count: featuresCount.value,
        },
      })
      projectStore.markModified?.()
    }
    ElMessage.success(t('dxfImportDialog.importToProjectSuccess'))
    store.closeDialog()
  } catch {
    ElMessage.error(t('dxfImportDialog.importToProjectFailed'))
  } finally {
    importing.value = false
  }
}
</script>

<style scoped>
.dxf-import-container {
  min-height: 320px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.result-section {
  max-height: 60vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.warning-section {
  margin-top: 4px;
}
</style>
