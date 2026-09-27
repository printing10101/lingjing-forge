<template>
  <div class="solo-topbar" :class="{ 'solo-mode': isSoloMode }">
    <!-- 左侧应用标识 -->
    <div class="topbar-left">
      <div class="app-logo">
        <i class="fas fa-cube"></i>
        <span class="app-name">灵境制造</span>
      </div>
      
      <!-- 文件标签页 -->
      <div class="file-tabs" v-if="!isSoloMode">
        <div 
          v-for="tab in tabs" 
          :key="tab.filepath"
          class="tab-item"
          :class="{ active: activeTab?.filepath === tab.filepath }"
          @click="activeTab = tab"
        >
          <i :class="tab.icon" class="file-icon"></i>
          <span class="filename">{{ tab.filename }}</span>
          <button 
            class="tab-close" 
            @click.stop="handleCloseTab(tab)"
            v-if="tab.closable"
          >
            <i class="fas fa-times"></i>
          </button>
        </div>
        
        <button class="tab-new" title="新建标签">
          <i class="fas fa-plus"></i>
        </button>
      </div>
    </div>
    
    <!-- 中间操作按钮 -->
    <div class="topbar-center">
      <!-- 搜索框 -->
      <div class="search-box">
        <i class="fas fa-search"></i>
        <input 
          type="text"
          placeholder="搜索文件 (Ctrl+P)"
          :disabled="isSoloMode"
          @keydown.enter.exact.prevent="searchFile"
        />
        <span class="search-shortcut" v-if="!isSoloMode">Ctrl+P</span>
      </div>
      
      <!-- Git 状态 -->
      <div v-if="!isSoloMode" class="git-status">
        <span class="git-modified" title="已修改">
          <i class="fas fa-caret-right"></i>
          {{ gitModifiedCount }}
        </span>
        <span class="git-staged" title="已暂存">
          <i class="fas fa-check"></i>
          {{ gitStagedCount }}
        </span>
        <span class="git-untracked" title="未跟踪">
          <i class="fas fa-minus"></i>
          {{ gitUntrackedCount }}
        </span>
      </div>
    </div>
    
    <!-- 右侧功能按钮 -->
    <div class="topbar-right">
      <!-- 模式切换按钮 -->
      <button 
        class="btn-mode-switch"
        @click="$emit('toggle-mode')"
        :class="{ active: isSoloMode }"
        title="切换工作区模式"
      >
        <i :class="isSoloMode ? 'fas fa-rocket' : 'fas fa-code'"></i>
        <span class="mode-label">{{ isSoloMode ? 'Solo 设计模式' : 'IDE 模式' }}</span>
      </button>
      
      <!-- 工具按钮 -->
      <div class="tool-buttons" v-if="isSoloMode">
        <button 
          class="btn-tool" 
          title="打开 AI 对话"
          @click="emit('open-chat')"
        >
          <i class="fas fa-robot"></i>
        </button>
        
        <button 
          class="btn-tool" 
          title="命令面板 (Ctrl+Shift+P)"
          @click="emit('open-palette')"
        >
          <i class="fas fa-th"></i>
        </button>
        
        <button 
          class="btn-tool" 
          title="文件浏览器"
          @click="toggleFileExplorer"
        >
          <i :class="showFileExplorer ? 'fas fa-folder-open' : 'fas fa-folder'"></i>
        </button>
      </div>
      
      <div class="tool-buttons" v-else>
        <button class="btn-tool" title="运行与调试">
          <i class="fas fa-play"></i>
        </button>
        
        <button class="btn-tool" title="插件">
          <i class="fas fa-puzzle-piece"></i>
        </button>
        
        <button class="btn-tool" title="用户设置">
          <i class="fas fa-user-cog"></i>
        </button>
        
        <div class="btn-divider"></div>
        
        <button class="btn-backend-status" :class="{ online: backendStatus }">
          <i :class="backendStatus ? 'fas fa-check-circle' : 'fas fa-exclamation-circle'"></i>
          <span class="status-text">{{ backendStatus ? '运行中' : '停止' }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue';

interface Tab {
  filepath: string;
  filename: string;
  icon: string;
  closable: boolean;
  modified?: boolean;
}

interface Props {
  mode: 'solo' | 'ide';
  showFileExplorer?: boolean;
  gitModifiedCount?: number;
  gitStagedCount?: number;
  gitUntrackedCount?: number;
  backendStatus?: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  showFileExplorer: true,
  gitModifiedCount: 0,
  gitStagedCount: 0,
  gitUntrackedCount: 0,
  backendStatus: false
});

const emit = defineEmits<{
  (e: 'toggle-mode'): void;
  (e: 'open-chat'): void;
  (e: 'open-palette'): void;
}>();

const isSoloMode = computed(() => props.mode === 'solo');
const showFileExplorer = ref(props.showFileExplorer);

// 模拟标签页数据（实际应从 store 获取）
const tabs = ref<Tab[]>([
  {
    filepath: '/engineering/src/views/Dashboard.vue',
    filename: 'Dashboard.vue',
    icon: 'fab fa-vuejs',
    closable: true,
    modified: false
  },
  {
    filepath: '/engineering/src/App.vue',
    filename: 'App.vue',
    icon: 'fab fa-vuejs',
    closable: true,
    modified: true
  }
]);

