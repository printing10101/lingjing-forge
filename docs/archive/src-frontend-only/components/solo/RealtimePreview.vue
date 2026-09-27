<template>
  <div class="realtime-preview">
    <!-- 预览面板顶部工具栏 -->
    <div class="preview-toolbar">
      <div class="toolbar-left">
        <h3 class="preview-title">
          <i class="fas fa-eye"></i>
          实时预览
        </h3>
        
        <!-- 设备切换 -->
        <div class="device-switcher">
          <button 
            v-for="device in devices" 
            :key="device.value"
            class="btn-device"
            :class="{ active: currentDevice === device.value }"
            @click="currentDevice = device.value"
          >
            <i :class="device.icon"></i>
            {{ device.label }}
          </button>
        </div>
      </div>
      
      <div class="toolbar-right">
        <button class="btn-refresh" @click="refreshPreview" :disabled="isRefreshing">
          <i :class="isRefreshing ? 'fas fa-spin fa-spinner' : 'fas fa-sync-alt'"></i>
          {{ isRefreshing ? '刷新中...' : '刷新' }}
        </button>
        <button class="btn-fullscreen" @click="toggleFullscreen">
          <i :class="isFullscreen ? 'fas fa-compress' : 'fas fa-expand'"></i>
        </button>
      </div>
    </div>
    
    <!-- 预览内容区域 -->
    <div class="preview-content" :class="{ fullscreen: isFullscreen }">
      <!-- 加载状态 -->
      <div v-if="loading" class="preview-loading">
        <div class="loading-spinner">
          <div class="spinner"></div>
          <p>正在加载预览...</p>
        </div>
      </div>
      
      <!-- iframe 预览 -->
      <div v-else-if="showIframe" class="iframe-container">
        <iframe
          ref="previewIframe"
          :src="previewUrl"
          @load="onLoad"
          :class="deviceClasses"
        ></iframe>
        
        <!-- 移动端适配遮罩 -->
        <div v-if="currentDevice === 'mobile'" class="mobile-frame">
          <div class="mobile-notch"></div>
          <div class="mobile-content">
            <slot name="mobile-viewport"></slot>
          </div>
        </div>
      </div>
      
      <!-- 组件级预览（当显示组件时） -->
      <div v-if="showComponentView" class="component-preview">
        <component :is="currentComponent" v-if="currentComponent" />
      </div>
      
      <!-- 代码变更视图 -->
      <div v-if="showDiff" class="diff-viewer">
        <DiffViewer 
          :old-code="oldCode"
          :new-code="newCode"
          :filename="diffFilename"
        />
      </div>
      
      <!-- 空状态 -->
      <div v-if="!loading && !hasPreview" class="empty-preview">
        <i class="fas fa-inbox"></i>
        <p>暂无预览内容</p>
        <small>请修改文件后查看实时预览</small>
      </div>
    </div>
    
    <!-- 底部状态栏 -->
    <div class="preview-statusbar">
      <div class="status-left">
        <span class="status-indicator" :class="connectionStatus">
          <i :class="statusIcons[connectionStatus]"></i>
          {{ statusText[connectionStatus] }}
        </span>
        
        <!-- 最近更改时间 -->
        <span v-if="lastUpdate" class="last-update">
          最后更新：{{ formatTime(lastUpdate) }}
        </span>
      </div>
      
      <div class="status-right">
        <!-- 缩放控制 -->
        <div class="zoom-control">
          <button @click="decreaseZoom" :disabled="zoomLevel <= 25">
            <i class="fas fa-search-minus"></i>
          </button>
          <span class="zoom-level">{{ zoomLevel }}%</span>
          <button @click="increaseZoom" :disabled="zoomLevel >= 200">
            <i class="fas fa-search-plus"></i>
          </button>
        </div>
        
        <!-- 调试信息 -->
        <button 
          v-if="showDebugInfo"
          class="btn-debug"
          @click="toggleDebugInfo"
          :class="{ active: debugInfoVisible }"
        >
          <i class="fas fa-code"></i>
          调试信息
        </button>
      </div>
    </div>
    
    <!-- 调试信息面板 -->
    <div v-if="debugInfoVisible && debugInfo" class="debug-info-panel">
      <div class="debug-header">
        <h4>调试信息</h4>
        <button class="btn-close" @click="debugInfoVisible = false">
          <i class="fas fa-times"></i>
        </button>
      </div>
      <div class="debug-content">
        <pre>{{ debugInfo }}</pre>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { useDebounceFn } from '@vueuse/core';

