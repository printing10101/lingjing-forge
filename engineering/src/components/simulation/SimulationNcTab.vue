<template>
  <div class="tab-panel">
    <div class="sim-layout">
      <!-- Left Column: Input & Controls -->
      <div class="sim-left">
        <!-- NC Code Input Section -->
        <NcCodeEditor
          :gcode="gcode"
          @update:gcode="emit('update:gcode', $event)"
        />

        <!-- Simulation Parameters -->
        <SimulationParams
          :sim-params="simParams"
          @update:sim-params="emit('update:simParams', $event)"
        />

        <!-- Run Button -->
        <div class="run-section">
          <el-button
            type="primary"
            size="large"
            class="btn-run"
            :loading="simState === 'running'"
            :disabled="!gcode.trim()"
            @click="emit('run')"
          >
            <el-icon v-if="simState !== 'running'" class="btn-icon">
              <VideoPlay />
            </el-icon>
            <span>{{ runButtonText }}</span>
          </el-button>
          <el-button
            v-if="simState === 'completed'"
            size="large"
            class="btn-rerun"
            @click="emit('run')"
          >
            {{ t("simulationPage.rerunSim") }}
          </el-button>
        </div>

        <!-- Simulation Result -->
        <SimulationResult
          :sim-result="simResult"
          :sim-state="simState"
          @download-stl="emit('download-stl')"
          @update:show-collision-detail="
            emit('update:showCollisionDetail', $event)
          "
        />

        <!-- Simulation History -->
        <SimulationHistory
          :history-items="historyItems"
          :history-loading="historyLoading"
        />
      </div>

      <!-- Right Column: 3D Viewport -->
      <div class="sim-right">
        <SimulationViewer
          ref="viewerRef"
          width="100%"
          height="100%"
          :show-grid="true"
          :show-axes="true"
          @ready="onViewerReady"
        />

        <ViewportOverlay
          :sim-state="simState"
          :gcode="gcode"
          :current-task-id="currentTaskId"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from "vue";
import { useI18n } from "vue-i18n";
import { VideoPlay } from "@element-plus/icons-vue";
import SimulationViewer from "@/components/simulation/SimulationViewer.vue";
import NcCodeEditor from "@/components/simulation/nc/NcCodeEditor.vue";
import SimulationParams from "@/components/simulation/nc/SimulationParams.vue";
import SimulationResult from "@/components/simulation/nc/SimulationResult.vue";
import SimulationHistory from "@/components/simulation/nc/SimulationHistory.vue";
import ViewportOverlay from "@/components/simulation/nc/ViewportOverlay.vue";
import type {
  SimParams,
  SimResultData,
  HistoryItem,
  SimState,
} from "@/components/simulation/nc/types";

const { t } = useI18n();

// ─── Props ──────────────────────────────────────────────

const props = defineProps<{
  gcode: string;
  simParams: SimParams;
  simState: SimState;
  simResult: SimResultData | null;
  historyItems: HistoryItem[];
  historyLoading: boolean;
  currentTaskId: string;
}>();

const emit = defineEmits<{
  "update:gcode": [value: string];
  "update:simParams": [value: SimParams];
  run: [];
  "download-stl": [];
  "update:showCollisionDetail": [value: boolean];
  "locate-collision": [index: number];
  "dismiss-collision": [index: number];
  "dismiss-all-collisions": [];
}>();

// ─── Template refs ─────────────────────────────────────

const viewerRef = ref<InstanceType<typeof SimulationViewer> | null>(null);

defineExpose({ viewerRef });

// ─── Run Button Text ─────────────────────────────────────

const runButtonText = computed(() => {
  switch (props.simState) {
    case "running":
      return t("simulationPage.simRunning");
    case "completed":
      return t("simulationPage.rerunSim");
    default:
      return t("simulationPage.runSim");
  }
});

// ─── Viewer Ready ────────────────────────────────────────

function onViewerReady() {
  // Viewer is initialized
}
</script>

<style scoped>
/* ─── Tab Panel ────────────────────────────────────────── */
.tab-panel {
  animation: fadeIn 0.25s ease;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* ─── Simulation Layout ──────────────────────────────── */
.sim-layout {
  display: grid;
  grid-template-columns: 420px 1fr;
  gap: 20px;
  min-height: 600px;
}

@media (max-width: 1200px) {
  .sim-layout {
    grid-template-columns: 1fr;
  }
  .sim-right {
    min-height: 400px;
  }
}

.sim-left {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-height: calc(100vh - 180px);
  overflow-y: auto;
  padding-right: 4px;
}

.sim-right {
  position: relative;
  min-height: 500px;
  border-radius: var(--radius-lg);
  overflow: hidden;
  border: 1px solid var(--bg-200);
  background: var(--bg-100);
}

/* ─── Run Button ───────────────────────────────────────── */
.run-section {
  display: flex;
  gap: 12px;
  align-items: center;
}

.btn-run {
  flex: 1;
  font-weight: 500;
  height: 42px;
}

.btn-icon {
  margin-right: 4px;
}

.btn-rerun {
  height: 42px;
}
</style>
