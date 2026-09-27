<template>
  <section class="wm-list-panel">
    <div class="wm-list-panel__header">
      <span class="wm-list-panel__title">{{ t('worldModel.versionList') }}</span>
      <el-tag v-if="versionPagination" size="small" type="info">
        {{ versionPagination.total }}
      </el-tag>
    </div>
    <div v-loading="versionsLoading" class="wm-list-panel__body">
      <el-empty
        v-if="!versionsLoading && versions.length === 0"
        :description="t('worldModel.emptyVersions')"
      />
      <div
        v-for="version in versions"
        :key="version.version"
        class="wm-version-card"
        :class="{ 'wm-version-card--active': currentVersion?.version === version.version }"
        @click="handleSelect(version.version)"
      >
        <div class="wm-version-card__header">
          <span class="wm-version-card__version">v{{ version.version }}</span>
          <el-tag v-if="version.is_active" type="success" size="small">
            {{ t('worldModel.active') }}
          </el-tag>
        </div>
        <div class="wm-version-card__desc">{{ version.description || '—' }}</div>
        <div class="wm-version-card__meta">
          <span>horizon: {{ version.prediction_horizon }}</span>
          <span>samples: {{ version.training_data_size }}</span>
        </div>
      </div>
    </div>
    <el-pagination
      v-if="totalPages > 1"
      :current-page="currentPage"
      small
      layout="prev, pager, next"
      :page-size="versionPagination?.limit ?? 50"
      :total="versionPagination?.total ?? 0"
      class="wm-list-panel__pager"
      @current-change="handlePageChange"
    />
  </section>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { WorldModelVersion } from '@/contracts/world_model'

const { t } = useI18n()

defineProps<{
  versions: WorldModelVersion[]
  currentVersion: WorldModelVersion | null
  versionsLoading: boolean
  totalPages: number
  versionPagination: { total: number; limit: number } | null
  currentPage: number
}>()

const emit = defineEmits<{
  'select-version': [version: string]
  'update:current-page': [page: number]
}>()

function handleSelect(version: string): void {
  emit('select-version', version)
}

function handlePageChange(page: number): void {
  emit('update:current-page', page)
}
</script>

<style scoped>
.wm-list-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-md);
  padding: 12px;
  max-height: calc(100vh - 140px);
}

.wm-list-panel__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.wm-list-panel__title {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.wm-list-panel__body {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.wm-list-panel__pager {
  margin-top: 8px;
  justify-content: center;
}

.wm-version-card {
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.2s;
}

.wm-version-card:hover {
  border-color: var(--accent-primary);
  background: var(--accent-light);
}

.wm-version-card--active {
  border-color: var(--accent-primary);
  background: var(--accent-light);
  box-shadow: var(--shadow-ring);
}

.wm-version-card__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.wm-version-card__version {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.wm-version-card__desc {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wm-version-card__meta {
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}
</style>