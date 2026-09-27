<template>
  <div class="upload-section">
    <div
      class="drop-zone"
      :class="{ 'is-dragover': isDragOver }"
      @click="triggerFilePicker"
      @dragenter.prevent="isDragOver = true"
      @dragover.prevent="isDragOver = true"
      @dragleave.prevent="isDragOver = false"
      @drop.prevent="onFileDrop"
    >
      <el-icon class="upload-icon">
        <UploadFilled />
      </el-icon>
      <div class="drop-text">
        <span class="primary-text">{{ $t('dxfImportDialog.uploadHint') }}</span>
        <span class="em-text">{{ $t('dxfImportDialog.uploadClick') }}</span>
      </div>
      <div class="drop-tip">
        {{ $t('dxfImportDialog.uploadTip') }}
      </div>
    </div>

    <input
      ref="fileInputRef"
      type="file"
      accept=".dxf"
      style="display: none;"
      @change="onFileInputChange"
    >

    <el-alert
      v-if="localFormatError"
      :title="localFormatError"
      type="error"
      :closable="false"
      show-icon
      style="margin-top: 12px;"
    />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { UploadFilled } from '@element-plus/icons-vue'

const emit = defineEmits<{
  (e: 'file-selected', file: File): void
}>()

const { t } = useI18n()

const isDragOver = ref(false)
const localFormatError = ref('')
const fileInputRef = ref<HTMLInputElement | null>(null)

function triggerFilePicker() {
  fileInputRef.value?.click()
}

function onFileInputChange(e: Event) {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  if (file) {
    handleFileSelected(file)
  }
  if (target) target.value = ''
}

function onFileDrop(e: DragEvent) {
  isDragOver.value = false
  const file = e.dataTransfer?.files?.[0]
  if (file) {
    handleFileSelected(file)
  }
}

function handleFileSelected(file: File) {
  localFormatError.value = ''

  const ext = (file.name.split('.').pop() || '').toLowerCase()
  if (ext !== 'dxf') {
    const msg = t('dxfImportDialog.invalidFormat')
    ElMessage.error(msg)
    localFormatError.value = msg
    return
  }

  if (file.size > 50 * 1024 * 1024) {
    ElMessage.warning(t('dxfImportDialog.largeFileWarning'))
  }

  emit('file-selected', file)
}
</script>

<style scoped>
.upload-section {
  padding: 24px 0;
}

.drop-zone {
  border: 2px dashed var(--border-light);
  border-radius: var(--radius-md);
  padding: 48px 16px;
  text-align: center;
  cursor: pointer;
  background: var(--bg-secondary);
  transition: all 0.2s ease;
  user-select: none;
}

.drop-zone:hover,
.drop-zone.is-dragover {
  border-color: var(--accent-primary);
  background: var(--bg-warm-tint);
}

.upload-icon {
  font-size: 48px;
  color: var(--accent-primary);
  margin-bottom: 12px;
}

.drop-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 14px;
  color: var(--text-secondary);
}

.drop-text .em-text {
  color: var(--accent-primary);
  font-style: normal;
}

.drop-tip {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>