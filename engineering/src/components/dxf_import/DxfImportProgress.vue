<template>
  <div class="progress-section">
    <div class="progress-status">
      <el-icon class="is-loading">
        <Loading />
      </el-icon>
      <span>
        <template v-if="isUploading">
          {{ $t('dxfImportDialog.uploading') }}
        </template>
        <template v-else>
          {{ $t('dxfImportDialog.parsing') }}
        </template>
      </span>
      <span class="file-name-inline">{{ currentFileName }}</span>
    </div>

    <el-progress
      :percentage="overallProgress"
      :status="isError ? 'exception' : undefined"
      :stroke-width="10"
      striped
      striped-flow
    />

    <div class="progress-detail">
      <span v-if="isUploading">
        {{ $t('dxfImportDialog.uploadProgress', { pct: uploadProgress }) }}
      </span>
      <span v-else>
        {{ $t('dxfImportDialog.parseProgress', { pct: parseProgress }) }}
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Loading } from '@element-plus/icons-vue'

defineProps<{
  isUploading: boolean
  isError: boolean
  currentFileName: string
  overallProgress: number
  uploadProgress: number
  parseProgress: number
}>()
</script>

<style scoped>
.progress-section {
  padding: 32px 0;
}

.progress-status {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-size: 14px;
  color: var(--text-secondary);
  margin-bottom: 16px;
}

.file-name-inline {
  color: var(--text-tertiary);
  font-size: 12px;
  margin-left: 8px;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.progress-detail {
  margin-top: 12px;
  text-align: center;
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>