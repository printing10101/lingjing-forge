<template>
  <div class="content-card">
    <div class="content-card__header">
      <span class="content-card__title">
        <el-icon style="margin-right: 6px;"><Radar /></el-icon>
        {{ t('settings.autoDetect.title') }}
      </span>
      <div class="header-actions">
        <el-button
          size="small"
          :loading="store.detecting"
          @click="store.previewDetect"
        >
          <el-icon style="margin-right: 4px;">
            <Search />
          </el-icon>
          {{ t('settings.autoDetect.scanButton') }}
        </el-button>
        <el-button
          v-if="store.detected.length > 0"
          type="success"
          size="small"
          :loading="store.detecting"
          @click="handleImport"
        >
          <el-icon style="margin-right: 4px;">
            <Download />
          </el-icon>
          {{ t('settings.autoDetect.importButton') }}
        </el-button>
      </div>
    </div>
    <div class="content-card__body">
      <el-alert
        v-if="store.detected.length === 0 && !store.detecting"
        title="{{ t('settings.autoDetect.emptyTitle') }}"
        type="info"
        :closable="false"
        show-icon
      >
        <div>
          {{ t('settings.autoDetect.scanHintPrefix') }}"{{ t('settings.autoDetect.scanButton') }}"{{ t('settings.autoDetect.scanHintSuffix') }}
          {{ t('settings.autoDetect.scanMethodDesc') }}
        </div>
      </el-alert>

      <div
        v-if="store.detecting"
        v-loading="store.detecting"
        class="detect-loading"
        :element-loading-text="t('settings.autoDetect.scanningText')"
      />

      <template v-if="store.detected.length > 0 && !store.detecting">
        <div class="detect-summary">
          <el-tag
            type="success"
            size="small"
          >
            {{ t('settings.autoDetect.scanResult', { total: store.detected.length, hit: detectedCount }) }}
          </el-tag>
          <span
            v-if="store.lastDetectDuration"
            class="detect-duration"
          >
            {{ t('settings.autoDetect.duration', { ms: store.lastDetectDuration }) }}
          </span>
        </div>

        <el-table
          :data="store.detected"
          stripe
          size="small"
          style="width: 100%"
        >
          <el-table-column
            :label="t('settings.autoDetect.colStatus')"
            width="80"
          >
            <template #default="{ row }">
              <el-tag
                v-if="row.detected"
                type="success"
                size="small"
                effect="dark"
              >
                {{ t('settings.autoDetect.statusOnline') }}
              </el-tag>
              <el-tag
                v-else
                type="info"
                size="small"
                effect="plain"
              >
                {{ t('settings.autoDetect.statusOffline') }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column
            :label="t('settings.autoDetect.colType')"
            width="140"
          >
            <template #default="{ row }">
              <el-tag
                size="small"
                type="success"
                effect="plain"
              >
                {{ getLabel(row.provider_type) }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column
            :label="t('settings.autoDetect.colSuggestedId')"
            min-width="160"
          >
            <template #default="{ row }">
              <span class="mono-text">{{ row.provider_id }}</span>
            </template>
          </el-table-column>

          <el-table-column
            label="Base URL"
            min-width="220"
          >
            <template #default="{ row }">
              <span class="mono-text">{{ row.base_url || '-' }}</span>
            </template>
          </el-table-column>

          <el-table-column
            :label="t('settings.autoDetect.colDefaultModel')"
            min-width="140"
          >
            <template #default="{ row }">
              <span class="mono-text">{{ row.default_model || '-' }}</span>
            </template>
          </el-table-column>

          <el-table-column
            :label="t('settings.autoDetect.colDetectionMethod')"
            width="140"
          >
            <template #default="{ row }">
              <span class="text-muted">{{ row.detection_method }}</span>
            </template>
          </el-table-column>

          <el-table-column
            :label="t('settings.autoDetect.colDescription')"
            min-width="200"
          >
            <template #default="{ row }">
              <span class="text-muted">{{ row.detail }}</span>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { Search, Download } from '@element-plus/icons-vue'
import { useLLMProvidersStore } from '@/stores/llmProviders'
import { PROVIDER_TYPE_META } from '@/api/llmProviders'
import type { ProviderType } from '@/types/llmProvider'

const { t } = useI18n()
const store = useLLMProvidersStore()

const detectedCount = computed(() => store.detected.filter((d) => d.detected).length)

function getLabel(type: ProviderType): string {
  return PROVIDER_TYPE_META[type]?.label ?? type
}

async function handleImport(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      t('settings.autoDetect.importConfirmMessage', { action: t('settings.autoDetect.importButton') }),
      t('settings.autoDetect.importConfirmTitle'),
      {
        confirmButtonText: t('settings.autoDetect.import'),
        cancelButtonText: t('settings.autoDetect.cancel'),
        type: 'info',
      },
    )
    await store.importDetectedProviders()
  } catch {
    // 用户取消
  }
}
</script>

<style scoped>
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
  gap: 8px;
}

.content-card__body {
  padding: 20px;
}

.detect-loading {
  height: 120px;
}

.detect-summary {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}

.detect-duration {
  font-size: 12px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

.mono-text {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-secondary);
  word-break: break-all;
}

.text-muted {
  color: var(--text-tertiary);
  font-size: 12px;
}
</style>
