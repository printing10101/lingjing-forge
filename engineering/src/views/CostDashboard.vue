<template>
  <div class="cost-dashboard">
    <!-- Page Header -->
    <div class="page-header">
      <div class="page-header__title">
        <h1>{{ t('costDashboard.pageTitle') }}</h1>
      </div>
    </div>

    <el-alert
      v-if="budgetExceeded"
      :title="t('costDashboard.alertBudgetExceededTitle')"
      type="error"
      :closable="false"
      show-icon
      style="margin-bottom: 16px"
    >
      <div>
        {{ t('costDashboard.alertBudgetExceededDesc') }}
      </div>
    </el-alert>

    <CostBudgetCards :budget-progresses="budgetProgresses" />

    <el-row :gutter="16">
      <el-col :span="12">
        <CostDistributionChart v-model:dimension="costDimension" />
      </el-col>
      <el-col :span="12">
        <CostByTypeChart />
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :span="24">
        <CostTrendChart v-model:days="trendDays" />
      </el-col>
    </el-row>

    <CostAlertsTable
      :alerts="alerts"
      :loading="loading.alerts"
      :has-unread="hasUnread"
      :alert-filter="alertFilter"
      @update:alert-filter="alertFilter = $event; loadAlerts()"
      @mark-read="markRead"
      @mark-all-read="markAllRead"
      @delete="deleteAlert"
      @refresh="loadAlerts"
    />

    <CostSuggestions
      :suggestions="suggestions"
      :loading="loading.suggestions"
      @refresh="loadSuggestions"
    />
  </div>
</template>

<script lang="ts" setup>
import { ref, onMounted, computed } from 'vue'
import CostBudgetCards from '@/components/cost/CostBudgetCards.vue'
import CostAlertsTable from '@/components/cost/CostAlertsTable.vue'
import CostSuggestions from '@/components/cost/CostSuggestions.vue'
import CostDistributionChart from '@/components/cost/CostDistributionChart.vue'
import CostByTypeChart from '@/components/cost/CostByTypeChart.vue'
import CostTrendChart from '@/components/cost/CostTrendChart.vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import http from '@/utils/http'
import { API_CONFIG, buildApiPath } from '@/config/api'

const { t } = useI18n()

// 类型定义
interface CostAlert {
  id: number
  created_at: number
  level: string
  scope_id: string
  resource_type: string
  usage_ratio: number
  status: 'warning' | 'exceeded'
  message: string
  is_read: 0 | 1
}

interface CostSuggestion {
  suggestion_id: string
  priority: 'high' | 'medium'
  category: string
  title: string
  description: string
  current_cost: number
  estimated_savings: number
  savings_percentage: number
  recommendation: string
}

const budgetProgresses = ref<BudgetProgress[]>([])
const alerts = ref<CostAlert[]>([])
const suggestions = ref<CostSuggestion[]>([])
const alertFilter = ref('')
const costDimension = ref('agent')
const trendDays = ref(7)

const loading = ref({
  alerts: false,
  suggestions: false,
})

const budgetExceeded = computed(() => alerts.value.some((a) => a.status === 'exceeded' && !a.is_read))
const hasUnread = computed(() => alerts.value.some((a) => !a.is_read))

async function loadBudgetProgress() {
  try {
    const res = await http.get(buildApiPath(API_CONFIG.COST_BUDGET, '/policies'))
    if (!res.data?.ok) return
    const policies = res.data.data || []

    budgetProgresses.value = policies.map((p: BudgetPolicyItem) => ({
      key: `${p.level}:${p.scope_id}:${p.resource_type}`,
      label: `${budgetLevelLabel(p.level)} · ${p.resource_type}`,
      percentage: Math.min(Math.round((p.usage_ratio || 0) * 100), 100),
      progressStatus: p.status === 'exceeded' ? 'exception' : p.status === 'warning' ? 'warning' : undefined,
      used: p.current_usage,
      limit: p.limit,
      usedStr: formatNumber(p.current_usage),
      limitStr: formatNumber(p.limit),
      status: p.status,
      tagType: statusTagType(p.status),
      statusLabel: formatStatusLabel(p.status),
    }))
  } catch (e: unknown) {
    console.warn('[CostDashboard] loadBudgetStatus failed:', e)
  }
}

