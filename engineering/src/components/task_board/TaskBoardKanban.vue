<template>
  <div class="kanban-container">
    <div class="kanban-board">
      <div
        v-for="column in columns"
        :key="column.key"
        class="kanban-column"
      >
        <div class="column-header">
          <div class="column-header-left">
            <span
              class="column-dot"
              :class="`dot-${column.key}`"
            />
            <span class="column-name">{{ column.label }}</span>
          </div>
          <span
            class="column-badge"
            :class="`badge-${column.key}`"
          >
            {{ column.items.length }}
          </span>
        </div>
        <div class="column-body">
          <TaskCard
            v-for="task in column.items"
            :key="task.job_id"
            :task="task"
            :param-desc="getParamDesc(task)"
            :priority="mapPriority(task)"
            @click="$emit('openDetail', task)"
          />
          <div
            v-if="column.items.length === 0"
            class="column-empty"
          >
            {{ t('taskBoard.emptyColumn') }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script lang="ts">
import type { TaskInfo } from '@/stores/tasks'

export interface KanbanColumn {
  key: string
  label: string
  items: TaskInfo[]
}
</script>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import TaskCard from '@/components/task/TaskCard.vue'

const { t } = useI18n()

defineProps<{
  columns: KanbanColumn[]
}>()

defineEmits<{
  openDetail: [task: TaskInfo]
}>()

function mapPriority(task: TaskInfo): 'high' | 'medium' | 'low' {
  if (task.status === 'failed') return 'high'
  if (task.error) return 'high'
  const p = task.params?.priority as string | undefined
  if (p === 'high' || p === 'urgent') return 'high'
  if (p === 'low') return 'low'
  if (task.status === 'running') return 'medium'
  return 'medium'
}

function getParamDesc(task: TaskInfo): string {
  if (!task.params) return ''
  const entries = Object.entries(task.params).filter(
    ([k]) => !['priority', 'revision'].includes(k)
  )
  if (entries.length === 0) return ''
  return entries
    .map(([k, v]) => `${k}: ${v}`)
    .join(' | ')
}
</script>

<style scoped>
.kanban-container {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

.kanban-board {
  display: flex;
  gap: 16px;
  min-width: 900px;
}

.kanban-column {
  flex: 1 1 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-light);
}

.column-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px 12px;
  border-bottom: 1px solid var(--border-light);
}

.column-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.column-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.dot-pending { background: var(--bg-500); }
.dot-running { background: var(--brand-500); }
.dot-review { background: var(--warning); }
.dot-done { background: var(--success); }

.column-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.column-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 22px;
  padding: 0 6px;
  border-radius: var(--radius-lg);
  font-size: 12px;
  font-weight: 600;
  color: var(--text-white);
}

.badge-pending { background: var(--bg-500); }
.badge-running { background: var(--brand-500); }
.badge-review { background: var(--warning); }
.badge-done { background: var(--success); }

.column-body {
  flex: 1;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 120px;
  max-height: calc(100vh - 320px);
  overflow-y: auto;
}

.column-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  font-size: 13px;
  color: var(--text-tertiary);
}

@media (max-width: 1200px) {
  .kanban-board {
    min-width: 800px;
  }
}
</style>