<template>
  <transition name="slide-panel">
    <div
      v-if="visible"
      class="detail-overlay"
      @click.self="emit('close')"
    >
      <div class="detail-panel">
        <div class="detail-header">
          <h3 class="detail-title">
            {{ route?.name }}
          </h3>
          <el-button
            text
            circle
            @click="emit('close')"
          >
            <el-icon :size="18">
              <Close />
            </el-icon>
          </el-button>
        </div>

        <div class="detail-status-row">
          <el-tag
            :type="statusTagType(route?.status ?? '')"
            size="small"
            effect="light"
          >
            {{ route?.status }}
          </el-tag>
          <span class="detail-version">{{ route?.version }}</span>
        </div>

        <p class="detail-description">
          {{ route?.description }}
        </p>

        <div class="detail-section-label">
          {{ t('processPlanning.routePage.stepListLabel') }}
        </div>
        <div class="step-list">
          <div
            v-for="(step, index) in route?.steps ?? []"
            :key="step.name + (step.tool_id ?? '') + index"
            class="step-item"
          >
            <div class="step-number">
              {{ index + 1 }}
            </div>
            <div class="step-content">
              <div class="step-name">
                {{ step.name }}
              </div>
              <div class="step-duration">
                {{ step.duration }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { Close } from '@element-plus/icons-vue'

interface ProcessStep {
  name: string
  description: string
  duration: string
  tool_id?: number
  parameters?: Record<string, unknown>
}

interface ProcessRoute {
  id: number
  name: string
  description: string
  status: string
  version: string
  material_type: string
  steps: ProcessStep[]
  created_at: string
  updated_at: string
}

defineProps<{
  visible: boolean
  route: ProcessRoute | null
}>()

const emit = defineEmits<{
  close: []
}>()

const { t } = useI18n()

function statusTagType(status: string): 'success' | 'warning' | 'info' {
  switch (status) {
    case t('processPlanning.routePage.statusPublished'):
    case 'published':
      return 'success'
    case t('processPlanning.routePage.statusDraft'):
    case 'draft':
      return 'warning'
    case t('processPlanning.routePage.statusArchived'):
    case 'archived':
      return 'info'
    default:
      return 'info'
  }
}
</script>

<style scoped>
.detail-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1000;
  background: var(--bg-overlay-light);
  display: flex;
  justify-content: flex-end;
}

.detail-panel {
  width: 400px;
  max-width: 90vw;
  height: 100%;
  background: var(--bg-primary);
  box-shadow: var(--shadow-xl);
  padding: 28px 24px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.detail-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  padding-right: 8px;
}

.detail-status-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.detail-version {
  font-size: 13px;
  color: var(--text-tertiary);
  font-weight: 500;
}

.detail-description {
  margin: 0;
  font-size: 14px;
  color: var(--text-secondary);
  line-height: 1.7;
}

.detail-section-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-accent);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-top: 8px;
}

.step-list {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.step-item {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 14px 0;
  border-bottom: 1px solid var(--border-light);
}

.step-item:last-child {
  border-bottom: none;
}

.step-number {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--accent-light);
  color: var(--accent-primary);
  font-size: 13px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}

.step-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.step-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

.step-duration {
  font-size: 12px;
  color: var(--text-tertiary);
}

/* 侧滑动画 */
.slide-panel-enter-active,
.slide-panel-leave-active {
  transition: opacity var(--transition-normal);
}

.slide-panel-enter-active .detail-panel,
.slide-panel-leave-active .detail-panel {
  transition: transform var(--transition-normal);
}

.slide-panel-enter-from,
.slide-panel-leave-to {
  opacity: 0;
}

.slide-panel-enter-from .detail-panel,
.slide-panel-leave-to .detail-panel {
  transform: translateX(100%);
}
</style>