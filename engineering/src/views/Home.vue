<template>
  <div class="home-page">
    <!-- Page Header -->
    <div class="page-header">
      <div class="page-header__title">
        <h1>{{ t("home.pageTitle") }}</h1>
      </div>
    </div>

    <el-tabs v-model="activeTab" class="home-tabs">
      <!-- ===== Tab 1: 生产总览 ===== -->
      <el-tab-pane name="overview">
        <template #label>
          {{ t("home.tabOverview") }}
        </template>
        <div class="tab-body">
          <!-- 1. Welcome Banner -->
          <WelcomeBanner
            :greeting="greeting"
            :current-date-time="currentDateTime"
          />

          <!-- 2. Time Range Filter -->
          <TimeRangeFilter
            v-model:active-range="activeRange"
            :time-ranges="timeRanges"
          />

          <!-- 3. KPI Cards -->
          <KpiCards :kpi-cards="kpiCards" />

          <!-- 4. Two-column Layout -->
          <section class="content-row">
            <!-- Left: Production Progress Table -->
            <ProductionProgressTable
              :work-orders="displayWorkOrders"
              :error="!!tasksStore.error"
              @view-detail="handleViewOrderDetail"
            />

            <!-- Right: Real-time Alerts -->
            <RealTimeAlerts
              :alerts="alerts"
              :loading="alertsLoading"
              :error="alertsError"
            />
          </section>

          <!-- 5. Quick Actions -->
          <QuickActions
            :quick-actions="quickActions"
            @action-click="handleAction"
          />
        </div>
      </el-tab-pane>

      <!-- ===== Tab 2: 生产报表（原独立页并入；v-if 首次激活才挂载，避免首页多发请求） ===== -->
      <el-tab-pane name="report">
        <template #label>
          {{ t("home.tabReport") }}
        </template>
        <div v-if="visitedTabs.report" class="tab-body">
          <ProductionReportPanel />
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import {
  ref,
  reactive,
  computed,
  markRaw,
  onMounted,
  onUnmounted,
  watch,
} from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  Box,
  CircleCheck,
  Setting,
  Tickets,
  Plus,
  DataLine,
  Document,
  Monitor,
} from "@element-plus/icons-vue";
import http from "@/utils/http";
import { API_CONFIG } from "@/config/api";
import { useAgentStore } from "@/stores/agents";
import { useTasksStore } from "@/stores/tasks";

import WelcomeBanner from "@/components/home/WelcomeBanner.vue";
import TimeRangeFilter from "@/components/home/TimeRangeFilter.vue";
import KpiCards from "@/components/home/KpiCards.vue";
import type { KPICard } from "@/components/home/KpiCards.vue";
import ProductionProgressTable from "@/components/home/ProductionProgressTable.vue";
import type { WorkOrder } from "@/components/home/ProductionProgressTable.vue";
import RealTimeAlerts from "@/components/home/RealTimeAlerts.vue";
import type { AlertItem } from "@/components/home/RealTimeAlerts.vue";
import QuickActions from "@/components/home/QuickActions.vue";
import type { QuickAction } from "@/components/home/QuickActions.vue";
import ProductionReportPanel from "@/components/production/ProductionReportPanel.vue";

// Stores
const agentStore = useAgentStore();
const tasksStore = useTasksStore();
const router = useRouter();
const route = useRoute();
const { t } = useI18n();

/* ------------------------------------------------------------------ */
/*  Tab 切换：总览 / 生产报表（?tab=report 深链，兼容旧 /production-report） */
/* ------------------------------------------------------------------ */
const activeTab = ref(route.query.tab === "report" ? "report" : "overview");

// Tab 内容首次激活才挂载（生产与测试环境行为一致，不依赖 el-tabs 的 lazy）
const visitedTabs = reactive<Record<string, boolean>>({
  overview: true,
  report: false,
});
watch(activeTab, (tab) => {
  visitedTabs[tab] = true;
});

watch(
  () => route.query.tab,
  (tab) => {
    if (route.path === "/" && (tab === "report" || tab === "overview")) {
      activeTab.value = tab;
    }
  },
);

// Computed — 使用 Store 数据

const displayTasks = computed(() => tasksStore.tasks);

