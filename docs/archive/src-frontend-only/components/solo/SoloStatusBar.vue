<template>
  <div class="solo-statusbar" :class="{ syncing: syncStatus === 'syncing' }">
    <!-- 左侧状态信息 -->
    <div class="statusbar-left">
      <!-- 同步状态 -->
      <div 
        class="sync-status"
        :class="syncStatus"
        @click="$emit('sync-now')"
        title="点击立即同步"
      >
        <i :class="syncIcons[syncStatus]"></i>
        <span class="sync-text">{{ syncText[syncStatus] }}</span>
        
        <!-- 同步进度条 -->
        <div v-if="syncStatus === 'syncing'" class="sync-progress">
          <div class="progress-bar">
            <div class="progress-fill" :style="{ width: syncProgress + '%' }"></div>
          </div>
        </div>
      </div>
      
      <!-- Git 分支 -->
      <div class="git-branch" v-if="gitBranch">
        <i class="fab fa-git-alt"></i>
        <span>{{ gitBranch }}</span>
      </div>
      
      <!-- 编码格式 -->
      <div class="encoding">
        <i class="fas fa-font"></i>
        <span>{{ encoding }}</span>
      </div>
      
      <!-- 行尾符 -->
      <div class="line-endings">
        <i class="fas fa-paragraph"></i>
        <span>{{ lineEndings }}</span>
      </div>
    </div>
    
    <!-- 右侧操作项 -->
    <div class="statusbar-right">
      <!-- 语言模式 -->
      <div class="language-mode" v-if="language">
        <i class="fas fa-language"></i>
        <span>{{ language }}</span>
      </div>
      
      <!-- 缩进设置 -->
      <div class="indent-settings">
        <i class="fas fa-indent"></i>
        <span>{{ indentSize }} {{ indentUnit }}</span>
      </div>
      
      <!-- 显示比例 -->
      <div class="zoom-level">
        <i class="fas fa-search"></i>
        <span>{{ zoomLevel }}%</span>
      </div>
      
      <!-- 时间戳 -->
      <div class="timestamp">
        <i class="far fa-clock"></i>
        <span>{{ currentTime }}</span>
      </div>
      
      <!-- 模式切换 -->
      <button 
        class="btn-switch-mode"
        @click="toggleMode"
        title="切换到 {{ isSoloMode ? 'IDE' : 'Solo' }} 模式"
      >
        <i :class="isSoloMode ? 'fas fa-code' : 'fas fa-rocket'"></i>
        <span>{{ isSoloMode ? 'IDE 模式' : 'Solo 模式' }}</span>
      </button>
      
      <!-- 设置按钮 -->
      <button 
        class="btn-settings"
        title="设置"
        @click="$emit('open-settings')"
      >
        <i class="fas fa-cog"></i>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue';

interface Props {
  mode?: 'solo' | 'ide';
  syncStatus?: 'idle' | 'syncing' | 'synced' | 'error';
  syncProgress?: number;
  gitBranch?: string;
  encoding?: string;
  lineEndings?: string;
  language?: string;
  indentSize?: number;
  indentUnit?: string;
  zoomLevel?: number;
}

const props = withDefaults(defineProps<Props>(), {
  mode: 'solo',
  syncStatus: 'idle',
  syncProgress: 0,
  gitBranch: 'main',
  encoding: 'UTF-8',
  lineEndings: 'LF',
  language: 'Vue',
  indentSize: 2,
  indentUnit: 'space',
  zoomLevel: 100
});

const emit = defineEmits<{
  (e: 'sync-now'): void;
  (e: 'open-settings'): void;
  (e: 'mode-change'): void;
}>();

const isSoloMode = computed(() => props.mode === 'solo');
const currentTime = ref('');
const syncProgress = ref(props.syncProgress);

// 同步图标
const syncIcons = {
  idle: 'fas fa-sync',
  syncing: 'fas fa-sync fa-spin',
  synced: 'fas fa-check-circle',
  error: 'fas fa-exclamation-circle'
};

// 同步文本
const syncText = {
  idle: '等待同步',
  syncing: '同步中...',
  synced: '已同步',
  error: '同步失败'
};

// 更新时间
const updateTime = () => {
  const now = new Date();
  currentTime.value = now.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit'
  });
};

// 切换同步进度
const syncInterval = setInterval(() => {
  if (props.syncStatus === 'syncing') {
    syncProgress.value = Math.min(syncProgress.value + 5, 100);
  }
}, 200);

// 切换模式
const toggleMode = () => {
  emit('mode-change');
};

// 生命周期
onMounted(() => {
  updateTime();
  setInterval(updateTime, 1000);
});

onUnmounted(() => {
  clearInterval(syncInterval);
});
</script>

<style scoped lang="scss">
.solo-statusbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 12px;
  background: #0e639c;
  color: white;
  font-size: 12px;
  border-top: 1px solid #1177bb;
  
  &.syncing {
    background: #ff9800;
  }
}

.statusbar-left,
.statusbar-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.sync-status {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s ease;
  
  &:hover {
    background: rgba(255, 255, 255, 0.15);
  }
  
  i {
    font-size: 14px;
  }
  
  .sync-text {
    font-size: 11px;
  }
  
  .sync-progress {
    width: 80px;
    height: 4px;
    background: rgba(255, 255, 255, 0.2);
    border-radius: 2px;
    margin-left: 8px;
    overflow: hidden;
    
    .progress-bar {
      height: 100%;
      background: rgba(255, 255, 255, 0.5);
    }
  }
}

.git-branch,
.encoding,
.line-endings,
.language-mode,
.indent-settings,
.zoom-level,
.timestamp {
  display: flex;
  align-items: center;
  gap: 4px;
  
  i {
    font-size: 12px;
    opacity: 0.8;
  }
}

.git-branch {
  .fab {
    font-size: 14px;
  }
}

.btn-switch-mode,
.btn-settings {
  background: rgba(255, 255, 255, 0.15);
  border: none;
  color: white;
  padding: 4px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 11px;
  transition: all 0.2s ease;
  display: flex;
  align-items: center;
  gap: 4px;
  
  &:hover {
    background: rgba(255, 255, 255, 0.25);
  }
  
  i {
    font-size: 12px;
  }
}

.btn-settings {
  width: 28px;
  height: 24px;
  padding: 0;
  justify-content: center;
}
</style>
