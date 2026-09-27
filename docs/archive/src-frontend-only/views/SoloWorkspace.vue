<template>
  <div class="solo-workspace" :class="{ 'solo-mode': isSoloMode, 'ide-mode': isIdeMode }">
    <!-- 顶部精简工具栏 -->
    <SoloTopBar :mode="mode" @toggle-mode="toggleMode" />
    
    <div class="main-content">
      <!-- 左侧文件浏览器 -->
      <div class="file-explorer" :class="{ hidden: !layoutConfig.sidebarVisible }">
        <FileExplorer v-if="isSoloMode" />
        <ProjectExplorer v-else />
      </div>
      
      <!-- 主编辑器区域 -->
      <div class="editor-container">
        <CodeEditor 
          v-model="currentFileContent"
          :filename="currentFile"
          :readonly="isReadOnly"
          @save="handleSave"
        />
      </div>
      
      <!-- 右侧 AI 对话面板（Solo 模式专属） -->
      <div class="ai-panel" v-if="isSoloMode">
        <AISoloChat 
          @change-generation="handleCodeGeneration"
          @apply-changes="handleApplyChanges"
          @suggest-git-stage="handleSuggestGitStage"
        />
        <RealtimePreview v-model="previewContent" />
      </div>
    </div>
    
    <!-- 底部状态栏 -->
    <SoloStatusBar 
      :mode="mode"
      :sync-status="syncStatus"
      @sync-now="forceSync"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { useModeSwitcher, type WorkspaceMode } from '@/composables/headless/useModeSwitcher';
import { syncWithMainSource } from '@/workers/sync-worker';
import { useGitStore } from '@/stores/gitStore';

// 导入组件
import SoloTopBar from '@/components/solo/SoloTopBar.vue';
import SoloStatusBar from '@/components/solo/SoloStatusBar.vue';
import FileExplorer from '@/components/explorer/FileExplorer.vue';
import ProjectExplorer from '@/components/explorer/ProjectExplorer.vue';
import CodeEditor from '@/components/editor/CodeEditor.vue';
import AISoloChat from '@/components/solo/AISoloChat.vue';
import RealtimePreview from '@/components/solo/RealtimePreview.vue';

// 模式切换 composable
const { mode, isSoloMode, isIdeMode, layoutConfig, toggleMode, enterSoloMode, exitSoloMode } = useModeSwitcher();

// Git Store
const gitStore = useGitStore();

// 状态管理
const currentFile = ref('');
const currentFileContent = ref('');
const isReadOnly = ref(false);
const syncStatus = ref<'idle' | 'syncing' | 'synced' | 'error'>('idle');

// 预览内容
const previewContent = ref('');

// 快捷键处理
const handleKeyDown = (e: KeyboardEvent) => {
  if (e.ctrlKey && e.key === 'k') {
    e.preventDefault();
  }
  
  if (e.ctrlKey && e.shiftKey && e.key === 'p') {
    e.preventDefault();
  }
};

// 文件保存处理
const handleSave = async () => {
  if (isReadOnly.value) return;
  
  // 保存到镜像目录
  try {
    // TODO: 调用文件保存 API
    await syncWithMainSource(); // 强制同步到主目录
    syncStatus.value = 'synced';
  } catch (error) {
    console.error('[Solo Mode] Save Error:', error);
    syncStatus.value = 'error';
  }
};

// AI 代码生成处理
const handleCodeGeneration = async (newCode: string, filename: string) => {
  currentFile.value = filename;
  currentFileContent.value = newCode;
};

// 应用代码修改
const handleApplyChanges = async (changes: Array<{ file: string; code: string }>) => {
  try {
    for (const { file, code } of changes) {
      currentFile.value = file;
      currentFileContent.value = code;
      await handleSave();
    }
  } catch (error) {
    console.error('[Solo Mode] Apply Changes Error:', error);
  }
};

// 建议 Git 暂存
const handleSuggestGitStage = async (files: string[]) => {
  await gitStore.stageFiles(files);
};

// 强制同步
const forceSync = async () => {
  syncStatus.value = 'syncing';
  try {
    await syncWithMainSource();
    syncStatus.value = 'synced';
  } catch (error) {
    syncStatus.value = 'error';
  }
};

// 生命周期
onMounted(() => {
  window.addEventListener('keydown', handleKeyDown);
  
  // 检查是否为 Solo 模式启动
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('mode') === 'solo') {
    enterSoloMode();
  }
});

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeyDown);
});
</script>

<style scoped lang="scss">
.solo-workspace {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #1e1e1e;
  color: #d4d4d4;
}

.main-content {
  flex: 1;
  display: flex;
  overflow: hidden;
}

.file-explorer {
  width: 250px;
  min-width: 200px;
  border-right: 1px solid #333;
  background: #252526;
  transition: width 0.3s ease, display 0.3s ease;
  
  &.hidden {
    display: none;
  }
}

.editor-container {
  flex: 1;
  overflow: hidden;
  background: #1e1e1e;
}

.ai-panel {
  width: 400px;
  min-width: 350px;
  border-left: 1px solid #333;
  background: #252526;
  display: flex;
  flex-direction: column;
}

.solo-mode {
  .file-explorer {
    width: 200px;
  }
  
  .editor-container {
    flex: 0 0 65%;
  }
}

.ide-mode {
  .ai-panel {
    display: none;
  }
  
  .editor-container {
    flex: 1;
  }
}
</style>
