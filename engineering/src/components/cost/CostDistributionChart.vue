<template>
  <el-card shadow="hover" class="chart-card">
    <template #header>
      <div class="card-header">
        <span>{{ t("costDashboard.chartCostDistribution") }}</span>
        <div>
          <el-select
            :model-value="dimension"
            size="small"
            style="width: 120px"
            @update:model-value="$emit('update:dimension', $event)"
          >
            <el-option
              :label="t('costDashboard.dimensionAgent')"
              value="agent"
            />
            <el-option
              :label="t('costDashboard.dimensionProject')"
              value="project"
            />
            <el-option
              :label="t('costDashboard.dimensionModel')"
              value="model"
            />
            <el-option
              :label="t('costDashboard.dimensionProvider')"
              value="provider"
            />
          </el-select>
          <el-button
            size="small"
            :loading="loading"
            circle
            :aria-label="t('costDashboard.refreshCostDistributionAriaLabel')"
            :title="t('costDashboard.refreshCostDistributionTitle')"
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
    <div ref="chartRef" class="chart-container" />
  </el-card>
</template>

<script lang="ts" setup>
import { ref, watch } from "vue";
import { Refresh } from "@element-plus/icons-vue";
import { useI18n } from "vue-i18n";
import http from "@/utils/http";
import { API_CONFIG, buildApiPath } from "@/config/api";
import { useEChart } from "@/composables/useEChart";

const { t } = useI18n();

const props = defineProps<{
  dimension: string;
}>();

defineEmits<{
  "update:dimension": [value: string];
}>();

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
        params: { dimension: props.dimension },
      },
    );
    if (!res.data?.ok) return;
    const data: CostSummaryItem[] = res.data.data || [];

    const names = data.map((d) => d.scope_id || "(unknown)");
    const values = data.map((d) => d.total_cost || 0);

    const chart = getChart();
    if (chart) {
      chart.setOption({
        tooltip: {
          trigger: "item",
          formatter: (params: {
            name: string;
            value: number;
            percent: number;
          }) =>
            `${params.name}: $${params.value.toFixed(4)} (${params.percent}%)`,
        },
        series: [
          {
            type: "pie",
            radius: ["45%", "75%"],
            center: ["50%", "50%"],
            roseType: "area",
            itemStyle: {
              borderRadius: 6,
              borderColor: "var(--bg-card)",
              borderWidth: 2,
            },
            data: names.map((n: string, i: number) => ({
              name: n,
              value: values[i],
            })),
            label: { formatter: "{b}\n{d}%" },
          },
        ],
      });
    }
  } catch (e: unknown) {
    console.warn("[CostDistributionChart] loadData failed:", e);
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.dimension,
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

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
</style>
