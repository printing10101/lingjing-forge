<template>
  <div class="workflow-list-panel">
    <div class="panel-header">
      <span class="panel-title">{{ t('workflowPanel.listTitle') }}</span>
      <div class="panel-filter">
        <el-select
          :model-value="statusFilter"
          size="small"
          :placeholder="t('workflowPanel.filterStatusPlaceholder')"
          clearable
          style="width: 120px"
          @update:model-value="$emit('update:statusFilter', $event)"
        >
          <el-option
            v-for="opt in statusOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </div>
    </div>

    <div
      v-loading="loading"
      class="workflow-list-body"
    >
      <el-empty
        v-if="!loading && workflows.length === 0"
        :description="t('workflowPanel.emptyNoWorkflows')"
        :image-size="60"
      />
      <div
        v-for="wf in workflows"
        :key="wf.id"
        class="workflow-card"
        :class="{ active: wf.id === currentRunId }"
        @click="$emit('select', wf.id)"
      >
        <div class="workflow-card-header">
          <span class="workflow-name">{{ wf.name }}</span>
          <el-tag
            :type="statusTagType(wf.status)"
            size="small"
            effect="light"
          >
            {{ statusLabel(wf.status) }}
          </el-tag>
        </div>
        <div class="workflow-card-meta">
          <span class="meta-item mono">{{ wf.id.slice(0, 12) }}…</span>
          <span class="meta-item">v{{ wf.version }}</span>
        </div>
        <div class="workflow-card-footer">
          <span class="meta-item">
            {{ t('workflowPanel.nodesCount') }}: {{ wf.spec?.nodes?.length ?? 0 }}
          </span>
          <span
            v-if="wf.created_at"
            class="meta-item"
          >
            {{ formatTime(wf.created_at) }}
          </span>
        </div>
      </div>
    </div>

    <div class="workflow-list-footer">
      <el-pagination
        :current-page="currentPage"
        :page-size="pageSize"
        :total="totalCount"
        layout="prev, pager, next"
        small
        @current-change="$emit('update:currentPage', $event)"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { getTaskStatusTagType, getTaskStatusLabel } from '@/utils/statusHelpers'
import type { WorkflowRunRecord } from '@/composables/useWorkflow'

const { t } = useI18n()

defineProps<{
  workflows: WorkflowRunRecord[]
  loading: boolean
  statusFilter: string
  statusOptions: Array<{ value: string; label: string }>
  currentPage: number
  pageSize: number
  totalCount: number
  currentRunId: string | null
}>()

defineEmits<{
  'update:statusFilter': [value: string]
  select: [id: string]
  'update:currentPage': [page: number]
}>()

function statusTagType(s?: string | null) {
  return getTaskStatusTagType(s ?? '')
}

function statusLabel(s?: string | null): string {
  return getTaskStatusLabel(s ?? '') || '-'
}

function formatTime(s: string | null): string {
  if (!s) return '-'
  try {
    const d = new Date(s)
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return s
  }
}
</script>

<style scoped>
.workflow-list-panel {
  display: flex;
  flex-direction: column;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
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
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.workflow-list-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}
.workflow-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-xs);
  padding: 8px 10px;
  margin-bottom: 6px;
  cursor: pointer;
  transition: all 0.2s;
}
.workflow-card:hover {
  border-color: var(--brand-300);
  background: var(--el-fill-color-light);
}
.workflow-card.active {
  border-color: var(--accent-primary);
  background: var(--accent-light);
}
.workflow-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 6px;
}
.workflow-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.workflow-card-meta,
.workflow-card-footer {
  display: flex;
  justify-content: space-between;
  margin-top: 4px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.meta-item {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.mono {
  font-family: var(--font-mono);
}
.workflow-list-footer {
  padding: 6px 8px;
  border-top: 1px solid var(--el-border-color-lighter);
  display: flex;
  justify-content: center;
}
</style>