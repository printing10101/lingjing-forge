<template>
  <div class="report-panel">
    <!-- 时间范围 + 导出 -->
    <div class="filter-bar">
      <el-radio-group
        v-model="timeRange"
        size="small"
        @change="handleTimeRangeChange"
      >
        <el-radio-button value="today">
          {{ t("productionReport.rangeToday") }}
        </el-radio-button>
        <el-radio-button value="week">
          {{ t("productionReport.rangeWeek") }}
        </el-radio-button>
        <el-radio-button value="month">
          {{ t("productionReport.rangeMonth") }}
        </el-radio-button>
        <el-radio-button value="custom">
          {{ t("productionReport.rangeCustom") }}
        </el-radio-button>
      </el-radio-group>
      <el-date-picker
        v-if="timeRange === 'custom'"
        v-model="customDateRange"
        type="daterange"
        :range-separator="t('productionReport.dateRangeSeparator')"
        :start-placeholder="t('productionReport.startDatePlaceholder')"
        :end-placeholder="t('productionReport.endDatePlaceholder')"
        size="small"
        @change="handleTimeRangeChange"
      />
      <el-button
        type="primary"
        size="small"
        :icon="Download"
        class="export-btn"
        @click="handleExport"
      >
        {{ t("productionReport.btnExport") }}
      </el-button>
    </div>

    <!-- 生产汇总卡片 -->
    <ProductionSummaryCards
      :summary-cards="summaryCards"
      :loading="dashboardLoading"
    />

    <!-- 图表区域 + 趋势明细表 -->
    <ProductionTrendChart
      v-model:chart-type="chartType"
      :stats-loading="statsLoading"
      :trend-data="trendData"
    />

    <!-- 最新生产记录 -->
    <ProductionRecordsTable
      :records="productionRecords"
      :loading="recordsLoading"
    />

    <!-- 工单列表 -->
    <ProductionWorkOrdersTable
      :work-orders="workOrders"
      :loading="workOrdersLoading"
    />
  </div>
</template>

<script setup lang="ts">
// 原独立页面 /production-report（已并入首页"生产报表"Tab）。
// 数据源与首页 KPI 同源（/production/dashboard、/production/stats），
// 故随 Tab 懒加载，避免首页首屏多发请求。
import { ref, computed, onMounted, onUnmounted, type Component } from "vue";
import { ElMessage } from "element-plus";
import { Download, Top, Bottom, Right } from "@element-plus/icons-vue";
import { useI18n } from "vue-i18n";
import http from "@/utils/http";
import { API_CONFIG } from "@/config/api";
import ProductionSummaryCards from "@/components/production/ProductionSummaryCards.vue";
import ProductionTrendChart from "@/components/production/ProductionTrendChart.vue";
import ProductionRecordsTable from "@/components/production/ProductionRecordsTable.vue";
import ProductionWorkOrdersTable from "@/components/production/ProductionWorkOrdersTable.vue";

const { t } = useI18n();

// 类型定义
interface SummaryCard {
  label: string;
  value: string;
  unit: string;
  trendIcon: Component;
  trendClass: string;
}

interface DashboardData {
  total_output: number;
  qualified_output: number;
  total_orders: number;
  active_orders: number;
  pass_rate: number;
  avg_cycle_time: number;
}

interface StatsItem {
  date: string;
  plan_output: number;
  actual_output: number;
  yield_rate: number;
  utilization: number;
  achievement_rate: number;
}

interface TrendRow {
  date: string;
  planOutput: number;
  actualOutput: number;
  yieldRate: string;
  utilization: string;
  achievementRate: number;
}

interface ProductionRecord {
  id: number;
  order_no: string;
  product_name: string;
  quantity: number;
  qualified_quantity: number;
  operator: string;
  status: string;
  created_at: string;
}

interface WorkOrder {
  id: number;
  order_no: string;
  product_name: string;
  quantity: number;
  status: string;
  priority: string;
  deadline: string;
  created_at: string;
}

// 状态
const loading = ref(false);
const dashboardLoading = ref(false);
const statsLoading = ref(false);
const recordsLoading = ref(false);
const workOrdersLoading = ref(false);
const timeRange = ref("month");
const customDateRange = ref<[Date, Date] | null>(null);
const chartType = ref("bar");

const dashboardData = ref<DashboardData | null>(null);
const trendData = ref<TrendRow[]>([]);
const productionRecords = ref<ProductionRecord[]>([]);
const workOrders = ref<WorkOrder[]>([]);

/** 时间范围映射为天数 */
function timeRangeToDays(range: string): number {
  switch (range) {
    case "today":
      return 1;
    case "week":
      return 7;
    case "month":
      return 30;
    default:
      return 30;
  }
}

/** 自定义日期范围映射为天数 */
function customRangeToDays(range: [Date, Date] | null): number | undefined {
  if (!range) return undefined;
  const diffMs = range[1].getTime() - range[0].getTime();
  return Math.max(1, Math.ceil(diffMs / (1000 * 60 * 60 * 24)));
}

