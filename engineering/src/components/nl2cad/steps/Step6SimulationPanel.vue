<template>
  <div class="content-panel">
    <div class="panel-header">
      <h3>{{ t("workflowGuide.step6Header") }}</h3>
      <p class="hint">
        {{ t("workflowGuide.step6Hint") }}
      </p>
    </div>
    <div class="panel-body">
      <div class="simulation-container">
        <div class="simulation-viewport">
          <slot name="simulation-viewer" />
        </div>
        <div class="simulation-controls">
          <el-button-group>
            <el-button type="primary" @click="$emit('startSimulation')">
              <el-icon><VideoPlay /></el-icon>
              {{ t("workflowGuide.btnStartSim") }}
            </el-button>
            <el-button @click="$emit('pauseSimulation')">
              <el-icon><VideoPause /></el-icon>
              {{ t("workflowGuide.btnPause") }}
            </el-button>
            <el-button @click="$emit('resetSimulation')">
              <el-icon><RefreshRight /></el-icon>
              {{ t("workflowGuide.btnReset") }}
            </el-button>
          </el-button-group>
          <el-button type="success" @click="$emit('downloadAnimation')">
            <el-icon><Download /></el-icon>
            {{ t("workflowGuide.btnDownloadAnimation") }}
          </el-button>
        </div>
      </div>
      <div class="panel-actions">
        <el-button @click="$emit('prev')">
          <el-icon><ArrowLeft /></el-icon>
          {{ t("workflowGuide.btnModifyCode") }}
        </el-button>
        <el-button type="primary" @click="$emit('complete')">
          <el-icon><CircleCheck /></el-icon>
          {{ t("workflowGuide.btnComplete") }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import {
  ArrowLeft,
  CircleCheck,
  Download,
  RefreshRight,
  VideoPlay,
  VideoPause,
} from "@element-plus/icons-vue";

defineEmits<{
  (e: "prev"): void;
  (e: "complete"): void;
  (e: "startSimulation"): void;
  (e: "pauseSimulation"): void;
  (e: "resetSimulation"): void;
  (e: "downloadAnimation"): void;
}>();

const { t } = useI18n();
</script>

<style scoped>
.content-panel {
  max-width: 900px;
  margin: 0 auto;
}
.panel-header {
  margin-bottom: 24px;
}
.panel-header h3 {
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0 0 8px;
}
.panel-header .hint {
  font-size: 14px;
  color: var(--text-tertiary);
  margin: 0;
}
.panel-body {
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-lg);
  padding: 24px;
}
.panel-actions {
  display: flex;
  justify-content: space-between;
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--border-light);
}
.simulation-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.simulation-viewport {
  height: 400px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.simulation-controls {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 16px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
}
</style>
