import { ref, computed, watch } from 'vue';
import type { ComputedRef } from 'vue';

/**
 * useModeSwitcher composable
 * 用于切换主工作区模式 ↔ Solo 设计模式
 * 
 * 功能：
 * - 模式状态管理（ide | solo）
 * - 布局自动调整
 * - 快捷键切换
 * - 本地存储持久化
 */

export type WorkspaceMode = 'ide' | 'solo';

interface UseModeSwitcherReturn {
  /** 当前工作区模式 */
  mode: Ref<WorkspaceMode>;
  
  /** 是否处于 Solo 模式 */
  isSoloMode: ComputedRef<boolean>;
  
  /** 是否处于 IDE 模式 */
  isIdeMode: ComputedRef<boolean>;
  
  /** 当前布局配置 */
  layoutConfig: ComputedRef<LayoutConfig>;
  
  /** 切换模式 */
  toggleMode: () => void;
  
  /** 进入 Solo 模式 */
  enterSoloMode: () => void;
  
  /** 退出 Solo 模式 */
  exitSoloMode: () => void;
  
  /** 重置模式 */
  resetMode: () => void;
}

interface LayoutConfig {
  editorWidth: number;
  aiPanelVisible: boolean;
  sidebarWidth: number;
  toolbarVisible: boolean;
}

const STORAGE_KEY = 'lingjing_workspace_mode';
const DEFAULT_MODE: WorkspaceMode = 'ide';

/**
 * 默认布局配置
 */
const LAYOUT_CONFIGS: Record<WorkspaceMode, LayoutConfig> = {
  ide: {
    editorWidth: 100,
    aiPanelVisible: false,
    sidebarWidth: 250,
    toolbarVisible: true
  },
  solo: {
    editorWidth: 70,
    aiPanelVisible: true,
    sidebarWidth: 200,
    toolbarVisible: true
  }
};

export function useModeSwitcher(): UseModeSwitcherReturn {
  const mode = ref<WorkspaceMode>(
    (localStorage.getItem(STORAGE_KEY) as WorkspaceMode) || DEFAULT_MODE
  );
  
  const layoutConfig = computed<LayoutConfig>(() => 
    LAYOUT_CONFIGS[mode.value]
  );
  
  const isSoloMode = computed(() => mode.value === 'solo');
  const isIdeMode = computed(() => mode.value === 'ide');
  
  /**
   * 切换模式
   */
  const toggleMode = () => {
    mode.value = mode.value === 'ide' ? 'solo' : 'ide';
    localStorage.setItem(STORAGE_KEY, mode.value);
  };
  
  /**
   * 进入 Solo 模式
   */
  const enterSoloMode = () => {
    mode.value = 'solo';
    localStorage.setItem(STORAGE_KEY, 'solo');
    
    // 触发全局事件
    window.dispatchEvent(new CustomEvent('workspace-mode-change', {
      detail: { mode: 'solo', timestamp: Date.now() }
    }));
  };
  
  /**
   * 退出 Solo 模式
   */
  const exitSoloMode = () => {
    mode.value = 'ide';
    localStorage.setItem(STORAGE_KEY, 'ide');
    
    // 触发全局事件
    window.dispatchEvent(new CustomEvent('workspace-mode-change', {
      detail: { mode: 'ide', timestamp: Date.now() }
    }));
  };
  
  /**
   * 重置模式
   */
  const resetMode = () => {
    mode.value = DEFAULT_MODE;
    localStorage.removeItem(STORAGE_KEY);
  };
  
  /**
   * 监听 URL 参数切换
   */
  watch(
    () => new URLSearchParams(window.location.search).get('mode'),
    (newMode) => {
      if (newMode === 'solo' && isIdeMode.value) {
        enterSoloMode();
      } else if (newMode === 'ide' && isSoloMode.value) {
        exitSoloMode();
      }
    },
    { immediate: true }
  );
  
  return {
    mode,
    isSoloMode,
    isIdeMode,
    layoutConfig,
    toggleMode,
    enterSoloMode,
    exitSoloMode,
    resetMode
  };
}

export default useModeSwitcher;
