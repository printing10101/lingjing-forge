<template>
  <div class="page-header">
    <div class="page-header__title">
      <h1>{{ t('workflowPanel.pageTitle') }}</h1>
      <span class="page-header__subtitle">
        {{ t('workflowPanel.pageSubtitle') }}
      </span>
    </div>
    <div class="page-header__actions">
      <el-button
        size="small"
        :icon="Refresh"
        :loading="loading"
        @click="$emit('refresh')"
      >
        {{ t('workflowPanel.btnRefresh') }}
      </el-button>
      <el-button
        type="primary"
        size="small"
        :icon="Plus"
        @click="$emit('openSubmit')"
      >
        {{ t('workflowPanel.btnSubmit') }}
      </el-button>
      <el-button
        v-if="canCancel"
        size="small"
        :icon="CircleClose"
        @click="$emit('cancelCurrent')"
      >
        {{ t('workflowPanel.btnCancel') }}
      </el-button>
      <el-button
        v-if="canResume"
        size="small"
        type="warning"
        :icon="VideoPlay"
        @click="$emit('openResume')"
      >
        {{ t('workflowPanel.btnResume') }}
      </el-button>
      <el-button
        v-if="!!currentRunId"
        size="small"
        type="danger"
        :icon="Delete"
        @click="$emit('deleteCurrent')"
      >
        {{ t('workflowPanel.btnDelete') }}
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import {
  Refresh,
  Plus,
  CircleClose,
  VideoPlay,
  Delete,
} from '@element-plus/icons-vue'

const { t } = useI18n()

defineProps<{
  loading: boolean
  canCancel: boolean
  canResume: boolean
  currentRunId: string | null
}>()

defineEmits<{
  refresh: []
  openSubmit: []
  cancelCurrent: []
  openResume: []
  deleteCurrent: []
}>()
</script>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  flex-shrink: 0;
}
.page-header__title h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.page-header__subtitle {
  display: block;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}
.page-header__actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>