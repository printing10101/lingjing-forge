<template>
  <el-card v-if="checkpoints.length > 0" shadow="hover" class="history-card">
    <template #header>
      <span>{{ t('agentDetail.sectionCheckpointHistory', { count: checkpoints.length }) }}</span>
    </template>
    <el-timeline>
      <el-timeline-item
        v-for="ckpt in checkpoints.slice(0, 10)"
        :key="ckpt.checkpoint_id"
        :timestamp="formatTime(ckpt.created_at)"
        placement="top"
      >
        <el-card shadow="hover" size="small">
          <div class="checkpoint-timeline-item">
            <el-tag size="small">{{ ckpt.checkpoint_type }}</el-tag>
            <span>{{ t('agentDetail.textEpochStep', { epoch: ckpt.epoch, step: ckpt.step }) }}</span>
            <span v-if="ckpt.best_metric !== null">
              {{ t('agentDetail.textBestMetricLabel', { name: ckpt.best_metric_name, value: ckpt.best_metric }) }}
            </span>
            <el-button size="small" type="warning" @click="$emit('rollback', ckpt.checkpoint_id)">
              {{ t('agentDetail.btnRollback') }}
            </el-button>
          </div>
        </el-card>
      </el-timeline-item>
    </el-timeline>
  </el-card>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useAgentStore } from '@/stores/agents'

import type { CheckpointInfo } from '@/stores/agents'

const { t } = useI18n()
const agentStore = useAgentStore()

defineProps<{
  checkpoints: CheckpointInfo[]
}>()

defineEmits<{
  (e: 'rollback', checkpointId: string): void
}>()

function formatTime(ts: string | number): string {
  return agentStore.formatTime(ts)
}
</script>

<style scoped>
.history-card {
  margin-bottom: 16px;
}

.checkpoint-timeline-item {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
</style>