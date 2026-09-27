<template>
  <div class="llm-engine-settings">
    <!-- 顶部状态摘要 -->
    <div class="content-card">
      <div class="content-card__header">
        <span class="content-card__title">
          <el-icon style="margin-right: 6px;"><Cpu /></el-icon>
          {{ t('settings.llmEngine.aiEngineStatus') }}
        </span>
        <div class="header-actions">
          <el-tag
            :type="store.hasActiveProvider ? 'success' : 'warning'"
            size="small"
            effect="plain"
          >
            {{ store.hasActiveProvider ? t('settings.llmEngine.activated') : t('settings.llmEngine.notActivated') }}
          </el-tag>
          <el-tag
            :type="store.encryptionAvailable ? 'success' : 'info'"
            size="small"
            effect="plain"
          >
            {{ store.encryptionAvailable ? t('settings.llmEngine.encryptedStorage') : t('settings.llmEngine.plaintextStorage') }}
          </el-tag>
          <el-button
            size="small"
            :loading="store.loading"
            circle
            @click="store.loadAll()"
          >
            <el-icon><Refresh /></el-icon>
          </el-button>
        </div>
      </div>
      <div class="content-card__body">
        <div class="status-grid">
          <div class="status-item">
            <span class="status-item__label">{{ t('settings.llmEngine.totalProviders') }}</span>
            <span class="status-item__value">{{ store.status?.total ?? store.providers.length }}</span>
          </div>
          <div class="status-item">
            <span class="status-item__label">{{ t('settings.llmEngine.enabled') }}</span>
            <span class="status-item__value">{{ store.status?.enabled ?? store.enabledProviders.length }}</span>
          </div>
          <div class="status-item">
            <span class="status-item__label">{{ t('settings.llmEngine.localProviders') }}</span>
            <span class="status-item__value">{{ store.status?.local_count ?? store.localProviders.length }}</span>
          </div>
          <div class="status-item">
            <span class="status-item__label">{{ t('settings.llmEngine.cloudProviders') }}</span>
            <span class="status-item__value">{{ store.status?.cloud_count ?? store.cloudProviders.length }}</span>
          </div>
          <div class="status-item status-item--full">
            <span class="status-item__label">{{ t('settings.llmEngine.currentActiveProvider') }}</span>
            <span class="status-item__value">
              <el-tag
                v-if="store.activeProvider"
                :type="store.activeProvider.enabled ? 'success' : 'danger'"
                size="small"
              >
                {{ store.activeProvider.name }} ({{ store.activeProvider.provider_id }})
              </el-tag>
              <span
                v-else
                class="status-item__empty"
              >{{ t('settings.llmEngine.noActiveProvider') }}</span>
            </span>
          </div>
          <div class="status-item status-item--full">
            <span class="status-item__label">{{ t('settings.llmEngine.configStoragePath') }}</span>
            <span class="status-item__value status-item__value--mono">
              {{ store.status?.db_path ?? '-' }}
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- 自动探测面板 -->
    <AutoDetectPanel />

    <!-- Provider 列表 -->
    <div class="content-card">
      <div class="content-card__header">
        <span class="content-card__title">
          <el-icon style="margin-right: 6px;"><Connection /></el-icon>
          {{ t('settings.llmEngine.providerList') }}
        </span>
        <div class="header-actions">
          <el-button
            type="primary"
            size="small"
            @click="openCreateDialog"
          >
            <el-icon style="margin-right: 4px;">
              <Plus />
            </el-icon>
            {{ t('settings.llmEngine.addProvider') }}
          </el-button>
        </div>
      </div>
      <div class="content-card__body">
        <ProviderList
          :loading="store.loading"
          @edit="openEditDialog"
          @test="openTestDialog"
          @health="store.checkHealth"
          @activate="store.activateProvider"
          @enable="store.setEnabled"
          @delete="handleDelete"
          @view-models="openModelsDialog"
        />
      </div>
    </div>

    <!-- 路由器状态 -->
    <RouterStatusPanel />

    <!-- 新增/编辑对话框 -->
    <ProviderFormDialog
      v-model:visible="formDialogVisible"
      :mode="formMode"
      :provider="editingProvider"
      @saved="onFormSaved"
    />

    <!-- 模型列表对话框 -->
    <ModelsDialog
      v-model:visible="modelsDialogVisible"
      :provider="modelsProvider"
    />

    <!-- 调用测试对话框 -->
    <TestDialog
      v-model:visible="testDialogVisible"
      :provider="testProvider"
    />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { Cpu, Refresh, Connection, Plus } from '@element-plus/icons-vue'
import { useLLMProvidersStore } from '@/stores/llmProviders'
import type { LLMProvider } from '@/types/llmProvider'
import ProviderList from './ProviderList.vue'
import ProviderFormDialog from './ProviderFormDialog.vue'
import AutoDetectPanel from './AutoDetectPanel.vue'
import RouterStatusPanel from './RouterStatusPanel.vue'
import ModelsDialog from './ModelsDialog.vue'
import TestDialog from './TestDialog.vue'

const { t } = useI18n()
const store = useLLMProvidersStore()

const formDialogVisible = ref(false)
const formMode = ref<'create' | 'edit'>('create')
const editingProvider = ref<LLMProvider | null>(null)

const modelsDialogVisible = ref(false)
const modelsProvider = ref<LLMProvider | null>(null)

const testDialogVisible = ref(false)
const testProvider = ref<LLMProvider | null>(null)

function openCreateDialog(): void {
  formMode.value = 'create'
  editingProvider.value = null
  formDialogVisible.value = true
}

function openEditDialog(provider: LLMProvider): void {
  formMode.value = 'edit'
  editingProvider.value = provider
  formDialogVisible.value = true
}

function openModelsDialog(provider: LLMProvider): void {
  modelsProvider.value = provider
  modelsDialogVisible.value = true
}

function openTestDialog(provider: LLMProvider): void {
  testProvider.value = provider
  testDialogVisible.value = true
}

async function handleDelete(provider: LLMProvider): Promise<void> {
  try {
    await ElMessageBox.confirm(
      t('settings.llmEngine.confirmDeleteMessage', { name: provider.name, id: provider.provider_id }),
      t('settings.llmEngine.deleteConfirmTitle'),
      {
        confirmButtonText: t('settings.llmEngine.btnDelete'),
        cancelButtonText: t('settings.llmEngine.btnCancel'),
        type: 'warning',
        confirmButtonClass: 'el-button--danger',
      },
    )
    await store.deleteProvider(provider.provider_id)
  } catch {
    // 用户取消
  }
}

function onFormSaved(): void {
  formDialogVisible.value = false
}

onMounted(() => {
  store.loadAll()
})
</script>

<style scoped>
.llm-engine-settings {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.content-card {
  background: var(--bg-0);
  border: 1px solid var(--bg-200);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.content-card__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 20px;
  border-bottom: 1px solid var(--bg-100);
  background: var(--bg-50);
}

.content-card__title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: center;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.content-card__body {
  padding: 20px;
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

.status-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  background: var(--bg-50);
  border-radius: var(--radius-md);
  border: 1px solid var(--bg-100);
}

.status-item--full {
  grid-column: span 4;
}

.status-item__label {
  font-size: 12px;
  color: var(--text-tertiary);
}

.status-item__value {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 6px;
}

.status-item__value--mono {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 400;
  word-break: break-all;
}

.status-item__empty {
  font-size: 13px;
  font-weight: 400;
  color: var(--text-tertiary);
  font-style: italic;
}

@media (max-width: 768px) {
  .status-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .status-item--full {
    grid-column: span 2;
  }
}
</style>
