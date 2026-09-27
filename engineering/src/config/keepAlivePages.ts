/**
 * 启用 keep-alive 缓存的页面清单（AppLayout 的 <keep-alive :include> 消费）。
 *
 * 只缓存"交互状态值得跨页保留"的页面：建模对话、规划流程、各类编辑器、训练工作台
 * （工作台的训练 SSE 监控依赖缓存——离开页面再回来时训练进度不丢）。
 *
 * 仪表盘/监控类页面刻意**不缓存**：
 * - 每次进入重新挂载，拉到最新数据，避免"切回来看到陈旧看板"；
 * - 失活组件的 onUnmounted 不触发，其轮询定时器（如 Home 的刷新定时器）
 *   会在后台永久运行——不缓存让定时器随卸载真正清理。
 *
 * name 依赖 <script setup> SFC 的文件名推断，因此约定：
 * - file 指向的 SFC 不得使用 defineOptions 覆盖组件名（keepAlivePages.test.ts 守护）；
 * - name 拼写错误会让缓存静默失效，同样由测试通过文件存在性校验拦截。
 */
export interface KeepAlivePage {
  /** 组件名（= SFC 文件名），keep-alive include 按它匹配 */
  name: string;
  /** 相对 src/ 的文件路径，仅用于测试校验文件存在 */
  file: string;
}

export const KEEP_ALIVE_PAGES: KeepAlivePage[] = [
  { name: "Workspace", file: "views/Workspace.vue" },
  { name: "NLModeling", file: "views/NLModeling.vue" },
  { name: "ProcessPlanning", file: "views/ProcessPlanning.vue" },
  { name: "RuleEditor", file: "views/RuleEditor.vue" },
  {
    name: "ToolpathEditor",
    file: "components/toolpath-editor/ToolpathEditor.vue",
  },
];

export const KEEP_ALIVE_PAGE_NAMES = KEEP_ALIVE_PAGES.map((page) => page.name);
