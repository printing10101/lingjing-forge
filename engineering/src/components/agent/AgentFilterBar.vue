<template>
  <div class="page-header">
    <div class="page-header__title">
      <h1>{{ t('agentDashboard.pageTitle') }}</h1>
      <p class="subtitle">
        {{ t('agentDashboard.pageSubtitle') }}
      </p>
    </div>
    <div class="page-header__actions">
      <el-select
        :model-value="statusFilter"
        :placeholder="t('agentDashboard.placeholderAllStatus')"
        size="small"
        style="width: 140px"
        @update:model-value="$emit('update:statusFilter', $event)"
      >
        <el-option
          :label="t('agentDashboard.statusAll')"
          value="all"
        />
        <el-option
          :label="t('agentDashboard.statusBusy')"
          value="busy"
        />
        <el-option
          :label="t('agentDashboard.statusIdle')"
          value="idle"
        />
        <el-option
          :label="t('agentDashboard.statusPaused')"
          value="paused"
        />
        <el-option
          :label="t('agentDashboard.statusError')"
          value="error"
        />
        <el-option
          :label="t('agentDashboard.statusStopped')"
          value="stopped"
        />
        <el-option
          :label="t('agentDashboard.statusRecovering')"
          value="recovering"
        />
      </el-select>
      <el-button
        type="primary"
        size="small"
        :icon="Plus"
        @click="$emit('deploy')"
      >
        {{ t('agentDashboard.btnDeployNew') }}
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Plus } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'

defineProps<{
  statusFilter: string
}>()

defineEmits<{
  'update:statusFilter': [value: string]
  deploy: []
}>()

const { t } = useI18n()
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
  flex-wrap: wrap;
  gap: 12px;
}

.page-header__title h1 {
  font-size: 1.5rem;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
}

.page-header__title .subtitle {
  font-size: 0.875rem;
  color: var(--text-tertiary);
  margin: 4px 0 0;
}

.page-header__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

@media (max-width: 640px) {
  .page-header {
    flex-direction: column;
    gap: 12px;
  }
}
</style>