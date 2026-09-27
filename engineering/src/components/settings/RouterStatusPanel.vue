<template>
  <div class="content-card">
    <div class="content-card__header">
      <span class="content-card__title">
        <el-icon style="margin-right: 6px;"><Share /></el-icon>
        {{ t('settings.routerStatus.title') }}
      </span>
      <div class="header-actions">
        <el-button
          size="small"
          :loading="store.loading"
          circle
          @click="store.refreshStatus()"
        >
          <el-icon><Refresh /></el-icon>
        </el-button>
      </div>
    </div>
    <div class="content-card__body">
      <div
        v-if="!store.routerStatus"
        class="empty-state"
      >
        <el-empty
          :description="t('settings.routerStatus.emptyDescription')"
          :image-size="60"
        />
      </div>

      <template v-else>
        <!-- 关键指标 -->
        <div class="status-grid">
          <div class="status-item">
            <span class="status-item__label">{{ t('settings.routerStatus.currentStrategy') }}</span>
            <span class="status-item__value">
              <el-tag
                size="small"
                type="primary"
              >
                {{ strategyLabel(store.routerStatus.current_strategy) }}
              </el-tag>
            </span>
          </div>
          <div class="status-item">
            <span class="status-item__label">{{ t('settings.routerStatus.activeProvider') }}</span>
            <span class="status-item__value">
              <span
                v-if="store.routerStatus.active_provider_id"
                class="mono"
              >
                {{ store.routerStatus.active_provider_id }}
              </span>
              <span
                v-else
                class="empty-text"
              >{{ t('settings.routerStatus.notActivated') }}</span>
            </span>
          </div>
          <div class="status-item">
            <span class="status-item__label">{{ t('settings.routerStatus.availableProviders') }}</span>
            <span class="status-item__value">{{ store.routerStatus.available_providers }}</span>
          </div>
          <div class="status-item">
            <span class="status-item__label">{{ t('settings.routerStatus.totalLatencySamples') }}</span>
            <span class="status-item__value">{{ store.routerStatus.total_latency_samples }}</span>
          </div>
          <div class="status-item status-item--full">
            <span class="status-item__label">{{ t('settings.routerStatus.cacheHitRate') }}</span>
            <span class="status-item__value">
              <el-progress
                :percentage="Math.round(store.routerStatus.cache_hit_rate * 100)"
                :stroke-width="10"
                :status="cacheHitStatus"
                style="max-width: 320px;"
              />
              <span class="cache-text">{{ (store.routerStatus.cache_hit_rate * 100).toFixed(1) }}%</span>
            </span>
          </div>
          <div class="status-item status-item--full">
            <span class="status-item__label">{{ t('settings.routerStatus.fallbackChain') }}</span>
            <span class="status-item__value">
              <template v-if="store.routerStatus.fallback_chain.length > 0">
                <el-tag
                  v-for="(pid, idx) in store.routerStatus.fallback_chain"
                  :key="pid + idx"
                  size="small"
                  effect="plain"
                  class="fallback-tag"
                >
                  <span class="fallback-idx">{{ idx + 1 }}</span>
                  <span class="mono">{{ pid }}</span>
                </el-tag>
                <el-icon
                  v-if="store.routerStatus.fallback_chain.length > 1"
                  class="fallback-arrow"
                >
                  <Right />
                </el-icon>
              </template>
              <span
                v-else
                class="empty-text"
              >{{ t('settings.routerStatus.noFallbackChain') }}</span>
            </span>
          </div>
        </div>

        <!-- 策略说明 -->
        <el-alert
          :title="strategyMeta.description"
          type="info"
          :closable="false"
          show-icon
          class="strategy-alert"
        />

        <!-- 策略切换 -->
        <div class="strategy-switcher">
          <span class="strategy-switcher__label">{{ t('settings.routerStatus.switchStrategy') }}</span>
          <el-select
            v-model="selectedStrategy"
            size="small"
            :placeholder="t('settings.routerStatus.placeholderSelectStrategy')"
            style="width: 240px;"
            @change="onStrategyChange"
          >
            <el-option
              v-for="s in strategyOptions"
              :key="s.value"
              :label="s.label"
              :value="s.value"
            >
              <span>{{ s.label }}</span>
              <span class="option-desc">{{ s.description }}</span>
            </el-option>
          </el-select>
          <span class="strategy-switcher__hint">{{ t('settings.routerStatus.switchHint') }}</span>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Share, Refresh, Right } from '@element-plus/icons-vue'
import { useLLMProvidersStore } from '@/stores/llmProviders'
import { ROUTING_STRATEGY_META } from '@/api/llmProviders'
import type { RoutingStrategy } from '@/types/llmProvider'

const { t } = useI18n()
const store = useLLMProvidersStore()

const strategyOptions = ROUTING_STRATEGY_META
const selectedStrategy = ref<RoutingStrategy | ''>('')

// 初始化选中策略
watch(
  () => store.routerStatus?.current_strategy,
  (s) => {
    if (s) selectedStrategy.value = s
  },
  { immediate: true },
)

const strategyMeta = computed(() => {
  const s = store.routerStatus?.current_strategy
  if (!s) return { label: '-', description: t('settings.routerStatus.notLoadedYet') }
  const meta = ROUTING_STRATEGY_META.find((m) => m.value === s)
  return meta ?? { label: s, description: t('settings.routerStatus.unknownStrategy') }
})

const cacheHitStatus = computed(() => {
  const rate = store.routerStatus?.cache_hit_rate ?? 0
  if (rate >= 0.7) return 'success'
  if (rate >= 0.3) return 'warning'
  return 'exception'
})

function strategyLabel(s: RoutingStrategy): string {
  const meta = ROUTING_STRATEGY_META.find((m) => m.value === s)
  return meta?.label ?? s
}

function onStrategyChange(_val: RoutingStrategy | ''): void {
  // 当前后端未提供策略切换端点，仅做 UI 预留
  // 后续扩展：调用 api.setRoutingStrategy(val) 后刷新
  if (store.routerStatus && selectedStrategy.value) {
    // 还原显示，避免误导
    selectedStrategy.value = store.routerStatus.current_strategy
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
  align-items: center;
  gap: 8px;
}

.content-card__body {
  padding: 20px;
}

.empty-state {
  padding: 12px 0;
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 16px;
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
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.mono {
  font-family: var(--font-mono);
  font-size: 13px;
  font-weight: 400;
}

.empty-text {
  font-size: 13px;
  font-weight: 400;
  color: var(--text-tertiary);
  font-style: italic;
}

.cache-text {
  font-size: 12px;
  color: var(--text-secondary);
  font-weight: 500;
}

.fallback-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-right: 4px;
}

.fallback-idx {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--brand-500);
  color: var(--text-white);
  font-size: 10px;
  font-weight: 600;
}

.fallback-arrow {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-left: 4px;
}

.strategy-alert {
  margin-bottom: 12px;
}

.strategy-switcher {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: var(--bg-50);
  border-radius: var(--radius-md);
  border: 1px dashed var(--bg-200);
}

.strategy-switcher__label {
  font-size: 13px;
  color: var(--text-secondary);
  font-weight: 500;
}

.strategy-switcher__hint {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-left: auto;
}

.option-desc {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-left: 8px;
}

@media (max-width: 768px) {
  .status-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .status-item--full {
    grid-column: span 2;
  }
  .strategy-switcher {
    flex-wrap: wrap;
  }
  .strategy-switcher__hint {
    margin-left: 0;
    width: 100%;
  }
}
</style>
