<template>
  <el-card v-loading="versionLoading" class="wm-detail-card">
    <template #header>
      <div class="wm-detail-card__header">
        <span>{{ t('worldModel.versionDetail') }}</span>
        <el-button
          v-if="currentVersion"
          link
          type="primary"
          @click="emit('use-active-version')"
        >
          {{ t('worldModel.useForPrediction') }}
        </el-button>
      </div>
    </template>
    <el-empty
      v-if="!versionLoading && !currentVersion"
      :description="t('worldModel.selectVersionHint')"
    />
    <el-descriptions
      v-else-if="currentVersion"
      :column="1"
      border
    >
      <el-descriptions-item :label="t('worldModel.fields.version')">
        {{ currentVersion.version }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('worldModel.fields.modelUri')">
        {{ currentVersion.model_uri }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('worldModel.fields.algorithm')">
        {{ currentVersion.description || '—' }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('worldModel.fields.trainingDataSize')">
        {{ currentVersion.training_data_size }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('worldModel.fields.predictionHorizon')">
        {{ currentVersion.prediction_horizon }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('worldModel.fields.createdAt')">
        {{ formatDateTime(currentVersion.created_at) }}
      </el-descriptions-item>
      <el-descriptions-item :label="t('worldModel.fields.isActive')">
        <el-tag :type="currentVersion.is_active ? 'success' : 'info'" size="small">
          {{ currentVersion.is_active ? t('common.yes') : t('common.no') }}
        </el-tag>
      </el-descriptions-item>
    </el-descriptions>
  </el-card>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { WorldModelVersion } from '@/contracts/world_model'

const { t } = useI18n()

defineProps<{
  currentVersion: WorldModelVersion | null
  versionLoading: boolean
}>()

const emit = defineEmits<{
  'use-active-version': []
}>()

function formatDateTime(iso: string): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('zh-CN')
  } catch {
    return iso
  }
}
</script>

<style scoped>
.wm-detail-card__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>