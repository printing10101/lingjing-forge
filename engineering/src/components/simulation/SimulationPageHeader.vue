<template>
  <div class="page-header">
    <div class="page-header__title">
      <h1 class="page-title">
        {{ t('simulationPage.pageTitle') }}
      </h1>
      <p class="page-subtitle">
        {{ t('simulationPage.pageSubtitle') }}
      </p>
    </div>
    <div class="page-header__actions">
      <el-button
        size="small"
        :icon="Refresh"
        :loading="historyLoading"
        @click="emit('refresh')"
      >
        {{ t('simulationPage.refreshHistory') }}
      </el-button>
      <el-button
        type="primary"
        size="small"
        :icon="Plus"
        @click="emit('new-simulation')"
      >
        {{ t('simulationPage.newSimulation') }}
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Refresh, Plus } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

interface Props {
  historyLoading: boolean
}

defineProps<Props>()

const emit = defineEmits<{
  (e: 'refresh'): void
  (e: 'new-simulation'): void
}>()
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 20px;
}

.page-header__title {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.page-title {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0;
  line-height: 1.3;
}

.page-subtitle {
  font-size: 13px;
  color: var(--text-tertiary);
  margin: 0;
}

.page-header__actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
</style>