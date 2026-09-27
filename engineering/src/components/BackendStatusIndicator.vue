<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useBackendStatus, type BackendStatusKind } from '@/composables/useBackendStatus'

const { t } = useI18n()
const { state, restart, tauriMode } = useBackendStatus()

const indicator = computed(() => {
  const map: Record<BackendStatusKind, { color: string; label: string; pulse: boolean }> = {
    idle: { color: 'var(--text-tertiary)', label: t('backendStatus.idle'), pulse: false },
    starting: { color: 'var(--warning)', label: t('backendStatus.starting'), pulse: true },
    running: { color: 'var(--success)', label: t('backendStatus.running'), pulse: false },
    stopping: { color: 'var(--warning)', label: t('backendStatus.stopping'), pulse: true },
    crashed: { color: 'var(--error)', label: t('backendStatus.crashed'), pulse: true },
    failed: { color: 'var(--error)', label: t('backendStatus.failed'), pulse: false },
    stopped: { color: 'var(--text-tertiary)', label: t('backendStatus.stopped'), pulse: false },
  }
  return map[state.status] || map.idle
})
</script>

<template>
  <div
    v-if="tauriMode"
    class="backend-status-indicator"
    :title="state.message"
  >
    <span
      class="dot"
      :class="{ pulse: indicator.pulse }"
      :style="{ background: indicator.color }"
    />
    <span
      class="label"
      :style="{ color: indicator.color }"
    >{{ indicator.label }}</span>
    <el-button
      v-if="state.status === 'crashed' || state.status === 'failed'"
      link
      type="primary"
      size="small"
      @click="restart"
    >
      {{ t('backendStatus.restart') }}
    </el-button>
  </div>
</template>

<style scoped>
.backend-status-indicator {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px;
  font-size: 12px;
  user-select: none;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.dot.pulse {
  animation: pulse 1.4s ease-in-out infinite;
}
.label {
  font-weight: 500;
}
@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.5; transform: scale(1.3); }
}
</style>
