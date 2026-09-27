<template>
  <div
    v-loading="loading"
    class="arl-list"
  >
    <el-empty
      v-if="!items.length"
      :description="emptyText"
    />
    <el-card
      v-for="req in items"
      :key="req.request_id"
      class="arl-card"
      shadow="hover"
    >
      <div class="arl-header">
        <div class="arl-id">
          {{ req.request_id }}
        </div>
        <div class="arl-meta">
          <!-- pending 模式：优先级 + 风险标签 -->
          <template v-if="mode === 'pending'">
            <el-tag
              :type="getPriorityTagType(req.priority)"
              size="small"
            >
              {{ getPriorityLabel(req.priority) }}
            </el-tag>
            <el-tag
              :type="getRiskTagType(req.risk_score)"
              size="small"
            >
              {{ t('approvalDashboard.riskLabel', { score: req.risk_score.toFixed(2) }) }}
            </el-tag>
          </template>
          <!-- approved / rejected 模式：状态标签 -->
          <el-tag
            v-else
            :type="mode === 'approved' ? 'success' : 'danger'"
            size="small"
          >
            {{ mode === 'approved' ? t('approvalDashboard.statApproved') : t('approvalDashboard.statRejected') }}
          </el-tag>
        </div>
      </div>

      <div class="arl-body">
        <p><strong>{{ t('approvalDashboard.fieldTaskId') }}</strong> {{ req.task_id }}</p>
        <p><strong>{{ t('approvalDashboard.fieldRequester') }}</strong> {{ req.requester }}</p>
        <p v-if="mode === 'pending'">
          <strong>{{ t('approvalDashboard.fieldRequestedAt') }}</strong> {{ formatSecondsTimestamp(req.requested_at) }}
        </p>
        <p v-else>
          <strong>{{ t('approvalDashboard.fieldCompletedAt') }}</strong> {{ formatSecondsTimestamp(req.completed_at) }}
        </p>
        <p v-if="mode === 'pending' && req.suggested_decision">
          <strong>{{ t('approvalDashboard.fieldSuggestedDecision') }}</strong> {{ req.suggested_decision }}
        </p>
        <el-tag
          v-if="mode === 'pending' && req.emergency_override"
          type="danger"
          size="small"
        >
          {{ t('approvalDashboard.tagEmergencyOverride') }}
        </el-tag>
      </div>

      <!-- pending 模式：操作按钮 -->
      <div
        v-if="mode === 'pending'"
        class="arl-actions"
      >
        <el-button
          size="small"
          @click="emit('view', req)"
        >
          {{ t('approvalDashboard.btnDetail') }}
        </el-button>
        <el-button
          size="small"
          type="success"
          @click="emit('approve', req, 'approved')"
        >
          {{ t('approvalDashboard.btnApprove') }}
        </el-button>
        <el-button
          size="small"
          type="danger"
          @click="emit('approve', req, 'rejected')"
        >
          {{ t('approvalDashboard.btnReject') }}
        </el-button>
        <el-button
          size="small"
          type="warning"
          @click="emit('approve', req, 'escalated')"
        >
          {{ t('approvalDashboard.btnEscalate') }}
        </el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
/**
 * 审批请求卡片列表（ApprovalDashboard 拆分子组件）
 *
 * 统一渲染 pending（操作按钮）/ approved / rejected（只读）三种状态卡片，
 * 消除主组件中三份重复的 el-card 模板。
 */
import { useI18n } from 'vue-i18n'
import { getPriorityTagType, getPriorityLabel } from '@/utils/statusHelpers'
import { formatSecondsTimestamp } from '@/utils/formatters'

/** 列表模式：pending（可操作）/ approved / rejected（只读）。 */
type ListMode = 'pending' | 'approved' | 'rejected'

/** 审批请求（与主组件 ApprovalRequest 对齐的完整结构，卡片按需取字段）。 */
interface ApprovalListItem {
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
  /** 审批请求列表。 */
  items: ApprovalListItem[]
  /** 列表模式。 */
  mode: ListMode
  /** 加载中。 */
  loading: boolean
  /** 空状态文案。 */
  emptyText: string
}>()

const emit = defineEmits<{
  /** 查看详情。 */
  (e: 'view', req: ApprovalListItem): void
  /** 快速审批（pending 模式）。 */
  (e: 'approve', req: ApprovalListItem, decision: string): void
}>()

const { t } = useI18n()

/** 风险分数 → 标签类型。 */
function getRiskTagType(score: number): 'success' | 'warning' | 'danger' {
  if (score >= 0.7) return 'danger'
  if (score >= 0.4) return 'warning'
  return 'success'
}
</script>

<style scoped>
.arl-list {
  min-height: 120px;
}

.arl-card {
  margin-bottom: 12px;
}

.arl-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.arl-id {
  font-weight: 600;
  font-size: 14px;
}

.arl-meta {
  display: flex;
  gap: 6px;
  align-items: center;
}

.arl-body p {
  margin: 4px 0;
  font-size: 13px;
}

.arl-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
  justify-content: flex-end;
}
</style>