interface Props {
  modelValue?: string;
  showDiff?: boolean;
  oldCode?: string;
  newCode?: string;
  diffFilename?: string;
  componentToPreview?: string;
}

const props = withDefaults(defineProps<Props>(), {
  showDiff: false
});

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void;
}>();

// 设备配置
interface Device {
  label: string;
  value: 'desktop' | 'tablet' | 'mobile';
  icon: string;
  width: number | string;
}

const devices: Device[] = [
  { label: '桌面', value: 'desktop', icon: 'fas fa-desktop', width: '100%' },
  { label: '平板', value: 'tablet', icon: 'fas fa-tablet-alt', width: '768px' },
  { label: '手机', value: 'mobile', icon: 'fas fa-mobile-alt', width: '375px' }
];

// 状态管理
const previewIframe = ref<HTMLIFrameElement>();
const currentDevice = ref<'desktop' | 'tablet' | 'mobile'>('desktop');
const loading = ref(false);
const isRefreshing = ref(false);
const isFullscreen = ref(false);
const zoomLevel = ref(100);
const connectionStatus = ref<'connected' | 'connecting' | 'disconnected'>('disconnected');
const debugInfoVisible = ref(false);

// 预览 URL（实际应指向 Vite HMR 服务器的预览页面）
const PREVIEW_BASE_URL = import.meta.env.VITE_PREVIEW_URL || 'http://localhost:5173';
const previewUrl = computed(() => {
  const params = new URLSearchParams({
    device: currentDevice.value,
    zoom: zoomLevel.value.toString()
  });
  return `${PREVIEW_BASE_URL}?${params.toString()}` + window.location.hash;
});

// 组件预览
const currentComponent = ref<any>(null);
const showComponentView = computed(() => !!props.componentToPreview && !props.showDiff);
const hasPreview = computed(() => loading.value || showComponentView.value || showDiff.value);

// 调试信息
const debugInfo = ref<string | null>(null);

// 最后更新时间
const lastUpdate = ref<Date | null>(null);

// 设备样式
const deviceClasses = computed(() => ({
  [`device-${currentDevice.value}`]: true,
  'is-refreshing': isRefreshing.value
}));

// 状态图标
const statusIcons = {
  connected: 'fas fa-check-circle',
  connecting: 'fas fa-spinner fa-spin',
  disconnected: 'fas fa-times-circle'
};

const statusText = {
  connected: '已连接',
  connecting: '连接中...',
  disconnected: '未连接'
};

/**
 * 刷新预览
 */
