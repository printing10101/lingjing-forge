<template>
  <el-dialog :model-value="visible" :title="t('agentDetail.dialogSaveCheckpointTitle')" width="480px" @update:model-value="$emit('update:visible', $event)">
    <el-form label-position="top">
      <el-form-item :label="t('agentDetail.labelEpoch')">
        <el-input-number v-model="form.epoch" :min="0" />
      </el-form-item>
      <el-form-item :label="t('agentDetail.labelStep')">
        <el-input-number v-model="form.step" :min="0" />
      </el-form-item>
      <el-form-item :label="t('agentDetail.labelBestMetricValue')">
        <el-input v-model="form.best_metric" :placeholder="t('agentDetail.placeholderOptional')" />
      </el-form-item>
      <el-form-item :label="t('agentDetail.labelMetricName')">
        <el-input v-model="form.best_metric_name" :placeholder="t('agentDetail.placeholderMetricName')" />
      </el-form-item>
      <el-form-item :label="t('agentDetail.labelCheckpointType')">
        <el-select v-model="form.checkpoint_type">
          <el-option :label="t('agentDetail.optionManual')" value="manual" />
          <el-option :label="t('agentDetail.optionAuto')" value="auto" />
          <el-option :label="t('agentDetail.labelEpoch')" value="epoch" />
        </el-select>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="$emit('update:visible', false)">
        {{ t('agentDetail.btnCancel') }}
      </el-button>
      <el-button type="primary" @click="handleSave">
        {{ t('agentDetail.btnSave') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'save', data: {
    epoch: number
    step: number
    best_metric_name: string
    best_metric: string
    checkpoint_type: string
  }): void
}>()

interface CheckpointFormData {
  epoch: number
  step: number
  best_metric_name: string
  best_metric: string
  checkpoint_type: string
}

const form = reactive<CheckpointFormData>({
  epoch: 0,
  step: 0,
  best_metric_name: 'loss',
  best_metric: '',
  checkpoint_type: 'manual',
})

function handleSave() {
  emit('save', { ...form })
}
</script>