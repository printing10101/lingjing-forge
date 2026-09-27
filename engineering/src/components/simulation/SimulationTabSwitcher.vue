<template>
  <div class="content-card">
    <div class="content-card__body">
      <div class="sim-tabs">
        <div
          v-for="tab in tabs"
          :key="tab.key"
          :class="['sim-tab-item', { active: modelValue === tab.key }]"
          @click="emit('update:modelValue', tab.key)"
        >
          <el-icon
            :size="16"
            style="margin-right: 6px"
          >
            <component :is="tab.icon" />
          </el-icon>
          {{ tab.label }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { VideoPlay, Monitor, Download } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

interface Tab {
  key: string
  label: string
  icon: ReturnType<typeof Object>
}

const tabs: Tab[] = [
  { key: 'simulation', label: t('simulationPage.tabSimulation'), icon: VideoPlay },
  { key: 'fem', label: t('simulationPage.tabFem'), icon: Monitor },
  { key: 'export', label: t('simulationPage.tabExport'), icon: Download },
]

interface Props {
  modelValue: string
}

defineProps<Props>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()
</script>

<style scoped>
.content-card__body {
  padding: 4px;
}

.sim-tabs {
  display: flex;
  gap: 4px;
}

.sim-tab-item {
  display: flex;
  align-items: center;
  padding: 8px 20px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 14px;
  color: var(--text-secondary);
  transition: all 0.2s ease;
  user-select: none;
}

.sim-tab-item:hover {
  color: var(--text-primary);
  background: var(--bg-200);
}

.sim-tab-item.active {
  background: var(--accent-primary);
  color: var(--text-white);
  font-weight: 500;
}
</style>