<template>
  <el-dialog
    v-model="visible"
    :title="$t('toolpathImport.title')"
    width="650px"
    :close-on-click-modal="false"
  >
    <el-tabs v-model="importMode">
      <el-tab-pane
        :label="$t('toolpathImport.pasteTab')"
        name="paste"
      >
        <el-input
          v-model="gcodeText"
          type="textarea"
          :rows="14"
          :placeholder="$t('toolpathImport.pastePlaceholder')"
          style="font-family: var(--font-mono); font-size: 12px"
        />
      </el-tab-pane>
      <el-tab-pane
        :label="$t('toolpathImport.uploadTab')"
        name="upload"
      >
        <el-upload
          drag
          :auto-upload="false"
          :limit="1"
          accept=".nc,.h,.txt,.gcode,.ngc"
          :on-change="handleFileChange"
          :on-remove="handleFileRemove"
        >
          <el-icon class="el-icon--upload">
            <UploadFilled />
          </el-icon>
          <div class="el-upload__text">
            {{ $t('toolpathImport.dragOrClick') }} <em>{{ $t('toolpathImport.clickSelect') }}</em>
          </div>
          <template #tip>
            <div class="el-upload__tip">
              {{ $t('toolpathImport.fileTypeTip') }}
            </div>
          </template>
        </el-upload>
      </el-tab-pane>
    </el-tabs>

    <div
      v-if="previewLines.length > 0"
      class="preview-section"
    >
      <h4>{{ $t('toolpathImport.preview', { count: previewLines.length }) }}</h4>
      <div class="preview-content">
        <!-- 动态字符串列表，无业务唯一 id，index 作为 key 可接受 -->
        <div
          v-for="(line, i) in previewLines"
          :key="`line-${i}`"
          class="preview-line"
        >
          {{ line }}
        </div>
      </div>
    </div>

    <template #footer>
      <el-button @click="visible = false">
        {{ $t('common.cancel') }}
      </el-button>
      <el-button
        type="primary"
        :disabled="!gcodeText.trim()"
        @click="handleImport"
      >
        {{ $t('toolpathImport.import') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { UploadFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { useToolpathEditorStore } from './stores/toolpathEditor'

const visible = defineModel<boolean>('visible', { required: true })
const store = useToolpathEditorStore()

const importMode = ref('paste')
const gcodeText = ref('')
const fileRef = ref<File | null>(null)

const previewLines = computed(() => {
  return gcodeText.value
    .trim()
    .split('\n')
    .filter((l) => l.trim())
    .slice(0, 20)
})

function handleFileChange(file: UploadFile) {
  if (file.raw) {
    const reader = new FileReader()
    reader.onload = (e) => {
      gcodeText.value = e.target?.result as string
    }
    reader.readAsText(file.raw)
    fileRef.value = file.raw
  }
}

function handleFileRemove() {
  fileRef.value = null
  gcodeText.value = ''
}

function handleImport() {
  const result = store.loadGCode(gcodeText.value)
  if (result.success) {
    ElMessage.success(result.message)
    visible.value = false
  } else {
    ElMessage.error(result.message)
  }
}
</script>

<style lang="scss" scoped>
.preview-section {
  margin-top: 12px;
  h4 {
    margin: 0 0 8px;
    font-size: 13px;
    font-weight: 600;
    color: var(--text-secondary);
  }
}

.preview-content {
  max-height: 200px;
  overflow-y: auto;
  background: var(--bg-secondary);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-xs);
  padding: 8px;
  font-family: var(--font-mono);
  font-size: 11px;

  .preview-line {
    padding: 1px 0;
    white-space: pre;
    overflow-x: auto;
  }
}
</style>