const refreshPreview = useDebounceFn(async () => {
  isRefreshing.value = true;
  loading.value = true;
  
  try {
    if (previewIframe.value) {
      previewIframe.value.contentWindow?.location.reload();
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  } finally {
    loading.value = false;
    isRefreshing.value = false;
    lastUpdate.value = new Date();
  }
}, 300);

/**
 * 加载完成回调
 */
const onLoad = () => {
  loading.value = false;
  connectionStatus.value = 'connected';
  lastUpdate.value = new Date();
};

/**
 * 切换全屏
 */
const toggleFullscreen = () => {
  isFullscreen.value = !isFullscreen.value;
};

/**
 * 缩放控制
 */
const increaseZoom = () => {
  if (zoomLevel.value < 200) {
    zoomLevel.value += 10;
    emitPreviewEvent();
  }
};

const decreaseZoom = () => {
  if (zoomLevel.value > 25) {
    zoomLevel.value -= 10;
    emitPreviewEvent();
  }
};

/**
 * 更新模型值
 */
watch(
  () => props.modelValue,
  (newValue) => {
    if (newValue) {
      loading.value = true;
      // 实际应调用后端 API 或直接更新 DOM
      setTimeout(() => {
        loading.value = false;
        lastUpdate.value = new Date();
      }, 300);
    }
  }
);

/**
 * 显示调试信息
 */
const toggleDebugInfo = async () => {
  debugInfoVisible.value = !debugInfoVisible.value;
  
  if (debugInfoVisible.value && !debugInfo.value) {
    try {
      const ws = new EventSource('/__hmr');
      
      debugInfo.value = JSON.stringify({
        hmrConnection: 'active',
        lastContentUpdate: new Date().toISOString(),
        currentDevice: currentDevice.value,
        zoomLevel: zoomLevel.value + '%',
        hotReloadEnabled: true,
        frameCount: 60
      }, null, 2);
      
      ws.close();
    } catch (error) {
      debugInfo.value = `HMR Connection: Disconnected\nError: ${error}`;
    }
  }
};

/**
 * 发射预览事件
 */
const emitPreviewEvent = () => {
  window.dispatchEvent(new CustomEvent('preview-update', {
    detail: { zoom: zoomLevel.value, device: currentDevice.value }
  }));
};

/**
 * 格式化时间
 */
const formatTime = (date: Date): string => {
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  });
};

/**
 * 初始化 HMR 监听
 */
onMounted(() => {
  // 监听 HMR 消息
  if (import.meta.hot) {
    import.meta.hot.on('vue-reload', () => {
      refreshPreview();
    });
    
    import.meta.hot.on('vue-reconnect', () => {
      connectionStatus.value = 'connected';
    });
  }
  
  // 监听文件变化事件
  window.addEventListener('file-changed', () => {
    refreshPreview();
  });
  
  // 自动刷新（如果配置了自动模式）
  const autoRefresh = localStorage.getItem('auto-refresh');
  if (autoRefresh === 'true') {
    setInterval(() => {
      if (!isRefreshing.value) {
        refreshPreview();
      }
    }, 5000);
  }
});

onUnmounted(() => {
  connectionStatus.value = 'disconnected';
});
</script>

<style scoped lang="scss">
.realtime-preview {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #1e1e1e;
  color: #d4d4d4;
}

.preview-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: #252526;
  border-bottom: 1px solid #333;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: 16px;
  
  .preview-title {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 6px;
    
    i {
      color: #4ec9b0;
    }
  }
}

.toolbar-right {
  display: flex;
  gap: 8px;
}

.btn-device {
  background: #2c2c2e;
  border: 1px solid #333;
  color: #888;
  padding: 6px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  transition: all 0.2s ease;
  display: flex;
  align-items: center;
  gap: 4px;
  
  i {
    font-size: 10px;
  }
  
  &:hover {
    background: #0e639c;
    border-color: #0e639c;
    color: white;
  }
  
  &.active {
    background: #0e639c;
    border-color: #0e639c;
    color: white;
  }
}

.btn-refresh,
.btn-fullscreen {
  background: #2c2c2e;
  border: 1px solid #333;
  color: #d4d4d4;
  padding: 6px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  transition: all 0.2s ease;
  
  &:hover:not(:disabled) {
    background: #0e639c;
    border-color: #0e639c;
  }
  
  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}

.preview-content {
  flex: 1;
  overflow: auto;
  background: #121212;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  
  &.fullscreen {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 9999;
    padding: 0;
  }
}

.iframe-container {
  position: relative;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
  background: white;
  transition: all 0.3s ease;
  
  .iframe-container.mobile {
    border-radius: 24px;
    border: 12px solid #333;
    overflow: hidden;
    background: #000;
  }
}

