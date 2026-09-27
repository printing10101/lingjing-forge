<template>
  <div
    class="filter-panel-wrapper"
    :class="{ collapsed: !filterVisible }"
  >
    <div class="filter-bar">
      <div class="filter-row">
        <div class="filter-item">
          <span class="filter-label">{{ t('taskBoard.labelPriority') }}</span>
          <el-select
            :model-value="priority"
            :placeholder="t('taskBoard.placeholderAll')"
            size="small"
            style="width: 120px"
            clearable
            @update:model-value="$emit('update:priority', $event)"
          >
            <el-option
              :label="t('taskBoard.priorityHigh')"
              value="high"
            />
            <el-option
              :label="t('taskBoard.priorityMedium')"
              value="medium"
            />
            <el-option
              :label="t('taskBoard.priorityLow')"
              value="low"
            />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">{{ t('taskBoard.labelAssignee') }}</span>
          <el-select
            :model-value="assignee"
            :placeholder="t('taskBoard.placeholderAll')"
            size="small"
            style="width: 120px"
            clearable
            @update:model-value="$emit('update:assignee', $event)"
          >
            <el-option
              v-for="name in assigneeOptions"
              :key="name"
              :label="name"
              :value="name"
            />
          </el-select>
        </div>
        <div class="filter-item">
          <span class="filter-label">{{ t('taskBoard.labelDateRange') }}</span>
          <el-date-picker
            :model-value="dateRange"
            type="daterange"
            :range-separator="t('taskBoard.rangeSeparator')"
            :start-placeholder="t('taskBoard.placeholderStartDate')"
            :end-placeholder="t('taskBoard.placeholderEndDate')"
            size="small"
            style="width: 260px"
            value-format="YYYY-MM-DD"
            @update:model-value="$emit('update:dateRange', $event)"
          />
        </div>
        <div class="filter-item">
          <span class="filter-label">{{ t('taskBoard.labelTaskType') }}</span>
          <el-select
            :model-value="taskType"
            :placeholder="t('taskBoard.placeholderAll')"
            size="small"
            style="width: 140px"
            clearable
            @update:model-value="$emit('update:taskType', $event)"
          >
            <el-option
              v-for="opt in taskTypeOptions"
              :key="opt"
              :label="opt"
              :value="opt"
            />
          </el-select>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

defineProps<{
  priority: string
  assignee: string
  dateRange: [string, string] | null
  taskType: string
  assigneeOptions: string[]
  taskTypeOptions: string[]
  filterVisible: boolean
}>()

defineEmits<{
  'update:priority': [value: string]
  'update:assignee': [value: string]
  'update:dateRange': [value: [string, string] | null]
  'update:taskType': [value: string]
  'update:filterVisible': [value: boolean]
}>()
</script>

<style scoped>
.filter-panel-wrapper {
  max-height: 120px;
  overflow: hidden;
  margin-bottom: 24px;
  transition: max-height 0.35s cubic-bezier(0.4, 0, 0.2, 1),
    margin-bottom 0.35s cubic-bezier(0.4, 0, 0.2, 1),
    opacity 0.25s ease;
  opacity: 1;
}

.filter-panel-wrapper.collapsed {
  max-height: 0;
  margin-bottom: 0;
  opacity: 0;
}

.filter-bar {
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  padding: 16px 20px;
  box-shadow: var(--shadow-sm);
}

.filter-row {
  display: flex;
  align-items: flex-end;
  gap: 20px;
  flex-wrap: wrap;
}

.filter-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.filter-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}
</style>