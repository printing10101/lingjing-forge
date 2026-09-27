<template>
  <el-dialog
    :model-value="visible"
    :title="t('approvalDashboard.reportDialogTitle')"
    width="900px"
    destroy-on-close
    @update:model-value="$emit('update:visible', $event)"
  >
    <div class="report-content">
      <template v-if="reportData">
        <el-descriptions :column="3" border>
          <el-descriptions-item :label="t('approvalDashboard.reportTotalRequests')">
            {{ reportData.total_requests }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('approvalDashboard.reportApprovedCount')">
            {{ reportData.approved_count }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('approvalDashboard.reportRejectedCount')">
            {{ reportData.rejected_count }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('approvalDashboard.reportEscalatedCount')">
            {{ reportData.escalated_count }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('approvalDashboard.reportEmergencyCount')">
            {{ reportData.emergency_count }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('approvalDashboard.reportAvgApprovalTime')">
            {{ reportData.avg_approval_time_hours.toFixed(2) }}h
          </el-descriptions-item>
          <el-descriptions-item :label="t('approvalDashboard.reportRejectionRate')">
            {{ (reportData.rejection_rate * 100).toFixed(2) }}%
          </el-descriptions-item>
          <el-descriptions-item :label="t('approvalDashboard.reportEscalationRate')">
            {{ (reportData.escalation_rate * 100).toFixed(2) }}%
          </el-descriptions-item>
        </el-descriptions>
        <el-button type="primary" style="margin-top: 16px;" @click="$emit('export')">
          {{ t('approvalDashboard.btnExportAuditLog') }}
        </el-button>
      </template>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

interface GovernanceReport {
  total_requests: number
  approved_count: number
  rejected_count: number
  escalated_count: number
  emergency_count: number
  avg_approval_time_hours: number
  rejection_rate: number
  escalation_rate: number
}

defineProps<{
  visible: boolean
  reportData: GovernanceReport | null
}>()

defineEmits<{
  'update:visible': [value: boolean]
  export: []
}>()
</script>

<style scoped>
.report-content {
  min-height: 200px;
}
</style>