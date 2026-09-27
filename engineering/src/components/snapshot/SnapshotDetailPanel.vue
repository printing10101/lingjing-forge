<template>
  <div class="snapshot-detail-panel">
    <div class="panel-header">
      <span class="panel-title">{{ t("snapshotPanel.detailTitle") }}</span>
      <div v-if="currentSnapshot" class="panel-header-actions">
        <el-button
          size="small"
          type="primary"
          :icon="VideoPlay"
          :loading="reproducing"
          @click="emit('reproduce')"
        >
          {{ t("snapshotPanel.btnReproduce") }}
        </el-button>
        <el-button size="small" :icon="Close" @click="emit('closeDetail')">
          {{ t("snapshotPanel.btnCloseDetail") }}
        </el-button>
      </div>
    </div>

    <div v-loading="currentLoading" class="snapshot-detail-body">
      <el-empty
        v-if="!currentLoading && !currentSnapshot"
        :description="t('snapshotPanel.emptyNoSnapshots')"
        :image-size="80"
      />
      <div v-if="currentSnapshot" class="snapshot-detail-content">
        <el-descriptions :column="1" border>
          <el-descriptions-item :label="t('snapshotPanel.colId')">
            <span class="mono">{{ currentSnapshot.snapshot_id }}</span>
          </el-descriptions-item>
          <el-descriptions-item :label="t('snapshotPanel.detailCreatedAt')">
            {{ formatTime(currentSnapshot.created_at) }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('snapshotPanel.detailCreatedBy')">
            {{ currentSnapshot.created_by }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('snapshotPanel.detailGitSha')">
            <span class="mono">{{ currentSnapshot.git_sha }}</span>
          </el-descriptions-item>
          <el-descriptions-item :label="t('snapshotPanel.detailCodeStatus')">
            <el-tag
              size="small"
              :type="currentSnapshot.code_dirty ? 'warning' : 'success'"
            >
              {{
                currentSnapshot.code_dirty
                  ? t("snapshotPanel.dirtyDirty")
                  : t("snapshotPanel.dirtyClean")
              }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item :label="t('snapshotPanel.detailModelUri')">
            <span class="mono">{{ currentSnapshot.model_uri }}</span>
          </el-descriptions-item>
          <el-descriptions-item
            :label="t('snapshotPanel.detailDatasetVersions')"
          >
            <div
              v-if="currentSnapshot.dataset_versions.length > 0"
              class="uri-list"
            >
              <div
                v-for="(uri, idx) in currentSnapshot.dataset_versions"
                :key="`uri-${idx}`"
                class="uri-item mono"
              >
                {{ uri }}
              </div>
            </div>
            <span v-else>-</span>
          </el-descriptions-item>
          <el-descriptions-item :label="t('snapshotPanel.detailMetrics')">
            <pre class="json-block">{{
              JSON.stringify(currentSnapshot.metrics, null, 2)
            }}</pre>
          </el-descriptions-item>
          <el-descriptions-item :label="t('snapshotPanel.detailEnvironment')">
            <pre class="json-block">{{
              JSON.stringify(currentSnapshot.environment, null, 2)
            }}</pre>
          </el-descriptions-item>
          <el-descriptions-item
            v-if="currentSnapshot.lineage_record_id"
            :label="t('snapshotPanel.detailLineageRecord')"
          >
            <span class="mono">{{ currentSnapshot.lineage_record_id }}</span>
          </el-descriptions-item>
          <el-descriptions-item
            v-if="currentSnapshot.mlflow_run_id"
            :label="t('snapshotPanel.detailMlflowRunId')"
          >
            <span class="mono">{{ currentSnapshot.mlflow_run_id }}</span>
          </el-descriptions-item>
          <el-descriptions-item
            v-if="currentSnapshot.notes"
            :label="t('snapshotPanel.detailNotes')"
          >
            {{ currentSnapshot.notes }}
          </el-descriptions-item>
        </el-descriptions>

        <div class="config-section">
          <div class="config-section__title">
            {{ t("snapshotPanel.detailConfig") }}
          </div>
          <pre class="json-block json-block--large">{{
            JSON.stringify(currentSnapshot.config, null, 2)
          }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { VideoPlay, Close } from "@element-plus/icons-vue";
import type { ExperimentSnapshot } from "@/contracts/observability";
import { formatDateTimeSafe } from "@/utils/formatters";

const { t } = useI18n();

defineProps<{
  currentSnapshot: ExperimentSnapshot | null;
  currentLoading: boolean;
  reproducing: boolean;
}>();

const emit = defineEmits<{
  (e: "reproduce"): void;
  (e: "closeDetail"): void;
}>();

const formatTime = (iso?: string): string => formatDateTimeSafe(iso);
</script>

<style scoped>
.snapshot-detail-panel {
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

.panel-header-actions {
  display: flex;
  gap: 8px;
}

.snapshot-detail-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.snapshot-detail-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.uri-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.uri-item {
  padding: 2px 6px;
  background: var(--el-fill-color-light);
  border-radius: var(--radius-2xs);
}

.json-block {
  margin: 0;
  padding: 8px;
  background: var(--el-fill-color-darker);
  border-radius: var(--radius-xs);
  font-family: var(--font-mono);
  font-size: 12px;
  overflow-x: auto;
  max-height: 240px;
  overflow-y: auto;
}

.json-block--large {
  max-height: 480px;
}

.config-section__title {
  font-weight: 600;
  margin-bottom: 8px;
  font-size: 14px;
}

.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}
</style>
