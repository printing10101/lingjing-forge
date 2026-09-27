<template>
  <el-card
    class="agent-card"
    shadow="never"
  >
    <!-- Card Top: Icon + Agent ID + Status -->
    <div class="agent-card__header">
      <div class="agent-card__icon agent-card__icon--gradient">
        <el-icon
          :size="22"
          color="var(--text-white)"
        >
          <Monitor />
        </el-icon>
      </div>
      <div class="agent-card__info">
        <div class="agent-card__name">
          {{ formatAgentId(agent.agent_id) }}
        </div>
        <el-tag
          :type="agentStore.statusTagType(agent.status)"
          size="small"
          effect="light"
          round
        >
          {{ agentStore.statusLabel(agent.status) }}
        </el-tag>
      </div>
    </div>

    <!-- Current Task -->
    <div class="agent-card__desc">
      {{ t('agentDashboard.currentTask', { task: agent.current_task_id || t('agentDashboard.statusIdle') }) }}
    </div>

    <!-- Stats Row -->
    <div class="agent-card__stats">
      <div class="agent-card__stat">
        <span class="agent-card__stat-label">{{ t('agentDashboard.labelStatus') }}</span>
        <span class="agent-card__stat-value">{{ agentStore.statusLabel(agent.status) }}</span>
      </div>
      <div class="agent-card__stat">
        <span class="agent-card__stat-label">{{ t('agentDashboard.labelHeartbeat') }}</span>
        <span class="agent-card__stat-value">{{ agentStore.formatTime(agent.last_heartbeat) }}</span>
      </div>
      <div class="agent-card__stat">
        <span class="agent-card__stat-label">{{ t('agentDashboard.labelUpdated') }}</span>
        <span class="agent-card__stat-value">{{ agentStore.formatTime(agent.updated_at) }}</span>
      </div>
    </div>

    <!-- Action Buttons -->
    <div class="agent-card__actions">
      <el-button
        size="small"
        type="primary"
        text
        @click="$emit('action', { type: 'detail', agent })"
      >
        {{ t('agentDashboard.btnDetail') }}
      </el-button>
      <el-button
        size="small"
        type="success"
        text
        :disabled="agent.status === 'stopped'"
        @click="$emit('action', { type: 'resume', agent })"
      >
        {{ t('agentDashboard.btnRestart') }}
      </el-button>
      <el-button
        size="small"
        type="danger"
        text
        @click="$emit('action', { type: 'delete', agent })"
      >
        {{ t('agentDashboard.btnDelete') }}
      </el-button>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { Monitor } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import { useAgentStore } from '@/stores/agents'
import type { AgentSummary } from '@/stores/agents'

defineProps<{
  agent: AgentSummary
}>()

defineEmits<{
  action: [payload: { type: 'detail' | 'resume' | 'delete'; agent: AgentSummary }]
}>()

const { t } = useI18n()
const agentStore = useAgentStore()

function formatAgentId(id: string): string {
  if (!id) return '-'
  return id.charAt(0).toUpperCase() + id.slice(1)
}
</script>

<style scoped>
.agent-card {
  border-radius: var(--radius-lg) !important;
  box-shadow: var(--shadow-sm) !important;
  transition: box-shadow var(--transition-normal), transform var(--transition-normal);
}

.agent-card:hover {
  box-shadow: var(--shadow-md) !important;
  transform: translateY(-2px);
}

/* -- Card Header (icon + name + badge) -- */
.agent-card__header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}

.agent-card__icon {
  width: 42px;
  height: 42px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.agent-card__icon--gradient {
  background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary));
}

.agent-card__info {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.agent-card__name {
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* -- Description / Current Task -- */
.agent-card__desc {
  font-size: 0.8125rem;
  color: var(--text-tertiary);
  margin-bottom: 14px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* -- Stats Row -- */
.agent-card__stats {
  display: flex;
  gap: 20px;
  margin-bottom: 14px;
}

.agent-card__stat {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.agent-card__stat-label {
  font-size: 0.6875rem;
  color: var(--text-tertiary);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.agent-card__stat-value {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--text-primary);
  max-width: 120px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* -- Action Buttons -- */
.agent-card__actions {
  display: flex;
  gap: 4px;
  border-top: 1px solid var(--border-light);
  padding-top: 12px;
}
</style>