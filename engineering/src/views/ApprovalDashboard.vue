<template>
  <div class="approval-dashboard-page">
    <div class="dashboard-header">
      <h2>{{ t('approvalDashboard.pageTitle') }}</h2>
      <div class="header-actions">
        <el-button
          :loading="loading"
          :icon="Refresh"
          @click="loadDashboard"
        >
          {{ t('approvalDashboard.btnRefresh') }}
        </el-button>
        <el-button
          type="primary"
          :icon="Document"
          @click="showReport = true"
        >
          {{ t('approvalDashboard.btnGovernanceReport') }}
        </el-button>
      </div>
    </div>

    <ApprovalStatsCards :counts="counts" />

    <el-tabs
      v-model="activeTab"
      class="approval-tabs"
    >
      <el-tab-pane
        :label="t('approvalDashboard.tabPending')"
        name="pending"
      >
        <ApprovalRequestList
          :items="pending"
          mode="pending"
          :loading="loading"
          :empty-text="t('approvalDashboard.emptyPending')"
          @view="viewDetail"
          @approve="quickApprove"
        />
      </el-tab-pane>

      <el-tab-pane
        :label="t('approvalDashboard.tabApproved')"
        name="approved"
      >
        <ApprovalRequestList
          :items="approved"
          mode="approved"
          :loading="loading"
          :empty-text="t('approvalDashboard.emptyApproved')"
          @view="viewDetail"
        />
      </el-tab-pane>

      <el-tab-pane
        :label="t('approvalDashboard.tabRejected')"
        name="rejected"
      >
        <ApprovalRequestList
          :items="rejected"
          mode="rejected"
          :loading="loading"
          :empty-text="t('approvalDashboard.emptyRejected')"
          @view="viewDetail"
        />
      </el-tab-pane>

      <el-tab-pane
        :label="t('approvalDashboard.tabHistory')"
        name="history"
      >
        <ApprovalTable
          :items="history"
          :loading="historyLoading"
          type="history"
          @view="viewDetail"
        />
      </el-tab-pane>
    </el-tabs>

    <ApprovalDetailDialog
      :visible="detailDialogVisible"
      :item="selectedRequest"
      @update:visible="detailDialogVisible = $event"
      @submit="handleDetailSubmit"
    />

    <ApprovalReportDialog
      :visible="showReport"
      :report-data="report"
      @update:visible="showReport = $event"
      @export="exportAuditLog"
    />
  </div>
</template>

<script setup lang="ts">
// TODO: 已拆分子组件 — ApprovalStatsCards, ApprovalTable, ApprovalDetailDialog, ApprovalReportDialog
import { ref, onMounted, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, Document } from '@element-plus/icons-vue'
import http from '@/utils/http'
import { triggerFileDownload } from '@/utils/download'
import { API_CONFIG, buildApiPath } from '@/config/api'
import { useI18n } from 'vue-i18n'

import ApprovalStatsCards from '@/components/approval/ApprovalStatsCards.vue'
import ApprovalTable from '@/components/approval/ApprovalTable.vue'
import ApprovalDetailDialog from '@/components/approval/ApprovalDetailDialog.vue'
import ApprovalReportDialog from '@/components/approval/ApprovalReportDialog.vue'
import ApprovalRequestList from '@/components/approval/ApprovalRequestList.vue'

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

interface DashboardData {
  pending: ApprovalRequest[]
  under_review: ApprovalRequest[]
  approved: ApprovalRequest[]
  rejected: ApprovalRequest[]
  counts: {
    pending: number
    under_review: number
    approved: number
    rejected: number
  }
}

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

const loading = ref(false)
const historyLoading = ref(false)
const activeTab = ref('pending')
const dashboard = ref<DashboardData>({
  pending: [],
  under_review: [],
  approved: [],
  rejected: [],
  counts: { pending: 0, under_review: 0, approved: 0, rejected: 0 },
})
const history = ref<ApprovalRequest[]>([])
const selectedRequest = ref<ApprovalRequest | null>(null)
const detailDialogVisible = ref(false)
const showReport = ref(false)
const reportLoading = ref(false)
const report = ref<GovernanceReport | null>(null)

const pending = computed(() => dashboard.value.pending)
const approved = computed(() => dashboard.value.approved)
const rejected = computed(() => dashboard.value.rejected)
const counts = computed(() => dashboard.value.counts)

