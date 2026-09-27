<template>
  <el-card shadow="hover" class="chart-card">
    <template #header>
      <div class="card-header">
        <span>{{ t("costDashboard.chartCostByType") }}</span>
        <div>
          <el-button
            size="small"
            :loading="loading"
            circle
            :aria-label="t('costDashboard.refreshCostByTypeAriaLabel')"
            :title="t('costDashboard.refreshCostByTypeTitle')"
            @click="loadData"
          >
            <el-icon :size="16">
              <Refresh />
            </el-icon>
          </el-button>
        </div>
      </div>
    </template>
    <div ref="chartRef" class="chart-container" />
  </el-card>
</template>

<script lang="ts" setup>
import { ref } from "vue";
import { Refresh } from "@element-plus/icons-vue";
import { useI18n } from "vue-i18n";
import http from "@/utils/http";
import { API_CONFIG, buildApiPath } from "@/config/api";
import { useEChart } from "@/composables/useEChart";

const { t } = useI18n();

interface CostSummaryItem {
  scope_id: string;
  total_cost: number;
  gpu_time_cost: number;
  gpu_memory_cost: number;
  api_calls_cost: number;
  data_transfer_cost: number;
}

const { chartRef, getChart } = useEChart(() => loadData());
const loading = ref(false);

async function loadData() {
  loading.value = true;
  try {
    const res = await http.get(
      buildApiPath(API_CONFIG.COST_BUDGET, "/summary"),
      {
        params: { dimension: "agent" },
      },
    );
    if (!res.data?.ok) return;
    const data: CostSummaryItem[] = res.data.data || [];

    const gpuTimeVals = data.map((d) => d.gpu_time_cost || 0);
    const gpuMemVals = data.map((d) => d.gpu_memory_cost || 0);
    const apiCallVals = data.map((d) => d.api_calls_cost || 0);
    const dataTransferVals = data.map((d) => d.data_transfer_cost || 0);
    const labels = data.map((d) => d.scope_id || "(unknown)");

    const chart = getChart();
    if (chart) {
      chart.setOption({
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow" },
        },
        legend: {
          data: [
            t("costDashboard.seriesGpuTime"),
            t("costDashboard.seriesGpuMemory"),
            t("costDashboard.seriesApiCalls"),
            t("costDashboard.seriesDataTransfer"),
          ],
        },
        grid: { left: "3%", right: "4%", bottom: "3%", containLabel: true },
        xAxis: {
          type: "category",
          data: labels,
          axisLabel: { rotate: 30, fontSize: 11 },
        },
        yAxis: { type: "value", name: t("costDashboard.chartYAxisName") },
        series: [
          {
            name: t("costDashboard.seriesGpuTime"),
            type: "bar",
            stack: "total",
            data: gpuTimeVals,
            itemStyle: { color: "var(--accent-primary)" },
          },
          {
            name: t("costDashboard.seriesGpuMemory"),
            type: "bar",
            stack: "total",
            data: gpuMemVals,
            itemStyle: { color: "var(--success)" },
          },
          {
            name: t("costDashboard.seriesApiCalls"),
            type: "bar",
            stack: "total",
            data: apiCallVals,
            itemStyle: { color: "var(--warning)" },
          },
          {
            name: t("costDashboard.seriesDataTransfer"),
            type: "bar",
            stack: "total",
            data: dataTransferVals,
            itemStyle: { color: "var(--error)" },
          },
        ],
      });
    }
  } catch (e: unknown) {
    console.warn("[CostByTypeChart] loadData failed:", e);
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.chart-card {
  margin-bottom: 16px;
}

.chart-container {
  width: 100%;
  height: 320px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
