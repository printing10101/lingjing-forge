<template>
  <div v-if="error" class="error-boundary-solo">
    <div class="error-content">
      <el-icon class="error-icon" :size="64">
        <WarningFilled />
      </el-icon>
      <h2>应用出错了</h2>
      <p class="error-message">{{ error.message || '未知错误' }}</p>
      <div class="error-actions">
        <el-button type="primary" @click="reload">
          刷新页面
        </el-button>
        <el-button @click="goHome">
          返回首页
        </el-button>
      </div>
      <details class="error-details" v-if="error.stack">
        <summary>错误详情</summary>
        <pre>{{ error.stack }}</pre>
      </details>
    </div>
  </div>
  <slot v-else />
</template>

<script setup lang="ts">
import { ref, onErrorCaptured } from 'vue';
import { useRouter } from 'vue-router';
import { WarningFilled } from '@element-plus/icons-vue';

const router = useRouter();
const error = ref<Error | null>(null);

onErrorCaptured((err: Error) => {
  error.value = err;
  console.error('ErrorBoundary caught error:', err);
  return false;
});

function reload() {
  window.location.reload();
}

function goHome() {
  error.value = null;
  router.push('/');
}
</script>

<style scoped>
.error-boundary-solo {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: linear-gradient(135deg, #1e1e1e 0%, #252526 100%);
  padding: 20px;
}

.error-content {
  background: #2d2d2e;
  border: 1px solid #333;
  border-radius: 8px;
  padding: 48px;
  max-width: 600px;
  text-align: center;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
}

.error-icon {
  color: #f14c4c;
  margin-bottom: 24px;
}

h2 {
  margin: 0 0 16px;
  color: #d4d4d4;
  font-size: 24px;
  font-weight: 600;
}

.error-message {
  margin: 0 0 32px;
  color: #888;
  font-size: 16px;
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
  color: #666;
  font-size: 14px;
  margin-bottom: 12px;
  user-select: none;
}

.error-details pre {
  background: #1e1e1e;
  border: 1px solid #333;
  border-radius: 4px;
  padding: 16px;
  overflow-x: auto;
  font-size: 12px;
  line-height: 1.5;
  color: #d4d4d4;
  max-height: 300px;
}
</style>
