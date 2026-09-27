<template>
  <div class="settings-page">
    <div class="page-header">
      <div class="page-header__title">
        <h1>{{ t('settings.pageTitle') }}</h1>
        <p class="subtitle">
          {{ t('settings.subtitle') }}
        </p>
      </div>
    </div>

    <div class="settings-layout">
      <div class="settings-nav">
        <div
          v-for="tab in tabs"
          :key="tab.key"
          class="settings-nav__item"
          :class="{ 'settings-nav__item--active': activeTab === tab.key }"
          @click="activeTab = tab.key"
        >
          <el-icon class="settings-nav__icon">
            <component :is="tab.icon" />
          </el-icon>
          <span class="settings-nav__label">{{ tab.label }}</span>
        </div>
      </div>
      <div class="settings-content">
        <KeepAlive>
          <component :is="currentComponent" />
        </KeepAlive>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, defineAsyncComponent, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { Setting, Cpu, Document, Connection, Service } from '@element-plus/icons-vue'
import GeneralSettings from '@/components/settings/GeneralSettings.vue'
import AISettings from '@/components/settings/AISettings.vue'
import ProcessSettings from '@/components/settings/ProcessSettings.vue'
import UISettings from '@/components/settings/UISettings.vue'
import LLMEngineSettings from '@/components/settings/LLMEngineSettings.vue'
import { useExtensionRegistry } from '@/composables/useExtensionRegistry'

const { t } = useI18n()
const activeTab = ref('general')

// 扩展点：合并插件通过 settings.tab 贡献的设置页签
// 插件贡献：component_url（面板组件）+ metadata.{title, icon}
const { listComputed } = useExtensionRegistry()
const pluginTabs = listComputed('settings.tab')

const componentMap: Record<string, Component> = {
  general: GeneralSettings,
  ai: AISettings,
  engine: LLMEngineSettings,
  process: ProcessSettings,
  ui: UISettings,
}

const currentComponent = computed(() => {
  // 插件 tab：组件通过扩展点加载器异步解析（defineAsyncComponent 包装 loader）
  const plugin = pluginTabs.value.find((p) => p.plugin_id === activeTab.value)
  if (plugin && plugin.component_loader) {
    return defineAsyncComponent(plugin.component_loader as () => Promise<Component>)
  }
  return componentMap[activeTab.value] || GeneralSettings
})

const tabs = [
  { key: 'general', label: t('settings.navGeneral'), icon: Setting },
  { key: 'ai', label: t('settings.navAiMonitor'), icon: Cpu },
  { key: 'engine', label: t('settings.navAiEngine'), icon: Service },
  { key: 'process', label: t('settings.navLogAudit'), icon: Document },
  { key: 'ui', label: t('settings.navAdvanced'), icon: Connection },
  // 插件贡献的设置页签（动态图标组件）
  ...pluginTabs.value.map((p) => ({
    key: p.plugin_id,
    label: (p.metadata?.title as string) || p.plugin_id,
    icon: (p.metadata?.icon as string) || Setting,
  })),
]
</script>

<style scoped>
.settings-page {
  max-width: var(--content-max-width);
  margin: 0 auto;
  padding: var(--page-padding);
}

.settings-layout {
  display: flex;
  gap: 24px;
  align-items: flex-start;
}

.settings-nav {
  width: 200px;
  flex-shrink: 0;
  background: var(--bg-0);
  border: 1px solid var(--bg-200);
  border-radius: var(--radius-lg);
  padding: 8px;
  position: sticky;
  top: 24px;
}

.settings-nav__item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 0.2s ease;
  color: var(--text-secondary);
  font-size: 14px;
  margin-bottom: 2px;
}

.settings-nav__item:hover {
  background: var(--bg-50);
  color: var(--text-primary);
}

.settings-nav__item--active {
  background: var(--brand-50);
  color: var(--brand-600, var(--brand-500));
  font-weight: 600;
}

.settings-nav__icon {
  font-size: 18px;
  flex-shrink: 0;
}

.settings-nav__label {
  white-space: nowrap;
}

.settings-content {
  flex: 1;
  min-width: 0;
}

/* Deep overrides for child components */
.settings-content :deep(.el-button) {
  border-radius: var(--radius-md);
}

.settings-content :deep(.el-switch) {
  --el-switch-on-color: var(--brand-500);
}

.settings-content :deep(.el-form-item__label) {
  color: var(--text-secondary);
  font-weight: 500;
}

.settings-content :deep(.el-descriptions__label) {
  color: var(--text-secondary);
  font-weight: 500;
}

.settings-content :deep(.el-descriptions__content) {
  color: var(--text-primary);
}

.settings-content :deep(.el-table) {
  --el-table-border-color: var(--bg-100);
  --el-table-header-bg-color: var(--bg-50);
  --el-table-header-text-color: var(--text-secondary);
  --el-table-text-color: var(--text-primary);
  --el-table-row-hover-bg-color: var(--bg-50);
}

.settings-content :deep(.el-dialog) {
  border-radius: var(--radius-lg);
}

.settings-content :deep(.el-pagination) {
  --el-pagination-button-bg-color: var(--bg-0);
}

.settings-content :deep(.el-alert) {
  border-radius: var(--radius-md);
}

.settings-content :deep(.el-progress-bar__outer) {
  background-color: var(--bg-100);
}
</style>
