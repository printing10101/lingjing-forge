<template>
  <div class="content-card">
    <div class="content-card__header">
      <span class="content-card__title">{{
        t("simulationPage.historyTitle")
      }}</span>
    </div>
    <div class="content-card__body">
      <div v-if="historyLoading" class="loading-wrap">
        <el-skeleton :rows="3" animated />
      </div>
      <el-empty
        v-else-if="historyItems.length === 0"
        :description="t('simulationPage.noHistory')"
        :image-size="60"
      />
      <div v-else class="history-list">
        <div
          v-for="item in historyItems"
          :key="item.task_id"
          class="history-item"
        >
          <div class="history-item__main">
            <el-tag
              :type="item.collision_collided ? 'danger' : 'success'"
              size="small"
              effect="plain"
              class="history-status"
            >
              {{
                item.collision_collided
                  ? t("simulationPage.historyCollision")
                  : t("simulationPage.historyPass")
              }}
            </el-tag>
            <span class="history-id">{{ item.task_id }}</span>
          </div>
          <div class="history-item__meta">
            <span>{{ item.duration_seconds?.toFixed(2) ?? "-" }}s</span>
            <span>{{
              t("simulationPage.historyVoxel", { size: item.voxel_size ?? "-" })
            }}</span>
            <span>{{
              t("simulationPage.historySegments", {
                count: item.segment_count ?? 0,
              })
            }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import type { HistoryItem } from "./types";

const { t } = useI18n();

defineProps<{
  historyItems: HistoryItem[];
  historyLoading: boolean;
}>();
</script>

<style scoped>
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

.loading-wrap {
  padding: 8px 0;
}

.history-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px;
  background: var(--bg-200);
  border-radius: var(--radius-sm);
  transition: background 0.2s;
}

.history-item:hover {
  background: var(--bg-200);
}

.history-item__main {
  display: flex;
  align-items: center;
  gap: 10px;
}

.history-status {
  flex-shrink: 0;
}

.history-id {
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--text-secondary);
}

.history-item__meta {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>