const activeTab = ref<Tab>(tabs.value[1]);

const toggleFileExplorer = () => {
  showFileExplorer.value = !showFileExplorer.value;
  emit('toggle-explorer', !showFileExplorer.value);
};

const handleCloseTab = (tab: Tab) => {
  tabs.value = tabs.value.filter(t => t.filepath !== tab.filepath);
  if (activeTab.value?.filepath === tab.filepath) {
    activeTab.value = tabs.value[tabs.value.length - 1] || null;
  }
};

const searchFile = async () => {
  // TODO: 调用文件搜索功能
};
</script>

<style scoped lang="scss">
.solo-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px;
  height: 40px;
  background: #252526;
  border-bottom: 1px solid #333;
  font-size: 13px;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 16px;
}

.app-logo {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: #d4d4d4;
  
  i {
    color: #0e639c;
    font-size: 16px;
  }
}

.file-tabs {
  display: flex;
  align-items: center;
  gap: 4px;
  max-width: 600px;
}

.tab-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: #2d2d2e;
  border: 1px solid #333;
  border-radius: 6px 6px 0 0;
  cursor: pointer;
  transition: all 0.2s ease;
  
  &.active {
    background: #1e1e1e;
    border-bottom-color: #1e1e1e;
  }
  
  &:hover:not(.active) {
    background: #333;
  }
  
  .file-icon {
    font-size: 14px;
    &.fab {
      color: #42b883;
    }
  }
  
  .filename {
    max-width: 150px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  
  .tab-close {
    background: none;
    border: none;
    color: #888;
    padding: 0;
    margin-left: 4px;
    cursor: pointer;
    font-size: 10px;
    
    &:hover {
      color: #f44336;
    }
  }
}

.tab-new {
  background: none;
  border: 1px solid #333;
  border-radius: 6px;
  width: 24px;
  height: 24px;
  color: #888;
  cursor: pointer;
  transition: all 0.2s ease;
  
  &:hover {
    background: #0e639c;
    border-color: #0e639c;
    color: white;
  }
}

.topbar-center {
  flex: 1;
  margin: 0 24px;
  display: flex;
  align-items: center;
  gap: 16px;
}

.search-box {
  display: flex;
  align-items: center;
  gap: 8px;
  background: #1e1e1e;
  border: 1px solid #333;
  border-radius: 6px;
  padding: 6px 12px;
  width: 300px;
  transition: all 0.2s ease;
  
  &:focus-within {
    border-color: #0e639c;
  }
  
  i {
    color: #888;
    font-size: 14px;
  }
  
  input {
    flex: 1;
    background: none;
    border: none;
    color: #d4d4d4;
    font-size: 13px;
    outline: none;
    
    &::placeholder {
      color: #666;
    }
    
    &:disabled {
      opacity: 0.5;
    }
  }
  
  .search-shortcut {
    background: #333;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 11px;
    color: #888;
  }
}

.git-status {
  display: flex;
  align-items: center;
  gap: 12px;
  
  .git-modified,
  .git-staged,
  .git-untracked {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 4px 8px;
    background: #2d2d2e;
    border-radius: 4px;
    font-size: 12px;
  }
  
  .git-modified {
    color: #ff9800;
  }
  
  .git-staged {
    color: #4ec9b0;
  }
  
  .git-untracked {
    color: #888;
  }
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.btn-mode-switch {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: #2d2d2e;
  border: 1px solid #333;
  border-radius: 6px;
  color: #d4d4d4;
  cursor: pointer;
  font-size: 13px;
  transition: all 0.2s ease;
  
  i {
    font-size: 14px;
  }
  
  .mode-label {
    display: none;
    font-size: 12px;
  }
  
  &:hover {
    background: #0e639c;
    border-color: #0e639c;
    
    .mode-label {
      display: inline;
    }
  }
  
  &.active {
    background: #0e639c;
    border-color: #0e639c;
    color: white;
  }
}

.tool-buttons {
  display: flex;
  align-items: center;
  gap: 4px;
}

.btn-tool {
  background: none;
  border: none;
  color: #888;
  width: 32px;
  height: 32px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s ease;
  
  &:hover {
    background: #2d2d2e;
    color: #d4d4d4;
  }
}

.btn-divider {
  width: 1px;
  height: 24px;
  background: #333;
  margin: 0 4px;
}

.btn-backend-status {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: #2d2d2e;
  border-radius: 6px;
  cursor: pointer;
  
  &.online {
    i {
      color: #4ec9b0;
    }
    
    .status-text {
      color: #4ec9b0;
    }
  }
  
  &.offline {
    i {
      color: #f44336;
    }
    
    .status-text {
      color: #f44336;
    }
  }
  
  i {
    font-size: 14px;
  }
  
  .status-text {
    font-size: 13px;
    color: #d4d4d4;
  }
}

.solo-mode {
  .file-tabs,
  .git-status,
  .tool-buttons:not(.solo-tools) {
    display: none;
  }
}
</style>
