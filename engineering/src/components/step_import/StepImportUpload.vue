<template>
  <div class="upload-section">
    <el-upload
      ref="uploadRef"
      class="step-uploader"
      drag
      :auto-upload="false"
      :limit="1"
      accept=".step,.stp"
      :on-change="handleFileChange"
      :on-remove="handleFileRemove"
      :file-list="fileList"
    >
      <el-icon class="el-icon--upload">
        <UploadFilled />
      </el-icon>
      <div class="el-upload__text">
        {{ $t('stepImport.uploadHint') }} <em>{{ $t('stepImport.uploadClick') }}</em>
      </div>
      <template #tip>
        <div class="el-upload__tip">
          {{ $t('stepImport.uploadTip') }}
        </div>
      </template>
    </el-upload>

    <div
      v-if="hasValidFile"
      class="import-options"
    >
      <el-form
        label-width="80px"
        size="small"
      >
        <el-form-item :label="$t('stepImport.outputFormat')">
          <el-radio-group
            :model-value="outputFormat"
            @change="(val: string | number | boolean | undefined) => $emit('update:outputFormat', val as string)"
          >
            <el-radio value="stl">
              {{ $t('stepImport.stlFormat') }}
            </el-radio>
            <el-radio value="brep">
              {{ $t('stepImport.brepFormat') }}
            </el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="$t('stepImport.precisionLevel')">
          <el-select
            :model-value="precision"
            style="width: 160px"
            @change="(val: string | number | boolean) => $emit('update:precision', val as string)"
          >
            <el-option
              :label="$t('stepImport.lowPrecision')"
              value="low"
            />
            <el-option
              :label="$t('stepImport.mediumPrecision')"
              value="medium"
            />
            <el-option
              :label="$t('stepImport.highPrecision')"
              value="high"
            />
          </el-select>
          <span class="precision-hint">
            {{ $t(precisionHint) }}
          </span>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { UploadFilled } from '@element-plus/icons-vue'
import type { UploadFile, UploadInstance } from 'element-plus'

const props = defineProps<{
  outputFormat: string
  precision: string
}>()

const emit = defineEmits<{
  'update:outputFormat': [value: string]
  'update:precision': [value: string]
  'fileChange': [file: File]
  'fileRemove': []
}>()

const uploadRef = ref<UploadInstance>()
const fileList = ref<UploadFile[]>([])
const hasValidFile = ref(false)

const precisionHint = computed(() => {
  switch (props.precision) {
    case 'low': return 'stepImport.lowPrecisionHint'
    case 'medium': return 'stepImport.mediumPrecisionHint'
    case 'high': return 'stepImport.highPrecisionHint'
    default: return ''
  }
})

function handleFileChange(file: UploadFile) {
  if (file.raw) {
    const ext = file.name.split('.').pop()?.toLowerCase()
    if (ext !== 'step' && ext !== 'stp') {
      fileList.value = []
      hasValidFile.value = false
      return
    }
    if (file.raw.size > 50 * 1024 * 1024) {
      fileList.value = []
      hasValidFile.value = false
      return
    }
    hasValidFile.value = true
    emit('fileChange', file.raw)
  }
  fileList.value = [file]
}

function handleFileRemove() {
  hasValidFile.value = false
  fileList.value = []
  emit('fileRemove')
}
</script>

<style scoped>
.step-uploader { width: 100%; }
.import-options { margin-top: 16px; padding: 12px; background: var(--bg-secondary); border-radius: var(--radius-sm); }
.precision-hint { margin-left: 12px; font-size: 12px; color: var(--text-tertiary); }
</style>