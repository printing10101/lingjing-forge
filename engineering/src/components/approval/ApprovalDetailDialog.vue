<template>
  <el-dialog
    :model-value="visible"
    :title="t('approvalDashboard.detailDialogTitle')"
    width="800px"
    destroy-on-close
    @update:model-value="$emit('update:visible', $event)"
  >
    <div v-if="item" class="detail-content">
      <el-descriptions :column="2" border>
        <el-descriptions-item :label="t('approvalDashboard.detailRequestId')" :span="2">
          {{ item.request_id }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('approvalDashboard.detailTaskId')">
          {{ item.task_id }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('approvalDashboard.detailStatus')">
          <el-tag :type="getApprovalStatusTagType(item.status)">
            {{ getApprovalStatusLabel(item.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('approvalDashboard.detailRequester')">
          {{ item.requester }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('approvalDashboard.detailPriority')">
          <el-tag :type="getPriorityTagType(item.priority)">
            {{ getPriorityLabel(item.priority) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('approvalDashboard.detailRiskScore')">
          {{ item.risk_score.toFixed(2) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('approvalDashboard.detailSuggestedDecision')">
          {{ item.suggested_decision }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('approvalDashboard.detailRequestedAt')">
          {{ formatSecondsTimestamp(item.requested_at) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('approvalDashboard.detailExpiresAt')">
          {{ formatSecondsTimestamp(item.expires_at) }}
        </el-descriptions-item>
      </el-descriptions>

      <el-divider content-position="left">
        {{ t('approvalDashboard.detailOperationContext') }}
      </el-divider>
      <pre class="context-json">{{ formatContext(item.context) }}</pre>

      <el-divider v-if="item.decisions.length" content-position="left">
        {{ t('approvalDashboard.detailDecisionHistory') }}
      </el-divider>
      <el-table v-if="item.decisions.length" :data="item.decisions" size="small">
        <el-table-column prop="approver_id" :label="t('approvalDashboard.colApprover')" width="150" />
        <el-table-column prop="decision" :label="t('approvalDashboard.colDecision')" width="100">
          <template #default="{ row }">
            <el-tag
              :type="row.decision === 'approved' ? 'success' : row.decision === 'rejected' ? 'danger' : 'warning'"
              size="small"
            >
              {{ row.decision }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="comment" :label="t('approvalDashboard.colComment')" min-width="200" />
        <el-table-column prop="decided_at" :label="t('approvalDashboard.colDecidedAt')" width="160">
          <template #default="{ row }">
            {{ formatSecondsTimestamp(row.decided_at) }}
          </template>
        </el-table-column>
      </el-table>

      <el-divider content-position="left">
        {{ t('approvalDashboard.detailDecisionActions') }}
      </el-divider>
      <div class="decision-actions">
        <el-input
          v-model="comment"
          type="textarea"
          :rows="3"
          :placeholder="t('approvalDashboard.decisionCommentPlaceholder')"
          style="margin-bottom: 12px;"
        />
        <el-button type="success" @click="emitSubmit('approved')">
          {{ t('approvalDashboard.btnApprove') }}
        </el-button>
        <el-button type="danger" @click="emitSubmit('rejected')">
          {{ t('approvalDashboard.btnReject') }}
        </el-button>
        <el-button type="warning" @click="emitSubmit('request_info')">
          {{ t('approvalDashboard.btnRequestInfo') }}
        </el-button>
        <el-button type="info" @click="emitSubmit('escalated')">
          {{ t('approvalDashboard.btnEscalate') }}
        </el-button>
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatSecondsTimestamp } from '@/utils/formatters'
import { getPriorityTagType, getPriorityLabel, getApprovalStatusTagType, getApprovalStatusLabel } from '@/utils/statusHelpers'

const { t } = useI18n()

const comment = ref('')

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
  visible: boolean
  item: ApprovalRequest | null
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  submit: [payload: { decision: string; comment: string }]
}>()

function emitSubmit(decision: string) {
  emit('submit', { decision, comment: comment.value })
  comment.value = ''
}

function formatContext(context: Record<string, unknown>): string {
  try {
    return JSON.stringify(context, null, 2)
  } catch {
    return String(context)
  }
}
</script>

<style scoped>
.detail-content {
  max-height: 70vh;
  overflow-y: auto;
}

.context-json {
  background: var(--bg-secondary);
  padding: 12px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  max-height: 200px;
  overflow: auto;
  margin: 0;
}

.decision-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.decision-actions .el-button {
  align-self: flex-start;
}
</style>