/** 将 tasksStore 数据映射为工单表格格式，并按 activeRange 时间范围过滤 */
const displayWorkOrders = computed<WorkOrder[]>(() => {
  const tasks = displayTasks.value;

  const statusMap: Record<string, string> = {
    pending: t("home.statusPending"),
    queued: t("home.statusQueued"),
    running: t("home.statusRunning"),
    completed: t("home.statusCompleted"),
    failed: t("home.statusFailed"),
    cancelled: t("home.statusCancelled"),
  };

  // 按时间范围过滤（today / week / month），数据来自任务 created_at
  const nowMs = Date.now();
  const rangeMs =
    activeRange.value === "today"
      ? 24 * 3600 * 1000
      : activeRange.value === "week"
        ? 7 * 24 * 3600 * 1000
        : 30 * 24 * 3600 * 1000;
  const inRange = (createdAt?: string) => {
    if (!createdAt) return true;
    const ts = new Date(createdAt).getTime();
    return Number.isFinite(ts) && nowMs - ts <= rangeMs;
  };

  return tasks
    .filter((task) => inRange(task.created_at))
    .map((task) => ({
      orderNo: task.job_id,
      productName: (task.params?.product as string) || task.task_type,
      process: (task.params?.process as string) || task.task_type,
      progress: task.progress ?? 0,
      status: task.status,
      statusLabel: statusMap[task.status] || task.status,
    }));
});

/** 查看工单详情：切换到"生产报表"Tab 查看完整工单列表。 */
function handleViewOrderDetail(_row: WorkOrder) {
  router.push({ path: "/", query: { tab: "report" } });
}

// Greeting & Clock
const now = ref(new Date());
let timer: ReturnType<typeof setInterval>;

// 告警 & KPI 响应式状态
const alerts = ref<AlertItem[]>([]);
const alertsLoading = ref(false);
const alertsError = ref(false);

interface ProductionDashboard {
  total_output: number;
  qualified_output: number;
  total_orders: number;
  active_orders: number;
  pass_rate: number;
  avg_cycle_time: number;
}

const productionData = ref<ProductionDashboard | null>(null);
const productionLoading = ref(false);
const productionError = ref(false);
/** 设备稼动率（%），来自生产按天统计接口最新一天的值 */
const productionUtilization = ref<number | null>(null);

onMounted(async () => {
  // 时钟
  timer = setInterval(() => {
    now.value = new Date();
  }, 1000);

  // 并行请求 5 个独立数据源，减少总等待时间
  alertsLoading.value = true;
  productionLoading.value = true;

  const [
    _agentsResult,
    _tasksResult,
    alertsResult,
    dashboardResult,
    statsResult,
  ] = await Promise.allSettled([
    agentStore.fetchAgents(),
    tasksStore.fetchTasks(),
    http.get(API_CONFIG.EQUIPMENT + "/alarms/"),
    http.get(API_CONFIG.PRODUCTION + "/dashboard/"),
    http.get(API_CONFIG.PRODUCTION + "/stats", { params: { days: 1 } }),
  ]);

  // 设备稼动率：取按天统计最新一条的 utilization（真实数据）
  if (statsResult.status === "fulfilled") {
    const list = statsResult.value.data?.data ?? [];
    if (Array.isArray(list) && list.length > 0) {
      const latest = list[list.length - 1];
      productionUtilization.value =
        typeof latest?.utilization === "number" ? latest.utilization : null;
    }
  }

  // 处理告警数据
  try {
    if (alertsResult.status === "fulfilled") {
      const list = alertsResult.value.data?.data ?? [];
      const severityColorMap: Record<string, string> = {
        high: "var(--error)",
        medium: "var(--warning)",
        low: "var(--info)",
      };
      alerts.value = list.map(
        (a: { message?: string; severity?: string; created_at?: string }) => ({
          message: a.message || t("home.msgUnknownAlert"),
          severityColor: severityColorMap[a.severity || "low"] || "var(--info)",
          time: a.created_at
            ? formatRelativeTime(a.created_at)
            : t("home.msgJustNow"),
        }),
      );
    }
  } catch {
    alertsError.value = true;
    alerts.value = [];
  } finally {
    alertsLoading.value = false;
  }

  // 处理生产仪表板数据
  try {
    if (dashboardResult.status === "fulfilled") {
      const data = dashboardResult.value.data?.data;
      if (data) productionData.value = data;
    }
  } catch {
    productionError.value = true;
    productionData.value = null;
  } finally {
    productionLoading.value = false;
  }
});

onUnmounted(() => {
  clearInterval(timer);
});

const greeting = computed(() => {
  const h = now.value.getHours();
  if (h < 6) return t("home.greetingEarlyMorning");
  if (h < 12) return t("home.greetingMorning");
  if (h < 14) return t("home.greetingNoon");
  if (h < 18) return t("home.greetingAfternoon");
  return t("home.greetingEvening");
});

const currentDateTime = computed(() => {
  const d = now.value;
  const pad = (n: number) => String(n).padStart(2, "0");
  const weekDays = [
    t("home.weekdaySun"),
    t("home.weekdayMon"),
    t("home.weekdayTue"),
    t("home.weekdayWed"),
    t("home.weekdayThu"),
    t("home.weekdayFri"),
    t("home.weekdaySat"),
  ];
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  return t("home.dateFormat", {
    year: d.getFullYear(),
    month: pad(d.getMonth() + 1),
    day: pad(d.getDate()),
    weekday: weekDays[d.getDay()],
    time,
  });
});

