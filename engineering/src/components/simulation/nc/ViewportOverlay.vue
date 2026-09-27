<template>
  <!-- Status overlay -->
  <div v-if="simState === 'running'" class="viewport-overlay running-overlay">
    <div class="overlay-spinner">
      <el-icon :size="32" class="is-loading">
        <Loading />
      </el-icon>
    </div>
    <span class="overlay-text">{{ t("simulationPage.overlayRunning") }}</span>
    <span class="overlay-sub">{{
      t("simulationPage.overlayTaskId", { taskId: currentTaskId })
    }}</span>
  </div>
  <div
    v-else-if="simState === 'idle' && !gcode"
    class="viewport-overlay idle-overlay"
  >
    <div class="idle-content">
      <svg
        width="64"
        height="64"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.2"
        class="idle-icon"
      >
        <path
          d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"
        />
      </svg>
      <span class="idle-title">{{ t("simulationPage.idleTitle") }}</span>
      <span class="idle-desc">{{ t("simulationPage.idleDesc") }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { Loading } from "@element-plus/icons-vue";
import type { SimState } from "./types";

const { t } = useI18n();

defineProps<{
  simState: SimState;
  gcode: string;
  currentTaskId: string;
}>();
</script>

<style scoped>
.viewport-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  z-index: 20;
  pointer-events: none;
}

.running-overlay {
  background: var(--bg-3d-overlay);
  backdrop-filter: blur(4px);
}

.overlay-spinner {
  color: var(--accent-primary);
}

.overlay-text {
  font-size: 16px;
  font-weight: 500;
  color: var(--text-primary);
}

.overlay-sub {
  font-size: 12px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

.idle-overlay {
  background: transparent;
}

.idle-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
}

.idle-icon {
  color: var(--text-tertiary);
  opacity: 0.3;
}

.idle-title {
  font-size: 16px;
  font-weight: 500;
  color: var(--text-tertiary);
  opacity: 0.5;
}

.idle-desc {
  font-size: 13px;
  color: var(--text-tertiary);
  opacity: 0.4;
  text-align: center;
  max-width: 240px;
}
</style>
