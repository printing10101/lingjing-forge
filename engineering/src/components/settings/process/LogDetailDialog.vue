<template>
  <el-dialog
    :model-value="visible"
    :title="$t('settings.logDetail')"
    width="60%"
    @update:model-value="$emit('update:visible', $event)"
  >
    <el-descriptions
      v-if="log"
      :column="1"
      border
    >
      <el-descriptions-item :label="$t('settings.timestamp')">
        {{ formatTimestamp(log.timestamp_ms as number) }}
      </el-descriptions-item>
      <el-descriptions-item :label="$t('settings.aiModuleCol')">
        {{ getModuleName(log.ai_module as string) }}
      </el-descriptions-item>
      <el-descriptions-item :label="$t('settings.userDecisionCol')">
        {{ getDecisionName(log.user_decision as string) }}
      </el-descriptions-item>
      <el-descriptions-item :label="$t('settings.opStatus')">
        {{ getStatusName(log.operation_status as string) }}
      </el-descriptions-item>
      <el-descriptions-item :label="$t('settings.confidence')">
        {{ (log.confidence as number) !== null ? `${((log.confidence as number) * 100).toFixed(2)}%` : 'N/A' }}
      </el-descriptions-item>
      <el-descriptions-item :label="$t('settings.aiRecommend')">
        <pre>{{ JSON.stringify(log.ai_recommendation, null, 2) }}</pre>
      </el-descriptions-item>
      <el-descriptions-item :label="$t('settings.finalExecution')">
        <pre>{{ JSON.stringify(log.final_execution, null, 2) }}</pre>
      </el-descriptions-item>
      <el-descriptions-item
        v-if="log.user_modifications"
        :label="$t('settings.userModifications')"
      >
        <pre>{{ JSON.stringify(log.user_modifications, null, 2) }}</pre>
      </el-descriptions-item>
      <el-descriptions-item :label="$t('settings.reasoningDesc')">
        {{ log.reasoning }}
      </el-descriptions-item>
    </el-descriptions>
  </el-dialog>
</template>

<script setup lang="ts">
import { formatTimestamp } from '@/utils/formatters'
import {
  getAuditModuleName as getModuleName,
  getAuditDecisionLabel as getDecisionName,
  getGenericStatusLabel as getStatusName,
} from '@/utils/statusHelpers'
import type { AuditLogEntry } from '@/composables/useAuditLog'

defineProps<{
  visible: boolean
  log: AuditLogEntry | null
}>()

defineEmits<{
  'update:visible': [value: boolean]
}>()
</script>

<style scoped>
pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
}
</style>