// Time Range Filter
const timeRanges = computed(() => [
  { key: "today", label: t("home.rangeToday") },
  { key: "week", label: t("home.rangeWeek") },
  { key: "month", label: t("home.rangeMonth") },
]);
const activeRange = ref("today");

// KPI Cards — 从生产仪表板 API 获取真实数据

const kpiCards = computed<KPICard[]>(() => {
  const tasks = displayTasks.value;
  const stats = tasksStore.stats;
  const pd = productionData.value;

  // 今日产量
  // 防御：pd 非空但 total_output 字段可能缺失（后端响应字段裁剪/旧版本），
  // 直接 .toLocaleString() 会抛 TypeError 导致整页 computed 崩溃（flaky 根因之一）
  const hasRealStats = stats && stats.total_tasks > 0;
  const productionCount =
    pd?.total_output != null
      ? pd.total_output.toLocaleString() + t("home.unitPiece")
      : hasRealStats
        ? stats.completed_tasks.toLocaleString() + t("home.unitPiece")
        : productionLoading.value
          ? t("home.loadingText")
          : "--";

  const productionChange =
    hasRealStats && stats.completed_tasks > 0
      ? `+${stats.completed_tasks}%`
      : "--";

  // 良品率
  const passRateValue =
    pd?.pass_rate != null
      ? (pd.pass_rate * 100).toFixed(1) + "%"
      : productionLoading.value
        ? t("home.loadingText")
        : "--";

  // 设备稼动率 — 来自 /production/stats 按天统计的真实利用率
  const utilizationValue =
    productionUtilization.value !== null
      ? productionUtilization.value.toFixed(1) + "%"
      : productionLoading.value
        ? t("home.loadingText")
        : "--";

  // 在制工单：running + pending
  const activeOrders = tasks.filter(
    (task) => task.status === "running" || task.status === "pending",
  ).length;
  const activeOrdersDisplay =
    hasRealStats || tasks.length > 0
      ? activeOrders + t("home.unitOrder")
      : "--";
  const activeOrdersChange = tasks.length > 0 ? String(activeOrders) : "--";

  return [
    {
      title: t("home.statTodayOutput"),
      value: productionCount,
      change: productionChange,
      isPositive: true,
      icon: markRaw(Box),
      color: "var(--brand-500)",
      iconBg: "var(--info-bg)",
    },
    {
      title: t("home.statPassRate"),
      value: passRateValue,
      change: "--",
      isPositive: true,
      icon: markRaw(CircleCheck),
      color: "var(--success)",
      iconBg: "var(--success-bg)",
    },
    {
      title: t("home.statUtilization"),
      value: utilizationValue,
      change: "--",
      isPositive: false,
      icon: markRaw(Setting),
      color: "var(--warning)",
      iconBg: "var(--warning-bg)",
    },
    {
      title: t("home.statActiveOrders"),
      value: activeOrdersDisplay,
      change: activeOrdersChange,
      isPositive: true,
      icon: markRaw(Tickets),
      color: "var(--info)",
      iconBg: "var(--info-bg)",
    },
  ];
});

// Helpers
function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const diff = Math.floor((Date.now() - date.getTime()) / 1000);
  if (diff < 60) return t("home.msgJustNow");
  if (diff < 3600)
    return t("home.timeMinutesAgo", { n: Math.floor(diff / 60) });
  if (diff < 86400)
    return t("home.timeHoursAgo", { n: Math.floor(diff / 3600) });
  return t("home.timeDaysAgo", { n: Math.floor(diff / 86400) });
}

// Quick Actions

const quickActions = computed<QuickAction[]>(() => [
  {
    label: t("home.btnNewOrder"),
    icon: markRaw(Plus),
    route: "/process-planning",
  },
  {
    label: t("home.btnStartInspection"),
    icon: markRaw(DataLine),
    route: "/quality-inspection",
  },
  {
    label: t("home.btnViewReport"),
    icon: markRaw(Document),
    route: "/production-report",
  },
  {
    label: t("home.btnEquipmentInspection"),
    icon: markRaw(Monitor),
    route: "/equipment-monitor",
  },
]);

function handleAction(action: QuickAction) {
  router.push(action.route);
}
</script>

<style scoped>
.home-page {
  max-width: var(--content-max-width);
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: var(--page-padding);
}

.tab-body {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding-top: 4px;
}

/* ===== Content Row (Two-column) ===== */
.content-row {
  display: grid;
  grid-template-columns: 3fr 2fr;
  gap: 16px;
}

@media (max-width: 900px) {
  .content-row {
    grid-template-columns: 1fr;
  }
}
</style>
