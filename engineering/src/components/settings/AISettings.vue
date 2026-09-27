<template>
  <div class="ai-settings">
    <AISovereigntyPanel v-model:show-sovereignty-intro="showSovereigntyIntro" />

    <SystemHealthMonitor />

    <!-- 系统健康检查 -->
    <div class="content-card">
      <div class="content-card__header">
        <span class="content-card__title">
          <el-icon style="margin-right: 6px;"><CircleCheck /></el-icon>
          {{ $t('settings.systemHealthCheck') }}
        </span>
        <span style="font-size: 12px; color: var(--text-tertiary);">{{ $t('settings.healthCheckDesc') }}</span>
      </div>
      <div class="content-card__body">
        <HealthCheck ref="healthCheckRef" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { CircleCheck } from '@element-plus/icons-vue'
import HealthCheck from '@/components/HealthCheck.vue'
import AISovereigntyPanel from './ai/AISovereigntyPanel.vue'
import SystemHealthMonitor from './ai/SystemHealthMonitor.vue'

const healthCheckRef = ref<InstanceType<typeof HealthCheck> | null>(null)

const showSovereigntyIntro = ref(true)

let healthCheckTimeoutId: number | null = null

onMounted(() => {
  healthCheckTimeoutId = window.setTimeout(() => {
    healthCheckRef.value?.runAllChecks()
  }, 300)
})

onBeforeUnmount(() => {
  if (healthCheckTimeoutId !== null) {
    clearTimeout(healthCheckTimeoutId)
    healthCheckTimeoutId = null
  }
})
</script>


