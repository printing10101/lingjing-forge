/**
 * 侧边栏导航分组配置。
 *
 * 抽取自 AppLayout.vue，统一管理导航项与图标，便于：
 * - 新增/修改路由时同步更新导航；
 * - 基于角色/权限过滤导航项；
 * - 顶部快速搜索（HeaderSearch）复用同一份数据源。
 *
 * labelKey 为 i18n 键（nav.groups.* / nav.items.*），
 * 中英文文案统一维护在 locales/zh-CN.ts 与 locales/en.ts。
 */
import type { Component } from "vue";
import {
  DataLine,
  SetUp,
  Monitor,
  View,
  MagicStick,
  Folder,
  List,
  Tickets,
  Setting,
  ChatDotRound,
  Share,
  Service,
  Cpu,
  Grid,
  Opportunity,
  Operation,
  Aim,
  Check,
  FolderOpened,
  Connection,
  Guide,
  Edit,
  Platform,
  Collection,
  Position,
  InfoFilled,
} from "@element-plus/icons-vue";

export interface NavItem {
  path: string;
  /** i18n 键，渲染时经 t() 翻译，保证语言切换后导航同步 */
  labelKey: string;
  icon: Component;
  /** 可选：所需权限标识，用于基于角色的导航过滤 */
  requiredPermission?: string;
}

export interface NavGroup {
  /** i18n 键，渲染时经 t() 翻译 */
  labelKey: string;
  items: NavItem[];
}

export const navGroups: NavGroup[] = [
  {
    labelKey: "nav.groups.core",
    items: [
      { path: "/", labelKey: "nav.items.home", icon: DataLine },
      {
        path: "/process-planning",
        labelKey: "nav.items.processPlanning",
        icon: SetUp,
      },
      {
        path: "/equipment-monitor",
        labelKey: "nav.items.equipmentMonitor",
        icon: Monitor,
      },
      { path: "/simulation", labelKey: "nav.items.simulation", icon: View },
      {
        path: "/quality-inspection",
        labelKey: "nav.items.qualityInspection",
        icon: MagicStick,
      },
      {
        path: "/process-understanding",
        labelKey: "nav.items.processUnderstanding",
        icon: ChatDotRound,
      },
      {
        path: "/agent-dashboard",
        labelKey: "nav.items.agentDashboard",
        icon: Tickets,
      },
    ],
  },
  {
    labelKey: "nav.groups.resources",
    items: [
      {
        path: "/material-management",
        labelKey: "nav.items.materialManagement",
        icon: Folder,
      },
      { path: "/task-board", labelKey: "nav.items.taskBoard", icon: List },
      {
        path: "/workflow-panel",
        labelKey: "nav.items.workflowPanel",
        icon: Share,
      },
      { path: "/settings", labelKey: "nav.items.settings", icon: Setting },
    ],
  },
  {
    labelKey: "nav.groups.intelligence",
    items: [
      // LNN 训练工作台：功能完整但此前仅深链可达，补齐导航入口
      { path: "/workspace", labelKey: "nav.items.workspace", icon: Platform },
      {
        path: "/flywheel-dashboard",
        labelKey: "nav.items.flywheelDashboard",
        icon: Opportunity,
      },
      { path: "/world-model", labelKey: "nav.items.worldModel", icon: Service },
      { path: "/rl-agent", labelKey: "nav.items.rlAgent", icon: Cpu },
      {
        path: "/explainability",
        labelKey: "nav.items.explainability",
        icon: Grid,
      },
      { path: "/nl-modeling", labelKey: "nav.items.nlModeling", icon: Guide },
    ],
  },
  {
    labelKey: "nav.groups.dataOps",
    items: [
      {
        path: "/cost-dashboard",
        labelKey: "nav.items.costDashboard",
        icon: Operation,
      },
      { path: "/goals", labelKey: "nav.items.goals", icon: Aim },
      {
        path: "/approval-dashboard",
        labelKey: "nav.items.approvalDashboard",
        icon: Check,
      },
    ],
  },
  {
    labelKey: "nav.groups.market",
    items: [
      {
        path: "/template-market",
        labelKey: "nav.items.templateCenter",
        icon: FolderOpened,
      },
      { path: "/plugins", labelKey: "nav.items.plugins", icon: Connection },
      { path: "/rule-editor", labelKey: "nav.items.ruleEditor", icon: Edit },
      {
        path: "/toolpath-editor",
        labelKey: "nav.items.toolpathEditor",
        icon: Position,
      },
      {
        path: "/dialect-manager",
        labelKey: "nav.items.dialectManager",
        icon: Collection,
      },
    ],
  },
  {
    labelKey: "nav.groups.system",
    items: [{ path: "/about", labelKey: "nav.items.about", icon: InfoFilled }],
  },
];
