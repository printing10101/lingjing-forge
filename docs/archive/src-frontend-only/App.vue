<template>
  <div id="app" class="app-container">
    <ErrorBoundary>
      <div v-if="!appReady" class="app-initializing">
        <el-icon class="is-loading" :size="28">
          <Loading />
        </el-icon>
        <span>{{ $t('status.loading', '正在加载...') }}</span>
      </div>
      
      <SoloWorkspace v-else />
    </ErrorBoundary>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useI18n } from 'vue-i18n';
import ErrorBoundary from './components/ErrorBoundary.vue';
import SoloWorkspace from './views/SoloWorkspace.vue';
import { Loading } from '@element-plus/icons-vue';

const { t } = useI18n();

// 应用初始化完成标志
const appReady = ref(false);

onMounted(async () => {
  // 模拟加载延迟以确保 UI 渲染
  await new Promise(resolve => setTimeout(resolve, 500));
  appReady.value = true;
});

</script>

<style>
#app {
  font-family: inherit;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  color: var(--text-primary);
  background-color: var(--bg-primary);
  height: 100vh;
  margin: 0;
  padding: 0;
}

.app-initializing {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100vh;
  gap: 16px;
  color: var(--text-tertiary);
  font-size: 14px;
}
</style>
