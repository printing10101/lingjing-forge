<template>
  <div class="panel panel-alerts">
    <div class="panel-header">
      <h3 class="panel-title">
        {{ t('home.cardRealTimeAlerts') }}
      </h3>
      <el-badge
        :value="alerts.length"
        class="alert-badge"
      />
    </div>
    <div class="alert-list">
      <div
        v-if="loading"
        class="alert-empty"
      >
        {{ t('home.msgAlertsLoading') }}
      </div>
      <div
        v-else-if="error"
        class="alert-empty"
      >
        {{ t('home.msgAlertsLoadFailed') }}
      </div>
      <div
        v-else-if="alerts.length === 0"
        class="alert-empty"
      >
        {{ t('home.msgNoAlerts') }}
      </div>
      <template v-else>
        <div
          v-for="alert in alerts"
          :key="alert.time + alert.message"
          class="alert-item"
        >
          <span
            class="alert-dot"
            :style="{ background: alert.severityColor }"
          />
          <div class="alert-content">
            <span class="alert-message">{{ alert.message }}</span>
            <span class="alert-time">{{ alert.time }}</span>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script lang="ts">
export interface AlertItem {
  message: string
  severityColor: string
  time: string
}
</script>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

defineProps<{
  alerts: AlertItem[]
  loading: boolean
  error: boolean
}>()

const { t } = useI18n()
</script>

<style scoped>
.panel {
  background: var(--bg-0);
  border: 1px solid var(--bg-200);
  border-radius: var(--radius-lg);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  border-bottom: 1px solid var(--bg-100);
  flex-shrink: 0;
}

.panel-title {
  margin: 0;
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--text-primary);
}

.alert-badge {
  flex-shrink: 0;
}

.alert-list {
  padding: 4px 20px 12px;
  display: flex;
  flex-direction: column;
  gap: 0;
  flex: 1;
  overflow-y: auto;
  max-height: 320px;
}

.alert-empty {
  padding: 32px 0;
  text-align: center;
  font-size: 0.825rem;
  color: var(--text-tertiary);
}

.alert-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid var(--bg-100);
  transition: background-color var(--transition-fast);
}

.alert-item:last-child {
  border-bottom: none;
}

.alert-item:hover {
  background-color: var(--bg-50);
  margin: 0 -20px;
  padding: 10px 20px;
}

.alert-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 5px;
}

.alert-content {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.alert-message {
  font-size: 0.825rem;
  color: var(--text-primary);
  line-height: 1.45;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.alert-time {
  font-size: 0.75rem;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}
</style>