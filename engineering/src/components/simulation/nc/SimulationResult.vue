<template>
  <div
    v-if="simState === 'completed' && simResult"
    class="content-card result-card"
  >
    <div class="content-card__header">
      <span class="content-card__title">{{
        t("simulationPage.resultTitle")
      }}</span>
      <el-tag
        :type="simResult.collision_detected ? 'danger' : 'success'"
        effect="dark"
        size="small"
      >
        {{
          simResult.collision_detected
            ? t("simulationPage.collisionDetected")
            : t("simulationPage.simPassed")
        }}
      </el-tag>
    </div>
    <div class="content-card__body">
      <div class="result-stats">
        <div class="result-stat">
          <span class="stat-label">{{ t("simulationPage.statDuration") }}</span>
          <span class="stat-value"
            >{{ (simResult.duration_seconds ?? 0).toFixed(2) }}s</span
          >
        </div>
        <div class="result-stat">
          <span class="stat-label">{{
            t("simulationPage.statVoxelCount")
          }}</span>
          <span class="stat-value">{{
            formatNumber(simResult.voxel_count ?? 0)
          }}</span>
        </div>
        <div class="result-stat">
          <span class="stat-label">{{
            t("simulationPage.statRemovedVoxel")
          }}</span>
          <span class="stat-value">{{
            formatNumber(simResult.removed_voxel_count ?? 0)
          }}</span>
        </div>
        <div class="result-stat">
          <span class="stat-label">{{
            t("simulationPage.statToolpathSegments")
          }}</span>
          <span class="stat-value">{{
            simResult.toolpath_segment_count ?? 0
          }}</span>
        </div>
      </div>

      <!-- Collision Alert -->
      <div v-if="simResult.collision_detected" class="collision-warning">
        <el-icon :size="20" color="var(--state-error)">
          <WarningFilled />
        </el-icon>
        <div class="collision-warning__content">
          <span class="collision-warning__title">
            {{
              t("simulationPage.collisionCount", {
                count: simResult.collision_details?.count ?? 0,
              })
            }}
          </span>
          <span class="collision-warning__desc">
            {{
              t("simulationPage.collisionSeverity", {
                severity: simResult.collision_details?.severity ?? "-",
              })
            }}
          </span>
        </div>
        <el-button
          size="small"
          type="danger"
          plain
          @click="emit('update:showCollisionDetail', true)"
        >
          {{ t("simulationPage.viewDetail") }}
        </el-button>
      </div>

      <!-- Pass/Fail Action -->
      <div v-if="simResult.collision_detected" class="fail-actions">
        <el-alert type="error" :closable="false" show-icon>
          <template #title>
            <span>{{ t("simulationPage.failAlertTitle") }}</span>
          </template>
          <template #default>
            <div class="fail-suggestions">
              <p>{{ t("simulationPage.suggestTitle") }}</p>
              <ul>
                <li>{{ t("simulationPage.suggest1") }}</li>
                <li>{{ t("simulationPage.suggest2") }}</li>
                <li>{{ t("simulationPage.suggest3") }}</li>
                <li>{{ t("simulationPage.suggest4") }}</li>
              </ul>
            </div>
          </template>
        </el-alert>
      </div>
      <div v-else class="pass-info">
        <el-alert type="success" :closable="false" show-icon>
          <template #title>
            <span>{{ t("simulationPage.passAlertTitle") }}</span>
          </template>
          <template #default>
            <span>{{ t("simulationPage.passAlertDesc") }}</span>
          </template>
        </el-alert>
      </div>

      <!-- Action buttons for completed simulation -->
      <div class="result-actions">
        <el-button
          size="small"
          :icon="Download"
          :disabled="!simResult?.simulation_result?.workpiece_stl_path"
          @click="emit('download-stl')"
        >
          {{ t("simulationPage.downloadStl") }}
        </el-button>
        <el-button
          size="small"
          @click="emit('update:showCollisionDetail', true)"
        >
          {{
            simResult.collision_detected
              ? t("simulationPage.collisionDetail")
              : t("simulationPage.viewReport")
          }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { Download, WarningFilled } from "@element-plus/icons-vue";
import type { SimResultData, SimState } from "./types";

const { t } = useI18n();

defineProps<{
  simResult: SimResultData | null;
  simState: SimState;
}>();

const emit = defineEmits<{
  "download-stl": [];
  "update:showCollisionDetail": [value: boolean];
}>();

function formatNumber(n: number): string {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(1) + "K";
  return String(n);
}
</script>

<style scoped>
.result-card {
  border-left: 3px solid var(--state-success);
}

.result-card:has(.collision-warning) {
  border-left-color: var(--state-error);
}

.content-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-bottom: 1px solid var(--bg-200);
}

.content-card__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.content-card__body {
  padding: 16px 20px;
}

.result-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 16px;
}

.result-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 10px 8px;
  background: var(--bg-200);
  border-radius: var(--radius-sm);
}

.stat-label {
  font-size: 11px;
  color: var(--text-tertiary);
}

.stat-value {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.collision-warning {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px;
  margin: 12px 0;
  background: var(--state-error-bg);
  border: 1px solid var(--state-error-border);
  border-radius: var(--radius-sm);
}

.collision-warning__content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.collision-warning__title {
  font-size: 14px;
  font-weight: 500;
  color: var(--state-error);
}

.collision-warning__desc {
  font-size: 12px;
  color: var(--text-secondary);
}

.fail-actions {
  margin-top: 12px;
}

.fail-suggestions {
  margin: 4px 0 0 0;
  padding-left: 0;
}

.fail-suggestions p {
  margin: 0 0 4px;
  font-size: 13px;
  color: var(--text-primary);
}

.fail-suggestions ul {
  margin: 0;
  padding-left: 18px;
}

.fail-suggestions li {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.8;
}

.pass-info {
  margin-top: 12px;
}

.result-actions {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}
</style>