async function loadDashboard() {
  loading.value = true
  try {
    const res = await http.get(buildApiPath(API_CONFIG.GOVERNANCE, '/approval-dashboard'))
    // 形状防护：接口返回空数组/异常形状时保持原状态（否则 dashboard.value 被替换
    // 为数组，pending/approved 等 computed 全部 undefined，模板渲染崩溃）
    const payload = res.data?.data
    if (payload && !Array.isArray(payload) && typeof payload === 'object') {
      dashboard.value = payload
    }
  } catch (e: unknown) {
    const errorMsg = e instanceof Error ? e.message : String(e)
    ElMessage.error(t('approvalDashboard.msgLoadDashboardFailed', { error: errorMsg }))
  } finally {
    loading.value = false
  }
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const res = await http.get(buildApiPath(API_CONFIG.GOVERNANCE, '/approval-requests'), {
      params: { limit: 100 },
    })
    history.value = res.data?.data || []
  } catch (e: unknown) {
    const errorMsg = e instanceof Error ? e.message : String(e)
    ElMessage.error(t('approvalDashboard.msgLoadHistoryFailed', { error: errorMsg }))
  } finally {
    historyLoading.value = false
  }
}

function viewDetail(request: ApprovalRequest) {
  selectedRequest.value = request
  detailDialogVisible.value = true
}

async function quickApprove(request: ApprovalRequest, decision: string) {
  try {
    const comment = decision === 'approved' ? t('approvalDashboard.msgQuickApprove') : decision === 'rejected' ? t('approvalDashboard.msgQuickReject') : t('approvalDashboard.msgQuickEscalate')
    await http.post(buildApiPath(API_CONFIG.GOVERNANCE, `/approval-requests/${request.request_id}/decide`), {
      approver_id: 'current_user',
      decision,
      comment,
    })
    ElMessage.success(t('approvalDashboard.msgApproveSuccess', { decision }))
    await loadDashboard()
  } catch (e: unknown) {
    const errorMsg = e instanceof Error ? e.message : String(e)
    ElMessage.error(t('approvalDashboard.msgApproveFailed', { error: errorMsg }))
  }
}

async function submitDecision(decision: string, comment: string) {
  if (!selectedRequest.value) return
  try {
    await http.post(buildApiPath(API_CONFIG.GOVERNANCE, `/approval-requests/${selectedRequest.value.request_id}/decide`), {
      approver_id: 'current_user',
      decision,
      comment,
    })
    ElMessage.success(t('approvalDashboard.msgSubmitDecisionSuccess', { decision }))
    detailDialogVisible.value = false
    await loadDashboard()
  } catch (e: unknown) {
    const errorMsg = e instanceof Error ? e.message : String(e)
    ElMessage.error(t('approvalDashboard.msgSubmitDecisionFailed', { error: errorMsg }))
  }
}

function handleDetailSubmit(payload: { decision: string; comment: string }) {
  submitDecision(payload.decision, payload.comment)
}

async function loadReport() {
  reportLoading.value = true
  try {
    const res = await http.get(buildApiPath(API_CONFIG.GOVERNANCE, '/reports/governance'), {
      params: { days: 30 },
    })
    report.value = res.data?.data || null
  } catch (e: unknown) {
    const errorMsg = e instanceof Error ? e.message : String(e)
    ElMessage.error(t('approvalDashboard.msgLoadReportFailed', { error: errorMsg }))
  } finally {
    reportLoading.value = false
  }
}

async function exportAuditLog() {
  try {
    const res = await http.get(buildApiPath(API_CONFIG.GOVERNANCE, '/audit-log/export'), {
      params: { format: 'csv' },
    })
    const blob = new Blob([res.data.data], { type: 'text/csv' })
    triggerFileDownload(blob, `audit_log_${Date.now()}.csv`)
    ElMessage.success(t('approvalDashboard.msgExportAuditLogSuccess'))
  } catch (e: unknown) {
    const errorMsg = e instanceof Error ? e.message : String(e)
    ElMessage.error(t('approvalDashboard.msgExportFailed', { error: errorMsg }))
  }
}

onMounted(() => {
  loadDashboard()
  loadHistory()
  loadReport()
})
</script>

<style scoped>
.approval-dashboard-page {
  padding: 16px;
  max-width: 1600px;
  margin: 0 auto;
}

.dashboard-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.dashboard-header h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.header-actions {
  display: flex;
  gap: 8px;
}

.approval-tabs {
  margin-top: 16px;
}

.tab-content {
  min-height: 200px;
  padding: 8px;
}
</style>