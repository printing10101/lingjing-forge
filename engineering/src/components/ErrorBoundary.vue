<template>
  <div
    v-if="error"
    class="error-boundary"
  >
    <div class="error-content">
      <el-icon
        class="error-icon"
        :size="64"
      >
        <WarningFilled />
      </el-icon>
      <h2>{{ t('errorBoundary.title') }}</h2>
      <p class="error-message">
        {{ error.message || t('errorBoundary.unknownError') }}
      </p>
      <div class="error-actions">
        <el-button
          type="primary"
          @click="reload"
        >
          {{ t('errorBoundary.reload') }}
        </el-button>
        <el-button @click="goHome">
          {{ t('errorBoundary.goHome') }}
        </el-button>
      </div>
      <details
        v-if="error.stack"
        class="error-details"
      >
        <summary>{{ t('errorBoundary.details') }}</summary>
        <pre>{{ error.stack }}</pre>
      </details>
    </div>
  </div>
  <slot v-else />
</template>

<script setup lang="ts">
import { ref, onErrorCaptured } from 'vue'
import { useI18n } from 'vue-i18n'
import { WarningFilled } from '@element-plus/icons-vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

const { t } = useI18n()
const router = useRouter()
const error = ref<Error | null>(null)

onErrorCaptured((err: Error) => {
  error.value = err
  console.error('ErrorBoundary caught error:', err)
  
  // 上报错误到监控系统（预留接口）
  reportError(err)
  
  return false // 阻止错误继续传播
})

function reload() {
  window.location.reload()
}

function goHome() {
  error.value = null
  router.push('/')
  ElMessage.success(t('errorBoundary.returnedHome'))
}

function reportError(err: Error) {
  // 本地化错误日志：项目原则要求核心数据本地化，错误日志先写入浏览器控制台
  // 与 localStorage 历史记录，便于用户排查；未来可在此处接入远程监控服务
  console.error('[ErrorBoundary] uncaught error:', err)
  try {
    const key = 'error_boundary_history'
    const max = 20
    const list: unknown[] = JSON.parse(localStorage.getItem(key) || '[]')
    list.unshift({
      message: err.message,
      stack: err.stack?.slice(0, 2000),
      time: new Date().toISOString(),
    })
    localStorage.setItem(key, JSON.stringify(list.slice(0, max)))
  } catch {
    // localStorage 不可用（隐私模式等）时静默忽略
  }
}
</script>

<style scoped>
.error-boundary {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: linear-gradient(135deg, var(--bg-secondary) 0%, var(--bg-300) 100%);
  padding: 20px;
}

.error-content {
  background: white;
  border-radius: var(--radius-lg);
  padding: 48px;
  max-width: 600px;
  text-align: center;
  box-shadow: var(--shadow-xl);
}

.error-icon {
  color: var(--error);
  margin-bottom: 24px;
}

h2 {
  margin: 0 0 16px;
  color: var(--text-primary);
  font-size: 24px;
  font-weight: 600;
}

.error-message {
  margin: 0 0 32px;
  color: var(--text-secondary);
  font-size: 16px;
  line-height: 1.6;
}

.error-actions {
  display: flex;
  gap: 16px;
  justify-content: center;
  margin-bottom: 24px;
}

.error-details {
  margin-top: 24px;
  text-align: left;
}

.error-details summary {
  cursor: pointer;
  color: var(--text-tertiary);
  font-size: 14px;
  margin-bottom: 12px;
  user-select: none;
}

.error-details pre {
  background: var(--bg-secondary);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  padding: 16px;
  overflow-x: auto;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary);
  max-height: 300px;
}
</style>
