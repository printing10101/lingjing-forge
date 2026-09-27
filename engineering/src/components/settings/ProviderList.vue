<template>
  <div class="provider-list">
    <el-alert
      v-if="!loading && providers.length === 0"
      :title="t('settings.providerList.emptyTitle')"
      type="info"
      :closable="false"
      show-icon
    >
      <div>
        {{ t('settings.providerList.emptyDescription') }}
        
      </div>
    </el-alert>

    <el-table
      v-else
      v-loading="loading"
      :data="providers"
      row-key="provider_id"
      stripe
      style="width: 100%"
    >
      <el-table-column
        :label="t('settings.providerList.colStatus')"
        width="120"
      >
        <template #default="{ row }">
          <div class="status-cell">
            <el-tag
              v-if="row.is_active"
              type="success"
              size="small"
              effect="dark"
            >
              {{ t('settings.providerList.activate') }}
            </el-tag>
            <el-tag
              v-else-if="row.enabled"
              type="info"
              size="small"
              effect="plain"
            >
              {{ t('settings.providerList.enabled') }}
            </el-tag>
            <el-tag
              v-else
              type="info"
              size="small"
              effect="plain"
            >
              {{ t('settings.providerList.disabled') }}
            </el-tag>
            <span
              v-if="row.last_health_status"
              class="health-dot"
              :class="`health-dot--${row.last_health_status}`"
              :title="t('settings.providerList.lastHealthStatus', { status: row.last_health_status })"
            />
          </div>
        </template>
      </el-table-column>

      <el-table-column
        :label="t('settings.providerList.colName')"
        min-width="180"
      >
        <template #default="{ row }">
          <div class="name-cell">
            <span class="name-cell__name">{{ row.name }}</span>
            <span class="name-cell__id">{{ row.provider_id }}</span>
          </div>
        </template>
      </el-table-column>

      <el-table-column
        :label="t('settings.providerList.colType')"
        width="160"
      >
        <template #default="{ row }">
          <el-tag
            size="small"
            :type="getTypeTagType(row.provider_type)"
            effect="plain"
          >
            {{ getTypeLabel(row.provider_type) }}
          </el-tag>
          <span
            class="category-badge"
            :class="`category-badge--${getCategory(row.provider_type)}`"
          >
            {{ getCategory(row.provider_type) === 'local' ? t('settings.providerList.typeLocal') : t('settings.providerList.typeCloud') }}
          </span>
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
        :label="t('settings.providerList.colDefaultModel')"
        min-width="160"
      >
        <template #default="{ row }">
          <span class="mono-text">{{ row.default_model || '-' }}</span>
        </template>
      </el-table-column>

      <el-table-column
        label="API Key"
        width="100"
        align="center"
      >
        <template #default="{ row }">
          <el-icon
            v-if="row.api_key_set"
            class="key-set"
          >
            <CircleCheck />
          </el-icon>
          <span
            v-else
            class="key-unset"
          >-</span>
        </template>
      </el-table-column>

      <el-table-column
        :label="t('settings.providerList.colPriority')"
        width="90"
        align="center"
      >
        <template #default="{ row }">
          <span class="priority-badge">{{ row.priority }}</span>
        </template>
      </el-table-column>

      <el-table-column
        :label="t('settings.providerList.colLastLatency')"
        width="120"
        align="center"
      >
        <template #default="{ row }">
          <span
            v-if="row.last_latency_ms != null"
            class="latency-text"
            :class="getLatencyClass(row.last_latency_ms)"
          >
            {{ row.last_latency_ms }}ms
          </span>
          <span
            v-else
            class="text-muted"
          >-</span>
        </template>
      </el-table-column>

      <el-table-column
        :label="t('settings.providerList.colActions')"
        width="280"
        fixed="right"
      >
        <template #default="{ row }">
          <div class="action-buttons">
            <el-button
              v-if="!row.is_active"
              size="small"
              type="success"
              link
              :disabled="!row.enabled"
              @click="$emit('activate', row.provider_id)"
            >
              {{ t('settings.providerList.activate') }}
            </el-button>
            <el-button
              size="small"
              link
              :type="row.enabled ? 'warning' : 'primary'"
              @click="$emit('enable', row.provider_id, !row.enabled)"
            >
              {{ row.enabled ? t('settings.providerList.btnDisable') : t('settings.providerList.btnEnable') }}
            </el-button>
            <el-button
              size="small"
              link
              :loading="healthChecking[row.provider_id]"
              @click="$emit('health', row.provider_id)"
            >
              {{ t('settings.providerList.healthCheck') }}
            </el-button>
            <el-dropdown
              trigger="click"
              @command="(cmd: string) => handleCommand(cmd, row as LLMProvider)"
            >
              <el-button
                size="small"
                link
              >
                {{ t('settings.providerList.btnMore') }}<el-icon class="el-icon--right">
                  <ArrowDown />
                </el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="edit">
                    {{ t('settings.providerList.editConfig') }}
                  </el-dropdown-item>
                  <el-dropdown-item command="models">
                    {{ t('settings.providerList.viewModels') }}
                  </el-dropdown-item>
                  <el-dropdown-item command="test">
                    {{ t('settings.providerList.invokeTest') }}
                  </el-dropdown-item>
                  <el-dropdown-item
                    command="delete"
                    divided
                  >
                    <span style="color: var(--state-error)">{{ t('settings.providerList.btnDelete') }}</span>
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { CircleCheck, ArrowDown } from '@element-plus/icons-vue'
import { useLLMProvidersStore } from '@/stores/llmProviders'
import { PROVIDER_TYPE_META } from '@/api/llmProviders'
import type { LLMProvider, ProviderType } from '@/types/llmProvider'

