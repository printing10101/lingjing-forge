<template>
  <div class="flywheel-dashboard-page">
    <!-- ===== Page Header ===== -->
    <div class="page-header">
      <div class="page-header__title">
        <h1>{{ t("flywheel.pageTitle") }}</h1>
        <span class="page-header__subtitle">
          {{ t("flywheel.pageSubtitle") }}
        </span>
      </div>
      <div class="page-header__actions">
        <el-tag v-if="store.status" :type="store.healthTagType" size="default">
          {{ t("flywheel.healthLabel") }}: {{ store.healthStatusLabel }}
        </el-tag>
        <el-button
          size="small"
          :icon="Refresh"
          :loading="store.anyLoading"
          @click="handleRefreshAll"
        >
          {{ t("flywheel.btnRefresh") }}
        </el-button>
      </div>
    </div>

    <!-- ===== Error Banner ===== -->
    <el-alert
      v-if="store.error"
      :title="store.error"
      type="error"
      show-icon
      :closable="true"
      class="error-banner"
      @close="store.error = null"
    />

    <!-- ===== Tabs ===== -->
    <el-tabs v-model="activeTab" type="card" class="flywheel-tabs">
      <!-- ====== Tab 1: 概览 ====== -->
      <el-tab-pane :label="t('flywheel.tabOverview')" name="overview">
        <FlywheelOverview
          :status="store.status"
          :loading="store.loading"
          :weekly-report="store.weeklyReport"
          :report-loading="store.reportLoading"
          :health-tag-type="store.healthTagType"
          :health-status-label="store.healthStatusLabel"
          @fetch-weekly-report="handleFetchWeeklyReport"
        />
      </el-tab-pane>

      <!-- ====== Tab 2: 反馈 ====== -->
      <el-tab-pane :label="t('flywheel.tabFeedback')" name="feedback">
        <FlywheelFeedback
          :feedback-stats="store.feedbackStats"
          :metric-definitions="store.metricDefinitions"
          :definitions-loading="store.definitionsLoading"
          @reload-definitions="store.fetchDefinitions()"
        />
      </el-tab-pane>

      <!-- ====== Tab 3: 模型热更新 ====== -->
      <el-tab-pane :label="t('flywheel.tabModels')" name="models">
        <FlywheelModels
          :deployments-loading="store.deploymentsLoading"
          :active-deployments="store.activeDeployments"
          :deployments="store.deployments"
          @search="handleFilterDeployments"
          @reset="handleResetDeploymentFilters"
        />
      </el-tab-pane>

      <!-- ====== Tab 4: 指标历史 ====== -->
      <el-tab-pane :label="t('flywheel.tabMetrics')" name="metrics">
        <FlywheelMetrics
          :metrics-loading="store.metricsLoading"
          :current-metrics="store.currentMetrics"
          :historical-metrics="store.historicalMetrics"
          :metrics-period-days="store.metricsPeriodDays"
          :metrics-days="metricsDays"
          @update:metrics-days="handleMetricsDaysChange"
          @refresh="handleRefreshMetrics"
        />
      </el-tab-pane>

      <!-- ====== Tab 5: 它学会了什么（W7.2 离线反思/灰度事件流） ====== -->
      <el-tab-pane :label="t('flywheel.tabLearnings')" name="learnings">
        <FlywheelLearnings />
      </el-tab-pane>

      <!-- ====== Tab 6: 实验快照（原 /snapshot-panel 独立页并入） ====== -->
      <el-tab-pane :label="t('flywheel.tabSnapshots')" name="snapshots">
        <SnapshotWorkbench v-if="visitedTabs.snapshots" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, watch } from "vue";
import { useRoute } from "vue-router";
import { useI18n } from "vue-i18n";
import { Refresh } from "@element-plus/icons-vue";
import { useFlywheelStore } from "@/stores/flywheel";
import type { DeploymentStatus } from "@/stores/flywheel";
import FlywheelOverview from "./flywheel/FlywheelOverview.vue";
import FlywheelFeedback from "./flywheel/FlywheelFeedback.vue";
import FlywheelModels from "./flywheel/FlywheelModels.vue";
import FlywheelMetrics from "./flywheel/FlywheelMetrics.vue";
import FlywheelLearnings from "./flywheel/FlywheelLearnings.vue";
import SnapshotWorkbench from "@/components/snapshot/SnapshotWorkbench.vue";

const { t } = useI18n();
const store = useFlywheelStore();
const route = useRoute();

// 本地状态
const activeTab = ref<
  "overview" | "feedback" | "models" | "metrics" | "learnings" | "snapshots"
>("overview");
const metricsDays = ref<number>(7);

// Tab 内容首次激活才挂载（不依赖 el-tabs 的 lazy，生产与测试行为一致）
const visitedTabs = reactive<Record<string, boolean>>({ overview: true });
watch(activeTab, (tab) => {
  visitedTabs[tab] = true;
});

// 深链兼容：/snapshot-panel → /flywheel-dashboard?tab=snapshots
watch(
  () => route.query.tab,
  (tab) => {
    if (route.path === "/flywheel-dashboard" && tab === "snapshots") {
      activeTab.value = "snapshots";
    }
  },
  { immediate: true },
);

// 事件处理
async function handleRefreshAll(): Promise<void> {
  await store.refreshAll(metricsDays.value);
}

async function handleFetchWeeklyReport(): Promise<void> {
  await store.fetchWeeklyReport(false);
}

async function handleRefreshMetrics(): Promise<void> {
  await store.fetchMetrics(metricsDays.value);
}

async function handleMetricsDaysChange(days: number): Promise<void> {
  metricsDays.value = days;
  await store.fetchMetrics(days);
}

async function handleFilterDeployments(
  modelName: string | undefined,
  status: DeploymentStatus | undefined,
): Promise<void> {
  await store.fetchDeployments(modelName, status);
}

async function handleResetDeploymentFilters(): Promise<void> {
  await store.fetchDeployments();
}

// 生命周期
onMounted(() => {
  void store.refreshAll(metricsDays.value);
});
</script>

<style scoped>
.flywheel-dashboard-page {
  padding: 16px 24px;
  height: 100%;
  overflow-y: auto;
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}

.page-header__title h1 {
  margin: 0 0 4px 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.page-header__subtitle {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.page-header__actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.error-banner {
  margin-bottom: 16px;
}

.flywheel-tabs {
  margin-top: 8px;
}
</style>
