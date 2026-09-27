<template>
  <div class="simulation-page">
    <!-- 页面头部 -->
    <SimulationPageHeader
      :history-loading="historyLoading"
      @refresh="fetchHistory"
      @new-simulation="handleNewSimulation"
    />

    <!-- 统计概览 -->
    <SimulationStatsRow
      :total-count="historyItems.length"
      :pass-count="passCount"
      :fail-count="failCount"
      :avg-duration="avgDuration"
    />

    <!-- Tab 切换 -->
    <SimulationTabSwitcher v-model="activeTab" />

    <!-- Tab 1: NC Code Simulation -->
    <SimulationNcTab
      v-show="activeTab === 'simulation'"
      ref="ncTabRef"
      :gcode="gcode"
      :sim-params="simParams"
      :sim-state="simState"
      :sim-result="simResult"
      :history-items="historyItems"
      :history-loading="historyLoading"
      :current-task-id="currentTaskId"
      @update:gcode="gcode = $event"
      @update:sim-params="simParams = $event"
      @run="handleRunSimulation"
      @download-stl="handleDownloadStl"
      @update:show-collision-detail="showCollisionDetail = $event"
    />

    <!-- Tab 2: FEM Analysis -->
    <SimulationFemTab
      v-show="activeTab === 'fem'"
      :fem-params="femParams"
      :fem-result="femResult"
      :fem-solving="femSolving"
      @update:fem-params="femParams = $event"
      @solve="handleStartSolve"
      @reset="resetFemParams"
    />

    <!-- Tab 3: Export Management -->
    <SimulationExportTab
      v-show="activeTab === 'export'"
      :gif-export="gifExport"
      :mp4-export="mp4Export"
      :export-loading="exportLoading"
      @update:gif-export="gifExport = $event"
      @update:mp4-export="mp4Export = $event"
      @export-gif="handleExportGif"
      @export-mp4="handleExportMp4"
    />

    <!-- Collision Detail Modal -->
    <CollisionAlertModal
      v-model:visible="showCollisionDetail"
      :collisions="collisionList"
      @locate="handleLocateCollision"
      @dismiss="handleDismissCollision"
      @dismiss-all="handleDismissAllCollisions"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage } from "element-plus";
import {
  useAnimationExport,
  useCollisionHandling,
  useFemSolver,
  useSimulationHistory,
  useSimulationRunner,
} from "@/composables/simulation";
import SimulationPageHeader from "@/components/simulation/SimulationPageHeader.vue";
import SimulationStatsRow from "@/components/simulation/SimulationStatsRow.vue";
import SimulationTabSwitcher from "@/components/simulation/SimulationTabSwitcher.vue";
import SimulationNcTab from "@/components/simulation/SimulationNcTab.vue";
import SimulationFemTab from "@/components/simulation/SimulationFemTab.vue";
import SimulationExportTab from "@/components/simulation/SimulationExportTab.vue";
import CollisionAlertModal from "@/components/simulation/CollisionAlertModal.vue";

const ncTabRef = ref<InstanceType<typeof SimulationNcTab> | null>(null);
const activeTab = ref<string>("simulation");
const { t } = useI18n();

// 历史与统计
const {
  historyItems,
  historyLoading,
  fetchHistory,
  passCount,
  failCount,
  avgDuration,
} = useSimulationHistory();

// 仿真运行核心
const {
  gcode,
  simParams,
  simState,
  currentTaskId,
  simResult,
  showCollisionDetail,
  handleRunSimulation,
  stopPolling,
  resetRun,
} = useSimulationRunner({
  onTaskCompleted: () => {
    void fetchHistory();
  },
  getViewer: () => ncTabRef.value?.viewerRef ?? null,
});

// 碰撞处理
const {
  collisionList,
  handleLocateCollision,
  handleDismissCollision,
  handleDismissAllCollisions,
} = useCollisionHandling(simResult);

// FEM 求解
const { femParams, femResult, femSolving, resetFemParams, handleStartSolve } =
  useFemSolver();

// 动画导出与 STL 下载
const {
  gifExport,
  mp4Export,
  exportLoading,
  handleExportGif,
  handleExportMp4,
  handleDownloadStl,
} = useAnimationExport({ gcode, simParams, simResult });

function handleNewSimulation() {
  resetRun();
  activeTab.value = "simulation";
}

/**
 * 消费自然语言建模页的交接数据（sessionStorage: nl2cad_simulation_handoff）：
 * 有生成的 G 代码则预填并切到仿真 Tab，实现"建模→仿真"链路贯通。
 */
function consumeNl2cadHandoff() {
  try {
    const raw = sessionStorage.getItem("nl2cad_simulation_handoff");
    if (!raw) return;
    sessionStorage.removeItem("nl2cad_simulation_handoff");
    const handoff = JSON.parse(raw) as { model_path?: string; gcode?: string };
    if (handoff.gcode) {
      gcode.value = handoff.gcode;
      activeTab.value = "simulation";
      ElMessage.success(t("simulationPage.msgNl2cadHandoff"));
    }
  } catch {
    // 交接数据损坏时静默忽略，不影响正常仿真流程
  }
}

onMounted(() => {
  void fetchHistory();
  consumeNl2cadHandoff();
});

onUnmounted(() => {
  stopPolling();
});
</script>

<style scoped>
.simulation-page {
  padding: var(--page-padding);
  max-width: var(--content-max-width);
  margin: 0 auto;
}
</style>
