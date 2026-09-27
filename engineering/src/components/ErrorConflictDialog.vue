<template>
  <Teleport to="body">
    <Transition name="dialog-fade">
      <div
        v-if="visible"
        class="conflict-overlay"
        @click.self="handleClose"
      >
        <div class="conflict-dialog">
          <div
            class="dialog-header"
            :class="severityClass"
          >
            <div class="header-icon">
              <el-icon :size="24">
                <WarningFilled />
              </el-icon>
            </div>
            <h3>{{ dialogData.title }}</h3>
          </div>

          <div class="dialog-body">
            <div class="error-code">
              <el-tag
                :type="severityTag"
                size="small"
              >
                {{ dialogData.severity }}
              </el-tag>
              <span class="code-text">{{ dialogData.code }}</span>
            </div>

            <div class="message-section">
              <p class="error-message">
                {{ dialogData.message }}
              </p>
            </div>

            <div
              v-if="dialogData.detail"
              class="detail-section"
            >
              <h4>{{ $t('errorConflict.detailTitle') }}</h4>
              <p>{{ dialogData.detail }}</p>
            </div>

            <div
              v-if="dialogData.suggestion"
              class="suggestion-section"
            >
              <h4>
                <el-icon><Opportunity /></el-icon>
                {{ $t('errorConflict.solutionTitle') }}
              </h4>
              <p class="suggestion-text">
                {{ dialogData.suggestion }}
              </p>
            </div>
          </div>

          <div class="dialog-footer">
            <el-button
              type="primary"
              @click="handleClose"
            >
              {{ $t('errorConflict.acknowledged') }}
            </el-button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { WarningFilled, Opportunity } from '@element-plus/icons-vue'
import { useErrorBus, type ErrorDialogPayload } from '@/composables/useErrorBus'

const visible = ref(false)
const dialogData = ref<ErrorDialogPayload>({
  title: '',
  code: '',
  message: '',
  severity: 'error',
  detail: '',
  suggestion: '',
  recoverable: false,
})

const severityClass = computed(() => {
  switch (dialogData.value.severity) {
    case 'critical': return 'severity-critical'
    case 'error': return 'severity-error'
    case 'warning': return 'severity-warning'
    default: return 'severity-error'
  }
})

const severityTag = computed(() => {
  switch (dialogData.value.severity) {
    case 'critical': return 'danger'
    case 'error': return 'danger'
    case 'warning': return 'warning'
    default: return 'danger'
  }
})

function handleError(payload: ErrorDialogPayload) {
  dialogData.value = payload
  visible.value = true
}

function handleClose() {
  visible.value = false
}

// 修复：使用类型安全的 useErrorBus 替代 window.addEventListener('manufacturing-error')，
// 避免事件名拼写错误、payload 类型无法校验等问题；订阅生命周期由 composable 内部管理。
useErrorBus().on(handleError)
</script>

<style lang="scss" scoped>
.conflict-overlay {
  position: fixed;
  inset: 0;
  background: var(--bg-overlay);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 3000;
}

.conflict-dialog {
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  width: 540px;
  max-width: 90vw;
  max-height: 80vh;
  overflow-y: auto;
  box-shadow: var(--shadow-xl);
}

.dialog-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 20px 24px;
  border-radius: var(--radius-lg) var(--radius-lg) 0 0;
  color: var(--text-white);

  &.severity-critical {
    background: linear-gradient(135deg, var(--error), var(--error-dark));
  }
  &.severity-error {
    background: linear-gradient(135deg, var(--warning), var(--warning-dark));
  }
  &.severity-warning {
    background: linear-gradient(135deg, var(--warning), var(--warning-light));
  }

  .header-icon {
    flex-shrink: 0;
  }

  h3 {
    margin: 0;
    font-size: 18px;
    font-weight: 600;
  }
}

.dialog-body {
  padding: 20px 24px;
}

.error-code {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;

  .code-text {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--text-tertiary);
  }
}

.message-section {
  margin-bottom: 16px;

  .error-message {
    margin: 0;
    font-size: 15px;
    color: var(--text-primary);
    line-height: 1.6;
  }
}

.detail-section {
  margin-bottom: 16px;
  padding: 12px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-md);
  border-left: 3px solid var(--border-light);

  h4 {
    margin: 0 0 6px;
    font-size: 13px;
    color: var(--text-secondary);
  }

  p {
    margin: 0;
    font-size: 13px;
    color: var(--text-secondary);
    line-height: 1.6;
  }
}

.suggestion-section {
  padding: 14px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  border-left: 3px solid var(--accent-primary);

  h4 {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 0 0 8px;
    font-size: 14px;
    color: var(--accent-primary);
  }

  .suggestion-text {
    margin: 0;
    font-size: 13px;
    color: var(--text-primary);
    line-height: 1.7;
    white-space: pre-wrap;
  }
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  padding: 16px 24px;
  border-top: 1px solid var(--border-light);
}

.dialog-fade-enter-active,
.dialog-fade-leave-active {
  transition: opacity 0.25s ease;
}

.dialog-fade-enter-from,
.dialog-fade-leave-to {
  opacity: 0;
}
</style>
