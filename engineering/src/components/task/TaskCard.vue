<template>
  <div
    class="task-card"
    :class="`priority-${priority}`"
    @click="$emit('click')"
  >
    <div class="task-card-header">
      <span class="task-type-tag">{{ task.task_type }}</span>
      <span class="task-date">{{ formatDate(task.created_at) }}</span>
    </div>
    <div class="task-title">{{ task.job_id }}</div>
    <div
      v-if="paramDesc"
      class="task-desc"
    >
      {{ paramDesc }}
    </div>
    <div
      v-if="task.error"
      class="task-error"
    >
      <el-icon :size="14"><CircleCloseFilled /></el-icon>
      {{ truncate(task.error, 60) }}
    </div>
    <div class="task-footer">
      <div class="task-footer-left">
        <div
          class="avatar"
          :style="{ backgroundColor: avatarColor(task.owner_id || '') }"
        >
          {{ (task.owner_id || '?').charAt(0).toUpperCase() }}
        </div>
        <span class="owner-name">{{ task.owner_id || '-' }}</span>
      </div>
      <div
        v-if="task.status === 'running'"
        class="task-progress"
      >
        <el-progress
          :percentage="Math.round(task.progress)"
          :stroke-width="6"
          :show-text="false"
        />
        <span class="progress-text">{{ Math.round(task.progress) }}%</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { CircleCloseFilled } from '@element-plus/icons-vue'
import type { TaskInfo } from '@/stores/tasks'

defineProps<{ task: TaskInfo; paramDesc: string; priority: string }>()
defineEmits<{
  click: []
  'status-change': [payload: { task: TaskInfo; newStatus: string }]
}>()

function formatDate(iso: string | null): string {
  if (!iso) return ''
  try { return new Date(iso).toLocaleDateString('zh-CN') } catch { return iso }
}

function truncate(s: string, len: number): string {
  return s.length > len ? s.slice(0, len) + '…' : s
}

function avatarColor(id: string): string {
  const colors = ['#007AFF', '#34C759', '#FF9500', '#FF3B30', '#8F8670', '#B37FEB']
  let hash = 0
  for (let i = 0; i < id.length; i++) hash = id.charCodeAt(i) + ((hash << 5) - hash)
  return colors[Math.abs(hash) % colors.length]
}
</script>

<style scoped>
.task-card {
  padding: 12px;
  margin-bottom: 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-sm);
  cursor: pointer;
  background: var(--el-bg-color);
  transition: all 0.2s;
}
.task-card:hover {
  border-color: var(--accent-primary);
  background: var(--accent-light);
}
.task-card.priority-high { border-left: 3px solid #FF3B30; }
.task-card.priority-medium { border-left: 3px solid #FF9500; }
.task-card.priority-low { border-left: 3px solid #007AFF; }
.task-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.task-type-tag {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: var(--radius-xs);
  background: var(--el-fill-color-darker);
  color: var(--el-text-color-secondary);
}
.task-date {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}
.task-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-desc {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 4px;
}
.task-error {
  font-size: 11px;
  color: var(--state-error);
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 4px;
}
.task-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.task-footer-left {
  display: flex;
  align-items: center;
  gap: 6px;
}
.owner-name {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.avatar {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  color: #fff;
  flex-shrink: 0;
}
.task-progress {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 50%;
}
.progress-text {
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
</style>