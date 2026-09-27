/**
 * 插件契约（Plugin & ExtensionPoint Contract）
 *
 * 对应后端 app/contracts/plugin.py。
 * 详见 docs/development/core-contracts-design.md 第 5 章。
 *
 * 稳定性承诺：Stable v1.0.0，向后兼容扩展，breaking change 需新开 ADR。
 */

/** 插件 manifest。 */
export interface PluginManifest {
  /** 插件 ID（如 "ltc_chatter"）。 */
  id: string;
  name: string;
  /** semver。 */
  version: string;
  description: string;
  author: string;
  license: string;
  /** "module:ClassName" 格式。 */
  entrypoint: string;
  /** 契约依赖，形如 ["task@1.0.0", "dataset@1.0.0"]。 */
  required_contracts: string[];
  /** 可选契约依赖。 */
  optional_capabilities: string[];
  /** 能力授权请求，如 ["task:submit", "dataset:write"]。 */
  required_capabilities: string[];
  /** 依赖的其他插件 ID + 版本范围。 */
  dependencies: Record<string, string>;
  /** 配置规格（引用 ConfigSpec 名）。 */
  config_schema?: string;
  homepage?: string;
  tags: string[];
}

/** 插件状态。 */
export type PluginStatus =
  | 'installed'
  | 'enabled'
  | 'disabled'
  | 'error'
  | 'installing'
  | 'uninstalling';

/** 插件运行时信息。 */
export interface PluginInfo {
  manifest: PluginManifest;
  status: PluginStatus;
  /** 启用时间（ISO 8601）。 */
  enabled_at?: string;
  /** 错误信息。 */
  error?: string;
  /** 健康检查状态。 */
  health?: 'healthy' | 'unhealthy' | 'unknown';
}

/** 扩展点贡献（插件向前端注入 UI 组件或处理函数）。 */
export interface ExtensionPointContribution {
  /** 扩展点名（如 "workspace.panel"）。 */
  extension_point: string;
  plugin_id: string;
  /** 后端处理函数引用（可选，与 component_url 二选一）。 */
  handler?: string;
  /** 前端组件 URL（异步加载，如 "ltc_chatter/DashboardPanel.vue"）。 */
  component_url?: string;
  /** 组件 props 默认值。 */
  props: Record<string, unknown>;
  metadata: Record<string, unknown>;
}

/** 内置扩展点名（与后端 BUILTIN_EXTENSION_POINTS 对齐）。 */
export type BuiltinExtensionPoint =
  | 'workspace.panel'
  | 'workspace.tab'
  | 'task_board.action'
  | 'dataset.viewer'
  | 'config.editor'
  | 'snapshot.action'
  | 'command_palette.command';

/** 能力定义。 */
export interface Capability {
  name: string;
  description: string;
  /** 是否默认授权。 */
  default_grant: boolean;
}

/** 内置能力名（与后端 BUILTIN_CAPABILITIES 对齐）。 */
export type BuiltinCapabilityName =
  | 'task:submit'
  | 'task:workflow:run'
  | 'dataset:read'
  | 'dataset:write'
  | 'dataset:version:create'
  | 'config:sweep'
  | 'observability:snapshot:create'
  | 'observability:trace:export'
  | 'plugin:install'
  | 'compute:gpu'
  | 'network:egress';

// 抽象接口

/** 扩展点注册表接口（前端 useExtensionRegistry 实现此接口）。 */
export interface IExtensionRegistry {
  register(contribution: ExtensionPointContribution): void;
  unregister(plugin_id: string, extension_point?: string): void;
  list(extension_point: string): ExtensionPointContribution[];
  invoke(contribution: ExtensionPointContribution, payload: unknown): Promise<unknown>;
}

export const CONTRACTS_PLUGIN_VERSION = '1.0.0';

/** 所有内置扩展点常量（与后端 BUILTIN_EXTENSION_POINTS.all() 对齐）。 */
export const BUILTIN_EXTENSION_POINTS: BuiltinExtensionPoint[] = [
  'workspace.panel',
  'workspace.tab',
  'task_board.action',
  'dataset.viewer',
  'config.editor',
  'snapshot.action',
  'command_palette.command',
];
