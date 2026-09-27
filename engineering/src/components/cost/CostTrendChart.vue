<template>
  <el-card shadow="hover" class="chart-card">
    <template #header>
      <div class="card-header">
        <span>{{ t("costDashboard.chartCostTrend") }}</span>
        <div>
          <el-select
            :model-value="days"
            size="small"
            style="width: 100px"
            @update:model-value="$emit('update:days', $event)"
          >
            <el-option :label="t('costDashboard.days7')" :value="7" />
            <el-option :label="t('costDashboard.days14')" :value="14" />
            <el-option :label="t('costDashboard.days30')" :value="30" />
            <el-option :label="t('costDashboard.days60')" :value="60" />
          </el-select>
          <el-button
            size="small"
            :loading="loading"
            circle
            :aria-label="t('costDashboard.refreshCostTrendAriaLabel')"
            :title="t('costDashboard.refreshCostTrendTitle')"
            style="margin-left: 4px"
            @click="loadData"
          >
            <el-icon :size="16">
              <Refresh />
            </el-icon>
          </el-button>
        </div>
      </div>
    </template>
    <div ref="chartRef" class="chart-container chart-trend" />
  </el-card>
</template>

<script lang="ts" setup>
import { ref, watch } from "vue";
import * as echarts from "echarts";
import { Refresh } from "@element-plus/icons-vue";
import { useI18n } from "vue-i18n";
import http from "@/utils/http";
import { API_CONFIG, buildApiPath } from "@/config/api";
import { useEChart } from "@/composables/useEChart";

const { t } = useI18n();

const props = defineProps<{
  days: number;
}>();

defineEmits<{
  "update:days": [value: number];
}>();

interface CostTrendItem {
  timestamp: number;
  total_cost: number;
  gpu_time_cost: number;
  gpu_memory_cost: number;
  api_calls_cost: number;
}

const { chartRef, getChart } = useEChart(() => loadData());
const loading = ref(false);

async function loadData() {
  loading.value = true;
  try {
    const res = await http.get(buildApiPath(API_CONFIG.COST_BUDGET, "/trend"), {
      params: { days: props.days, interval_hours: 24 },
    });
    if (!res.data?.ok) return;
    const data: CostTrendItem[] = res.data.data || [];

    const times = data.map((d) => {
      const dt = new Date(d.timestamp * 1000);
      return `${dt.getMonth() + 1}/${dt.getDate()}`;
    });
    const totalCosts = data.map((d) => d.total_cost || 0);
    const gpuTimeCosts = data.map((d) => d.gpu_time_cost || 0);
    const gpuMemCosts = data.map((d) => d.gpu_memory_cost || 0);
    const apiCallCosts = data.map((d) => d.api_calls_cost || 0);

    const chart = getChart();
    if (chart) {
      chart.setOption({
        tooltip: { trigger: "axis" },
        legend: {
          data: [
            t("costDashboard.seriesTotalCost"),
            t("costDashboard.seriesGpuTime"),
            t("costDashboard.seriesGpuMemory"),
            t("costDashboard.seriesApiCalls"),
          ],
        },
        grid: { left: "3%", right: "4%", bottom: "3%", containLabel: true },
        xAxis: { type: "category", data: times, boundaryGap: false },
        yAxis: { type: "value", name: t("costDashboard.chartYAxisName") },
        series: [
          {
            name: t("costDashboard.seriesTotalCost"),
            type: "line",
            smooth: true,
            data: totalCosts,
            lineStyle: { width: 3, color: "var(--accent-primary)" },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: "rgba(64,158,255,0.3)" },
                { offset: 1, color: "rgba(64,158,255,0.05)" },
              ]),
            },
          },
          {
            name: t("costDashboard.seriesGpuTime"),
            type: "line",
            smooth: true,
            data: gpuTimeCosts,
            lineStyle: { color: "var(--success)" },
          },
          {
            name: t("costDashboard.seriesGpuMemory"),
            type: "line",
            smooth: true,
            data: gpuMemCosts,
            lineStyle: { color: "var(--warning)" },
          },
          {
            name: t("costDashboard.seriesApiCalls"),
            type: "line",
            smooth: true,
            data: apiCallCosts,
            lineStyle: { color: "var(--error)" },
          },
        ],
      });
    }
  } catch (e: unknown) {
    console.warn("[CostTrendChart] loadData failed:", e);
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.days,
  () => {
    loadData();
  },
);
</script>

<style scoped>
.chart-card {
  margin-bottom: 16px;
}

.chart-container {
  width: 100%;
  height: 320px;
}

.chart-trend {
  height: 280px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