const { t } = useI18n()

defineProps<{
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'edit', provider: LLMProvider): void
  (e: 'test', provider: LLMProvider): void
  (e: 'health', providerId: string): void
  (e: 'activate', providerId: string): void
  (e: 'enable', providerId: string, enabled: boolean): void
  (e: 'delete', provider: LLMProvider): void
  (e: 'view-models', provider: LLMProvider): void
}>()

const store = useLLMProvidersStore()

const providers = computed(() => store.providers)
const healthChecking = computed(() => store.healthChecking)

function getTypeLabel(type: ProviderType): string {
  return PROVIDER_TYPE_META[type]?.label ?? type
}

function getCategory(type: ProviderType): 'local' | 'cloud' {
  return PROVIDER_TYPE_META[type]?.category ?? 'cloud'
}

function getTypeTagType(type: ProviderType): 'success' | 'warning' | 'info' | 'primary' | 'danger' {
  const cat = getCategory(type)
  return cat === 'local' ? 'success' : 'warning'
}

function getLatencyClass(ms: number): string {
  if (ms < 500) return 'latency-good'
  if (ms < 2000) return 'latency-warn'
  return 'latency-bad'
}

function handleCommand(cmd: string, row: LLMProvider): void {
  switch (cmd) {
    case 'edit':
      emit('edit', row)
      break
    case 'models':
      emit('view-models', row)
      break
    case 'test':
      emit('test', row)
      break
    case 'delete':
      emit('delete', row)
      break
  }
}
</script>

<style scoped>
.provider-list {
  width: 100%;
}

.status-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.health-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}

.health-dot--healthy {
  background: var(--state-success);
  box-shadow: 0 0 4px var(--state-success);
}

.health-dot--unhealthy {
  background: var(--state-error);
}

.health-dot--unknown {
  background: var(--state-info);
}

.name-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.name-cell__name {
  font-weight: 600;
  color: var(--text-primary);
}

.name-cell__id {
  font-size: 11px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

.category-badge {
  margin-left: 6px;
  padding: 1px 6px;
  border-radius: var(--radius-2xs);
  font-size: 11px;
  font-weight: 500;
}

.category-badge--local {
  background: var(--state-success-bg);
  color: var(--success-dark);
}

.category-badge--cloud {
  background: var(--state-warning-bg);
  color: var(--warning-dark);
}

.mono-text {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-secondary);
  word-break: break-all;
}

.key-set {
  color: var(--state-success);
  font-size: 16px;
}

.key-unset {
  color: var(--text-tertiary);
}

.priority-badge {
  display: inline-block;
  min-width: 24px;
  padding: 2px 6px;
  border-radius: var(--radius-xs);
  background: var(--bg-100);
  font-size: 12px;
  font-weight: 600;
  text-align: center;
}

.latency-text {
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 600;
}

.latency-good {
  color: var(--state-success);
}

.latency-warn {
  color: var(--state-warning);
}

.latency-bad {
  color: var(--state-error);
}

.text-muted {
  color: var(--text-tertiary);
  font-size: 12px;
}

.action-buttons {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}
</style>