const summaryCards = computed<SummaryCard[]>(() => {
  const d = dashboardData.value;
  if (!d) return [];

  return [
    {
      label: t("productionReport.cardTotalOutput"),
      value: d.total_output.toLocaleString(),
      unit: t("productionReport.unitPiece"),
      trendIcon: Top,
      trendClass: "trend-up",
    },
    {
      label: t("productionReport.cardPassRate"),
      value: d.pass_rate.toFixed(1),
      unit: "%",
      trendIcon: d.pass_rate >= 98 ? Top : Bottom,
      trendClass: d.pass_rate >= 98 ? "trend-up" : "trend-down",
    },
    {
      label: t("productionReport.cardActiveOrders"),
      value: d.active_orders.toString(),
      unit: t("productionReport.unitOrder"),
      trendIcon: Right,
      trendClass: "trend-stable",
    },
    {
      label: t("productionReport.cardAvgCycle"),
      value: d.avg_cycle_time.toFixed(0),
      unit: t("productionReport.unitMinutes"),
      trendIcon: Right,
      trendClass: "trend-stable",
    },
  ];
});

// 请求取消控制
let currentAbortController: AbortController | null = null;

function cancelPendingRequests() {
  if (currentAbortController) {
    currentAbortController.abort();
    currentAbortController = null;
  }
}

// API 调用
async function fetchDashboard(signal?: AbortSignal) {
  dashboardLoading.value = true;
  try {
    const res = await http.get(API_CONFIG.PRODUCTION + "/dashboard/", {
      signal,
    });
    dashboardData.value = res.data?.data ?? null;
  } catch (error) {
    if ((error as { name?: string })?.name === "AbortError") return;
    dashboardData.value = null;
  } finally {
    dashboardLoading.value = false;
  }
}

async function fetchStats(days: number, signal?: AbortSignal) {
  statsLoading.value = true;
  try {
    const res = await http.get(API_CONFIG.PRODUCTION + "/stats", {
      params: { days },
      signal,
    });
    const list: StatsItem[] = res.data?.data ?? [];
    trendData.value = list.map((item) => ({
      date: item.date,
      planOutput: item.plan_output,
      actualOutput: item.actual_output,
      yieldRate: item.yield_rate.toFixed(1) + "%",
      utilization: item.utilization.toFixed(1) + "%",
      achievementRate: Math.min(Math.round(item.achievement_rate), 100),
    }));
  } catch (error) {
    if ((error as { name?: string })?.name === "AbortError") return;
    trendData.value = [];
  } finally {
    statsLoading.value = false;
  }
}

async function fetchRecords(signal?: AbortSignal) {
  recordsLoading.value = true;
  try {
    const res = await http.get(API_CONFIG.PRODUCTION + "/records/", {
      params: { limit: 20 },
      signal,
    });
    productionRecords.value = res.data?.data ?? [];
  } catch (error) {
    if ((error as { name?: string })?.name === "AbortError") return;
    productionRecords.value = [];
  } finally {
    recordsLoading.value = false;
  }
}

async function fetchWorkOrders(signal?: AbortSignal) {
  workOrdersLoading.value = true;
  try {
    const res = await http.get(API_CONFIG.PRODUCTION + "/work-orders/", {
      params: { limit: 10 },
      signal,
    });
    workOrders.value = res.data?.data ?? [];
  } catch (error) {
    if ((error as { name?: string })?.name === "AbortError") return;
    workOrders.value = [];
  } finally {
    workOrdersLoading.value = false;
  }
}

async function fetchAllData() {
  // 取消之前的请求
  cancelPendingRequests();

  // 创建新的AbortController
  currentAbortController = new AbortController();
  const signal = currentAbortController.signal;

  loading.value = true;
  const days =
    timeRange.value === "custom"
      ? customRangeToDays(customDateRange.value)
      : timeRangeToDays(timeRange.value);

  const promises: Promise<void>[] = [
    fetchDashboard(signal),
    fetchRecords(signal),
    fetchWorkOrders(signal),
  ];
  if (days !== undefined) {
    promises.push(fetchStats(days, signal));
  }

  await Promise.allSettled(promises);
  loading.value = false;
}

function handleTimeRangeChange() {
  fetchAllData();
}

/** 导出当前趋势数据为 CSV 文件（真实导出，非占位）。 */
function handleExport() {
  if (trendData.value.length === 0) {
    ElMessage.warning(t("productionReport.msgExportEmpty"));
    return;
  }
  try {
    const header = [
      t("productionReport.colDate"),
      t("productionReport.colPlanOutput"),
      t("productionReport.colActualOutput"),
      t("productionReport.colYieldRate"),
      t("productionReport.colUtilization"),
      t("productionReport.colAchievementRate"),
    ];
    const lines = trendData.value.map((r) =>
      [
        r.date,
        r.planOutput,
        r.actualOutput,
        r.yieldRate,
        r.utilization,
        r.achievementRate + "%",
      ].join(","),
    );
    const csv = "\uFEFF" + [header.join(","), ...lines].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const dateStr = new Date().toISOString().slice(0, 10);
    a.href = url;
    a.download = `production_report_${dateStr}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    ElMessage.success(t("productionReport.msgExportSuccess"));
  } catch (e: unknown) {
    console.warn("[ProductionReport] export failed:", e);
    ElMessage.error(t("productionReport.msgExportFailed"));
  }
}

onMounted(() => {
  fetchAllData();
});

onUnmounted(() => {
  // 组件卸载时取消所有待处理的请求
  cancelPendingRequests();
});
</script>

<style scoped>
.report-panel {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.export-btn {
  margin-left: auto;
}

.text-warning {
  color: var(--warning);
  font-weight: 500;
}

.text-danger {
  color: var(--error);
  font-weight: 500;
}
</style>
