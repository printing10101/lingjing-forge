<template>
  <section class="rl-list-panel">
    <div class="rl-list-panel__header">
      <span class="rl-list-panel__title">{{ t('rlAgent.policyVersions') }}</span>
      <el-tag v-if="store.versionPagination" size="small" type="info">
        {{ store.versionPagination.total }}
      </el-tag>
    </div>
    <div v-loading="store.versionsLoading" class="rl-list-panel__body">
      <el-empty
        v-if="!store.versionsLoading && !store.hasVersions"
        :description="t('rlAgent.emptyVersions')"
      />
      <div
        v-for="version in store.versions"
        :key="version.version"
        class="rl-version-card"
        :class="{ 'rl-version-card--active': store.currentVersion?.version === version.version }"
        @click="$emit('selectVersion', version.version)"
      >
        <div class="rl-version-card__header">
          <span class="rl-version-card__version">v{{ version.version }}</span>
          <el-tag v-if="version.is_active" type="success" size="small">
            {{ t('rlAgent.active') }}
          </el-tag>
        </div>
        <div class="rl-version-card__desc">{{ version.description || '\u2014' }}</div>
        <div class="rl-version-card__meta">
          <el-tag size="small" :type="POLICY_ALGORITHM_TAG_TYPE[version.algorithm]">
            {{ POLICY_ALGORITHM_LABELS[version.algorithm] }}
          </el-tag>
          <span>eps: {{ version.training_episodes }}</span>
        </div>
      </div>
    </div>
    <el-pagination
      v-if="store.totalPages > 1"
      v-model:current-page="currentPage"
      small
      layout="prev, pager, next"
      :page-size="store.versionPagination?.limit ?? 50"
      :total="store.versionPagination?.total ?? 0"
      class="rl-list-panel__pager"
      @current-change="handlePageChange"
    />
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRlAgentStore } from '@/stores/rlAgent'
import {
  POLICY_ALGORITHM_LABELS,
  POLICY_ALGORITHM_TAG_TYPE,
} from '@/contracts/rl_agent'

const { t } = useI18n()
const store = useRlAgentStore()
const currentPage = ref(1)

defineEmits<{ selectVersion: [version: string] }>()

function handlePageChange(page: number): void {
  currentPage.value = page
  store.fetchVersions({ limit: 50, offset: (page - 1) * 50 })
}
</script>

<style scoped>
.rl-list-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-md);
  padding: 12px;
  max-height: calc(100vh - 140px);
}

.rl-list-panel__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.rl-list-panel__title {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.rl-list-panel__body {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rl-list-panel__pager {
  margin-top: 8px;
  justify-content: center;
}

.rl-version-card {
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.2s;
}

.rl-version-card:hover {
  border-color: var(--accent-primary);
  background: var(--accent-light);
}

.rl-version-card--active {
  border-color: var(--accent-primary);
  background: var(--accent-light);
  box-shadow: var(--shadow-ring);
}

.rl-version-card__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.rl-version-card__version {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.rl-version-card__desc {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rl-version-card__meta {
  display: flex;
  gap: 8px;
  align-items: center;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}
</style>
