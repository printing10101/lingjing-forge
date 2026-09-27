/**
 * src-frontend-only/.dsh-config.ts
 * Solo 模式专属配置文件
 * 定义 Solo 模式的行为、快捷键、样式等
 */

export interface SoloConfig {
  // 模式标识
  readonly mode: 'solo';
  
  // 布局配置
  readonly layout: {
    editorWidth: number;      // 编辑器宽度百分比 (60-80)
    aiPanelWidth: number;     // AI 面板宽度固定 400px
    sidebarVisible: boolean;  // 侧边栏是否可见
  };
  
  // 快捷键配置
  readonly shortcuts: {
    openChat: string;         // Ctrl+K
    openCommandPalette: string; // Ctrl+Shift+P
    toggleFileExplorer: string; // Ctrl+B
    saveAll: string;          // Ctrl+Shift+S
  };
  
  // AI 对话行为
  readonly ai: {
    autoApplyChanges: boolean;     // 是否自动应用代码修改
    askBeforeExecuting: boolean;   // 执行前是否询问
    suggestGitStage: boolean;     // 是否建议 Git 暂存
    previewMode: 'live' | 'diff'; // 预览模式：实时 or 差异对比
  };
  
  // 样式配置
  readonly theme: {
    primaryColor: string;    // 主色调
    accentColor: string;     // 强调色
    darkMode: boolean;       // 是否启用深色模式
    editorTheme: string;     // 编辑器主题
  };
  
  // 工作区配置
  readonly workspace: {
    syncInterval: number;   // 同步间隔 (ms)
    autoBackup: boolean;    // 是否自动备份
    maxHistory: number;     // 最大历史记录数
  };
}

export const soloConfig: SoloConfig = {
  mode: 'solo',
  
  layout: {
    editorWidth: 70,       // 70% 编辑器
    aiPanelWidth: 400,     // 400px AI 面板
    sidebarVisible: true   // 默认显示文件浏览器
  },
  
  shortcuts: {
    openChat: 'Ctrl+K',
    openCommandPalette: 'Ctrl+Shift+P',
    toggleFileExplorer: 'Ctrl+B',
    saveAll: 'Ctrl+Shift+S'
  },
  
  ai: {
    autoApplyChanges: true,     // 自动应用修改（可关闭）
    askBeforeExecuting: false,  // 执行前不询问
    suggestGitStage: true,      // 建议 Git 暂存
    previewMode: 'live'         // 实时预览模式
  },
  
  theme: {
    primaryColor: '#0e639c',    // Trae 风格蓝色
    accentColor: '#4ec9b0',     // 青绿色强调
    darkMode: true,             // 默认深色模式
    editorTheme: 'vs-dark'      // VS Code 暗色主题
  },
  
  workspace: {
    syncInterval: 1000,     // 1 秒同步间隔
    autoBackup: true,       // 自动备份
    maxHistory: 50          // 保留 50 条历史记录
  }
};

export default soloConfig;