iframe {
  width: 100%;
  height: 100%;
  min-height: 600px;
  border: none;
  background: white;
  
  &.device-mobile {
    max-width: 375px;
  }
  
  &.device-tablet {
    max-width: 768px;
  }
  
  &.is-refreshing {
    opacity: 0.8;
  }
}

.mobile-frame {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  pointer-events: none;
  
  .mobile-notch {
    position: absolute;
    top: 0;
    left: 50%;
    transform: translateX(-50%);
    width: 120px;
    height: 20px;
    background: #333;
    border-bottom-left-radius: 12px;
    border-bottom-right-radius: 12px;
  }
  
  .mobile-content {
    width: 100%;
    height: 100%;
    background: #000;
  }
}

.component-preview {
  width: 100%;
  max-width: 1200px;
  height: 100%;
  background: white;
  border-radius: 8px;
  padding: 20px;
  overflow: auto;
}

.diff-viewer {
  width: 100%;
  height: 100%;
}

.empty-preview {
  text-align: center;
  color: #666;
  
  i {
    font-size: 48px;
    margin-bottom: 16px;
  }
  
  p {
    margin: 8px 0;
    font-size: 16px;
  }
  
  small {
    font-size: 12px;
    opacity: 0.7;
  }
}

.preview-loading {
  text-align: center;
  color: #666;
  
  .loading-spinner {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
  }
  
  .spinner {
    width: 40px;
    height: 40px;
    border: 4px solid #333;
    border-top-color: #0e639c;
    border-radius: 50%;
    animation: spin 1s linear infinite;
  }
  
  @keyframes spin {
    to { transform: rotate(360deg); }
  }
}

.preview-statusbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 16px;
  background: #252526;
  border-top: 1px solid #333;
  font-size: 12px;
}

.status-left {
  display: flex;
  align-items: center;
  gap: 12px;
  
  .status-indicator {
    display: flex;
    align-items: center;
    gap: 4px;
    
    i {
      font-size: 12px;
    }
    
    &.connected {
      color: #4ec9b0;
    }
    
    &.connecting {
      color: #ff9800;
    }
    
    &.disconnected {
      color: #f44336;
    }
  }
  
  .last-update {
    color: #888;
  }
}

.status-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.zoom-control {
  display: flex;
  align-items: center;
  gap: 8px;
  
  button {
    background: #2c2c2e;
    border: 1px solid #333;
    color: #d4d4d4;
    width: 28px;
    height: 28px;
    border-radius: 4px;
    cursor: pointer;
    transition: all 0.2s ease;
    
    &:hover:not(:disabled) {
      background: #0e639c;
      border-color: #0e639c;
    }
    
    &:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
  }
  
  .zoom-level {
    min-width: 40px;
    text-align: center;
    color: #888;
  }
}

.btn-debug {
  background: #2c2c2e;
  border: 1px solid #333;
  color: #888;
  padding: 4px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  transition: all 0.2s ease;
  
  i {
    margin-right: 4px;
  }
  
  &.active {
    background: #0e639c;
    border-color: #0e639c;
    color: white;
  }
  
  &:hover {
    background: #0e639c;
    border-color: #0e639c;
    color: white;
  }
}

.debug-info-panel {
  position: absolute;
  bottom: 40px;
  left: 16px;
  right: 16px;
  background: #1e1e1e;
  border: 1px solid #333;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
  overflow: hidden;
  
  .debug-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 16px;
    background: #252526;
    border-bottom: 1px solid #333;
    
    h4 {
      margin: 0;
      font-size: 13px;
      color: #d4d4d4;
    }
    
    .btn-close {
      background: none;
      border: none;
      color: #888;
      cursor: pointer;
      font-size: 14px;
      
      &:hover {
        color: #f44336;
      }
    }
  }
  
  .debug-content {
    padding: 16px;
    
    pre {
      margin: 0;
      font-size: 12px;
      line-height: 1.5;
      color: #d4d4d4;
      font-family: 'Fira Code', monospace;
      white-space: pre-wrap;
      word-wrap: break-word;
    }
  }
}
</style>
