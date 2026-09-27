<template>
  <div class="snapshot-list-panel">
    <div class="panel-header">
      <span class="panel-title">{{ t("snapshotPanel.listTitle") }}</span>
      <el-button size="small" link @click="emit('resetFilters')">
        {{ t("snapshotPanel.btnResetFilters") }}
      </el-button>
    </div>

    <div class="panel-filters">
      <el-input
        :model-value="filterCreatedBy"
        size="small"
        :placeholder="t('snapshotPanel.filterCreatedBy')"
        clearable
        @update:model-value="
          (val: string | number) => emit('update:filterCreatedBy', String(val))
        "
        @change="emit('filterChange')"
      />
      <el-input
        :model-value="filterGitSha"
        size="small"
        :placeholder="t('snapshotPanel.filterGitSha')"
        clearable
        @update:model-value="
          (val: string | number) => emit('update:filterGitSha', String(val))
        "
        @change="emit('filterChange')"
      />
      <el-input
        :model-value="filterModelUri"
        size="small"
        :placeholder="t('snapshotPanel.filterModelUri')"
        clearable
        @update:model-value="
          (val: string | number) => emit('update:filterModelUri', String(val))
        "
        @change="emit('filterChange')"
      />
    </div>

    <div v-loading="loading" class="snapshot-list-body">
      <el-empty
        v-if="!loading && snapshots.length === 0"
        :description="t('snapshotPanel.emptyNoSnapshots')"
        :image-size="60"
      />
      <div
        v-for="snap in snapshots"
        :key="snap.snapshot_id"
        class="snapshot-card"
        :class="{ active: snap.snapshot_id === currentSnapshotId }"
        @click="emit('select', snap.snapshot_id)"
      >
        <div class="snapshot-card-header">
          <span class="snapshot-id">{{
            snap.snapshot_id.substring(0, 8)
          }}</span>
          <el-tag size="small" :type="snap.code_dirty ? 'warning' : 'success'">
            {{
              snap.code_dirty
                ? t("snapshotPanel.dirtyDirty")
                : t("snapshotPanel.dirtyClean")
            }}
          </el-tag>
        </div>
        <div class="snapshot-card-meta">
          <div class="meta-row">
            <span class="meta-label"
              >{{ t("snapshotPanel.colCreatedBy") }}:</span
            >
            <span class="meta-value">{{ snap.created_by }}</span>
          </div>
          <div class="meta-row">
            <span class="meta-label">{{ t("snapshotPanel.colGitSha") }}:</span>
            <span class="meta-value mono">{{ shortSha(snap.git_sha) }}</span>
          </div>
          <div class="meta-row">
            <span class="meta-label"
              >{{ t("snapshotPanel.colModelUri") }}:</span
            >
            <span class="meta-value mono">{{ snap.model_uri }}</span>
          </div>
          <div class="meta-row">
            <span class="meta-label"
              >{{ t("snapshotPanel.colCreatedAt") }}:</span
            >
            <span class="meta-value">{{ formatTime(snap.created_at) }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="panel-pagination">
      <el-pagination
        :current-page="currentPage"
        :page-size="pageSize"
        :total="totalCount"
        layout="prev, pager, next"
        small
        @current-change="(val: number) => emit('pageChange', val)"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import type { SnapshotSummary } from "@/composables/useSnapshots";
import { formatDateTimeSafe } from "@/utils/formatters";

const { t } = useI18n();

defineProps<{
  snapshots: SnapshotSummary[];
  loading: boolean;
  currentPage: number;
  pageSize: number;
  totalCount: number;
  currentSnapshotId: string | null | undefined;
  filterCreatedBy: string;
  filterGitSha: string;
  filterModelUri: string;
}>();

const emit = defineEmits<{
  (e: "update:filterCreatedBy", val: string): void;
  (e: "update:filterGitSha", val: string): void;
  (e: "update:filterModelUri", val: string): void;
  (e: "select", snapshotId: string): void;
  (e: "resetFilters"): void;
  (e: "pageChange", page: number): void;
  (e: "filterChange"): void;
}>();

function shortSha(sha?: string): string {
  if (!sha) return "-";
  return sha.length > 8 ? sha.substring(0, 8) : sha;
}

const formatTime = (iso?: string): string => formatDateTimeSafe(iso);
</script>

<style scoped>
.snapshot-list-panel {
  display: flex;
  flex-direction: column;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-light);
}

.panel-title {
  font-weight: 600;
  font-size: 14px;
}

.panel-filters {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.snapshot-list-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.snapshot-card {
  padding: 10px 12px;
  margin-bottom: 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-xs);
  cursor: pointer;
  transition: all 0.2s;
}

.snapshot-card:hover {
  border-color: var(--accent-primary);
  background: var(--el-fill-color-light);
}

.snapshot-card.active {
  border-color: var(--accent-primary);
  background: var(--accent-light);
}

.snapshot-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.snapshot-id {
  font-weight: 600;
  font-size: 13px;
  font-family: var(--font-mono);
}

.snapshot-card-meta {
  font-size: 12px;
}

.meta-row {
  display: flex;
  margin-bottom: 2px;
}

.meta-label {
  color: var(--el-text-color-secondary);
  width: 80px;
  flex-shrink: 0;
}

.meta-value {
  color: var(--el-text-color-primary);
  word-break: break-all;
}

.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}

.panel-pagination {
  padding: 8px 12px;
  border-top: 1px solid var(--el-border-color-lighter);
  display: flex;
  justify-content: center;
}
</style>