async function loadAlerts() {
  loading.value.alerts = true
  try {
    const params: Record<string, string | number> = { limit: 100 }
    if (alertFilter.value) params.status = alertFilter.value
    const res = await http.get(buildApiPath(API_CONFIG.COST_BUDGET, '/alerts'), { params })
    if (!res.data?.ok) return
    alerts.value = res.data.data || []
  } catch (e: unknown) {
    console.warn('[CostDashboard] loadAlerts failed:', e)
  } finally {
    loading.value.alerts = false
  }
}

async function loadSuggestions() {
  loading.value.suggestions = true
  try {
    const res = await http.get(buildApiPath(API_CONFIG.COST_BUDGET, '/suggestions'))
    if (!res.data?.ok) return
    suggestions.value = res.data.data || []
  } catch (e: unknown) {
    console.warn('[CostDashboard] loadSuggestions failed:', e)
  } finally {
    loading.value.suggestions = false
  }
}

async function markRead(id: number) {
  try {
    await http.post(buildApiPath(API_CONFIG.COST_BUDGET, `/alerts/${id}/read`))
    const alert = alerts.value.find((a) => a.id === id)
    if (alert) alert.is_read = 1
  } catch (e: unknown) {
    console.warn('[CostDashboard] markRead failed:', e)
    ElMessage.error('标记已读失败，请稍后重试')
  }
}

async function markAllRead() {
  try {
    await http.post(buildApiPath(API_CONFIG.COST_BUDGET, '/alerts/read-all'))
    alerts.value.forEach((a) => (a.is_read = 1))
  } catch (e: unknown) {
    console.warn('[CostDashboard] markAllRead failed:', e)
    ElMessage.error('全部标记已读失败，请稍后重试')
  }
}

async function deleteAlert(id: number) {
  try {
    await http.delete(buildApiPath(API_CONFIG.COST_BUDGET, `/alerts/${id}`))
    alerts.value = alerts.value.filter((a) => a.id !== id)
  } catch (e: unknown) {
    console.warn('[CostDashboard] deleteAlert failed:', e)
    ElMessage.error('删除告警失败，请稍后重试')
  }
}

async function loadAll() {
  await Promise.all([
    loadBudgetProgress(),
    loadAlerts(),
    loadSuggestions(),
  ])
}

onMounted(() => {
  loadAll()
})

// 辅助函数
function budgetLevelLabel(level: string): string {
  const map: Record<string, string> = {
    global: t('costDashboard.levelGlobal'),
    project: t('costDashboard.levelProject'),
    agent: t('costDashboard.levelAgent'),
    task: t('costDashboard.levelTask'),
  }
  return map[level] || level
}

function statusTagType(status: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'ok') return 'success'
  if (status === 'warning') return 'warning'
  if (status === 'exceeded') return 'danger'
  return 'info'
}

function formatStatusLabel(status: string): string {
  const map: Record<string, string> = {
    ok: t('costDashboard.budgetStatusOk'),
    warning: t('costDashboard.budgetStatusWarning'),
    exceeded: t('costDashboard.budgetStatusExceeded'),
    disabled: t('costDashboard.budgetStatusDisabled'),
  }
  return map[status] || status
}

function formatNumber(n: number): string {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return n.toFixed(1)
}

// 内部类型
interface BudgetProgress {
  key: string
  label: string
  percentage: number
  progressStatus?: 'success' | 'warning' | 'exception'
  used: number
  limit: number
  usedStr: string
  limitStr: string
  status: string
  tagType: 'success' | 'warning' | 'danger' | 'info'
  statusLabel: string
}

interface BudgetPolicyItem {
  level: string
  scope_id: string
  resource_type: string
  usage_ratio: number
  status: string
  current_usage: number
  limit: number
}
</script>

<style scoped>
.cost-dashboard {
  padding: var(--page-padding);
  max-width: var(--content-max-width);
  margin: 0 auto;
}
</style>