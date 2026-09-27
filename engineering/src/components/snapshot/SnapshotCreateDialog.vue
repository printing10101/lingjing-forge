<template>
  <el-dialog
    :model-value="visible"
    :title="t('snapshotPanel.createDialogTitle')"
    width="640px"
    :close-on-click-modal="false"
    @update:model-value="(val: boolean) => emit('update:visible', val)"
  >
    <el-form
      ref="createFormRef"
      :model="createForm"
      label-width="140px"
      label-position="left"
    >
      <el-form-item :label="t('snapshotPanel.formConfig')">
        <el-input
          v-model="createForm.configStr"
          type="textarea"
          :rows="6"
          :placeholder="t('snapshotPanel.formConfigPlaceholder')"
        />
      </el-form-item>
      <el-form-item :label="t('snapshotPanel.formDatasetVersions')">
        <el-input
          v-model="createForm.datasetVersionsStr"
          type="textarea"
          :rows="3"
          :placeholder="t('snapshotPanel.formDatasetVersionsPlaceholder')"
        />
      </el-form-item>
      <el-form-item :label="t('snapshotPanel.formModelUri')">
        <el-input
          v-model="createForm.modelUri"
          :placeholder="t('snapshotPanel.formModelUriPlaceholder')"
        />
      </el-form-item>
      <el-form-item :label="t('snapshotPanel.formMetrics')">
        <el-input
          v-model="createForm.metricsStr"
          type="textarea"
          :rows="3"
          :placeholder="t('snapshotPanel.formMetricsPlaceholder')"
        />
      </el-form-item>
      <el-form-item :label="t('snapshotPanel.formCreatedBy')">
        <el-input
          v-model="createForm.createdBy"
          :placeholder="t('snapshotPanel.formCreatedByPlaceholder')"
        />
      </el-form-item>
      <el-form-item :label="t('snapshotPanel.formNotes')">
        <el-input
          v-model="createForm.notes"
          type="textarea"
          :rows="2"
          :placeholder="t('snapshotPanel.formNotesPlaceholder')"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button
        @click="emit('update:visible', false)"
      >
        {{ t('snapshotPanel.btnCancelDialog') }}
      </el-button>
      <el-button
        type="primary"
        :loading="creating"
        @click="handleConfirm"
      >
        {{ t('snapshotPanel.btnCreateConfirm') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import type { CreateSnapshotRequest } from '@/composables/useSnapshots'

const { t } = useI18n()

const props = defineProps<{
  visible: boolean
  creating: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', val: boolean): void
  (e: 'confirm', body: CreateSnapshotRequest): void
}>()

const createFormRef = ref()

interface CreateForm {
  configStr: string
  datasetVersionsStr: string
  modelUri: string
  metricsStr: string
  createdBy: string
  notes: string
}

const createForm = reactive<CreateForm>({
  configStr: '',
  datasetVersionsStr: '',
  modelUri: '',
  metricsStr: '',
  createdBy: '',
  notes: '',
})

function resetForm(): void {
  createForm.configStr = ''
  createForm.datasetVersionsStr = ''
  createForm.modelUri = ''
  createForm.metricsStr = '{}'
  createForm.createdBy = ''
  createForm.notes = ''
}

watch(() => props.visible, (val) => {
  if (val) resetForm()
})

async function handleConfirm(): Promise<void> {
  // 校验 config
  if (!createForm.configStr.trim()) {
    ElMessage.warning(t('snapshotPanel.msgConfigEmpty'))
    return
  }
  let config: Record<string, unknown>
  try {
    config = JSON.parse(createForm.configStr)
  } catch {
    ElMessage.warning(t('snapshotPanel.msgConfigInvalid'))
    return
  }

  // 校验 metrics（可选，默认 {}）
  let metrics: Record<string, number>
  try {
    metrics = JSON.parse(createForm.metricsStr || '{}')
  } catch {
    ElMessage.warning(t('snapshotPanel.msgMetricsInvalid'))
    return
  }

  // 解析 dataset_versions（每行一个 URI）
  const datasetVersions = createForm.datasetVersionsStr
    .split('\n')
    .map(s => s.trim())
    .filter(s => s.length > 0)
  if (datasetVersions.length === 0) {
    ElMessage.warning(t('snapshotPanel.msgDatasetVersionsEmpty'))
    return
  }

  const body: CreateSnapshotRequest = {
    config,
    dataset_versions: datasetVersions,
    model_uri: createForm.modelUri.trim() || 'model://unknown',
    metrics,
    created_by: createForm.createdBy.trim() || 'system:user',
    notes: createForm.notes.trim() || undefined,
  }

  emit('confirm', body)
}
</script>