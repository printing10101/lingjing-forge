<template>
  <div
    v-loading="loading"
    class="tab-content"
  >
    <el-table
      :data="items"
      stripe
    >
      <el-table-column
        prop="request_id"
        :label="t('approvalDashboard.colRequestId')"
        width="150"
      />
      <el-table-column
        prop="task_id"
        :label="t('approvalDashboard.colTaskId')"
        width="120"
      />
      <el-table-column
        prop="requester"
        :label="t('approvalDashboard.colRequester')"
        width="100"
      />
      <el-table-column
        prop="status"
        :label="t('approvalDashboard.colStatus')"
        width="100"
      >
        <template #default="{ row }">
          <el-tag
            :type="getApprovalStatusTagType(row.status)"
            size="small"
          >
            {{ getApprovalStatusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        prop="risk_score"
        :label="t('approvalDashboard.colRiskScore')"
        width="100"
      >
        <template #default="{ row }">
          {{ (row.risk_score ?? 0).toFixed(2) }}
        </template>
      </el-table-column>
      <el-table-column
        prop="requested_at"
        :label="t('approvalDashboard.colRequestedAt')"
        width="160"
      >
        <template #default="{ row }">
          {{ formatSecondsTimestamp(row.requested_at) }}
        </template>
      </el-table-column>
      <el-table-column
        :label="t('approvalDashboard.colActions')"
        fixed="right"
      >
        <template #default="{ row }">
          <el-button
            size="small"
            @click="$emit('view', row)"
          >
            {{ t('approvalDashboard.btnView') }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { formatSecondsTimestamp } from '@/utils/formatters'
import { getApprovalStatusTagType, getApprovalStatusLabel } from '@/utils/statusHelpers'

const { t } = useI18n()

interface ApprovalRequest {
  request_id: string
  task_id: string
  requester: string
  requested_at: number
  priority: string
  context: Record<string, unknown>
  status: string
  assigned_approver: string | null
  approvers: string[]
  decisions: Array<Record<string, unknown>>
  required_approvals: number
  risk_score: number
  risk_factors: string[]
  suggested_decision: string
  emergency_override: boolean
  emergency_reason: string
  expires_at: number | null
  completed_at: number | null
}

defineProps<{
  items: ApprovalRequest[]
  loading: boolean
  type: string
}>()

defineEmits<{
  view: [item: any]
  approve: [item: ApprovalRequest]
  reject: [item: ApprovalRequest]
  refresh: []
}>()
</script>

<style scoped>
.tab-content {
  min-height: 200px;
  padding: 8px;
}
</style>