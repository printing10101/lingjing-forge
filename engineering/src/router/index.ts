import { createRouter, createWebHistory } from "vue-router";
import { ElMessage } from "element-plus";
import { useAuthStore } from "@/stores/auth";
import { buildDocumentTitle } from "./title";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      name: "home",
      component: () => import("../views/Home.vue"),
      meta: { public: true, title: "生产总览" },
    },
    {
      path: "/login",
      name: "login",
      component: () => import("../views/Login.vue"),
      meta: { public: true, title: "登录" },
    },
    {
      path: "/workspace",
      name: "workspace",
      component: () => import("../views/Workspace.vue"),
      meta: { requiresAuth: true, title: "LNN 工作台" },
    },
    {
      path: "/settings",
      name: "settings",
      component: () => import("../views/Settings.vue"),
      meta: { requiresAuth: true, requiresAdmin: true },
    },
    {
      path: "/about",
      name: "about",
      component: () => import("../views/About.vue"),
      meta: { public: true, title: "关于" },
    },
    {
      // 任务历史已并入任务中心列表视图（?view=list 直达表格视图）
      path: "/task-history",
      redirect: { path: "/task-board", query: { view: "list" } },
    },
    {
      path: "/rule-editor",
      name: "rule-editor",
      component: () => import("../views/RuleEditor.vue"),
      meta: { requiresAuth: true, requiresAdmin: true, title: "工艺规则" },
    },
    {
      path: "/toolpath-editor",
      name: "toolpath-editor",
      component: () =>
        import("../components/toolpath-editor/ToolpathEditor.vue"),
      meta: { requiresAuth: true, requiresAdmin: true, title: "刀具路径" },
    },
    {
      path: "/process-planning",
      name: "process-planning",
      component: () => import("../views/ProcessPlanning.vue"),
      meta: { title: "工艺规划", requiresAuth: true },
    },
    {
      path: "/agent-dashboard",
      name: "agent-dashboard",
      component: () => import("../views/AgentDashboard.vue"),
      meta: { title: "代理状态监控", requiresAuth: true, requiresAdmin: true },
    },
    {
      // 独立详情页已移除，统一走智能体管理页的内嵌详情面板
      path: "/agent-detail/:id",
      redirect: (to) => ({
        path: "/agent-dashboard",
        query: { agent: to.params.id },
      }),
    },
    {
      // 模板中心：市场/分支管理/更新中心/详情四页合一，?template=<id> 打开详情抽屉
      path: "/template-market",
      name: "template-center",
      component: () => import("../views/TemplateCenter.vue"),
      meta: { title: "模板中心", requiresAuth: true },
    },
    {
      path: "/template-detail/:id",
      redirect: (to) => ({
        path: "/template-market",
        query: { template: String(to.params.id) },
      }),
    },
    {
      path: "/branch-manager",
      redirect: { path: "/template-market", query: { tab: "branches" } },
    },
    {
      path: "/update-center",
      redirect: { path: "/template-market", query: { tab: "updates" } },
    },
    {
      // 插件中心：市场/管理/日志三页合一
      path: "/plugins",
      name: "plugin-center",
      component: () => import("../views/PluginCenter.vue"),
      meta: { title: "插件中心", requiresAuth: true },
    },
    {
      path: "/plugin-manager",
      redirect: { path: "/plugins" },
    },
    {
      path: "/plugin-market",
      redirect: { path: "/plugins", query: { tab: "market" } },
    },
    {
      path: "/plugin-logs",
      redirect: { path: "/plugins", query: { tab: "logs" } },
    },
    {
      path: "/dialect-manager",
      name: "dialect-manager",
      component: () => import("../views/DialectManager.vue"),
      meta: { title: "后处理器方言", requiresAuth: true },
    },
    {
      path: "/task-board",
      name: "task-board",
      component: () => import("../views/TaskBoard.vue"),
      meta: { title: "任务看板", requiresAuth: true },
    },
    {
      path: "/workflow-panel",
      name: "workflow-panel",
      component: () => import("../views/WorkflowPanel.vue"),
      meta: { title: "工作流编排", requiresAuth: true },
    },
    {
      // 实验快照已并入数据飞轮"实验快照"Tab
      path: "/snapshot-panel",
      redirect: { path: "/flywheel-dashboard", query: { tab: "snapshots" } },
    },
    {
      path: "/flywheel-dashboard",
      name: "flywheel-dashboard",
      component: () => import("../components/FlywheelDashboard.vue"),
      meta: { title: "数据飞轮", requiresAuth: true },
    },
    {
      path: "/cost-dashboard",
      name: "cost-dashboard",
      component: () => import("../views/CostDashboard.vue"),
      meta: { title: "成本仪表盘", requiresAuth: true, requiresAdmin: true },
    },
    {
      path: "/approval-dashboard",
      name: "approval-dashboard",
      component: () => import("../views/ApprovalDashboard.vue"),
      meta: { title: "审批看板", requiresAuth: true, requiresAdmin: true },
    },
    {
      path: "/goals",
      name: "goals",
      component: () => import("../views/Goals.vue"),
      meta: { title: "目标管理", requiresAuth: true },
    },
    {
      path: "/simulation",
      name: "simulation",
      component: () => import("../views/Simulation.vue"),
      meta: { title: "仿真模拟", requiresAuth: true },
    },
    {
      path: "/equipment-monitor",
      name: "equipment-monitor",
      component: () => import("../views/EquipmentMonitor.vue"),
      meta: { title: "设备监控", requiresAuth: true },
    },
    {
      path: "/quality-inspection",
      name: "quality-inspection",
      component: () => import("../views/QualityInspection.vue"),
      meta: { title: "质量检测", requiresAuth: true },
    },
    {
      path: "/material-management",
      name: "material-management",
      component: () => import("../views/MaterialManagement.vue"),
      meta: { title: "物料管理", requiresAuth: true },
    },
    {
      // 生产报表已并入首页"生产报表"Tab
      path: "/production-report",
      redirect: { path: "/", query: { tab: "report" } },
    },
    {
      path: "/nl-modeling",
      name: "nl-modeling",
      component: () => import("../views/NLModeling.vue"),
      meta: { title: "自然语言建模", requiresAuth: true },
    },
    {
      path: "/process-understanding",
      name: "process-understanding",
      component: () => import("../views/ProcessUnderstanding.vue"),
      meta: { title: "工艺理解", requiresAuth: true },
    },
    {
      path: "/explainability",
      name: "explainability",
      component: () => import("../views/Explainability.vue"),
      meta: { title: "可解释性", requiresAuth: true },
    },
    {
      path: "/rl-agent",
      name: "rl-agent",
      component: () => import("../views/RLAgent.vue"),
      meta: { title: "强化学习", requiresAuth: true },
    },
    {
      path: "/world-model",
      name: "world-model",
      component: () => import("../views/WorldModel.vue"),
      meta: { title: "世界模型", requiresAuth: true },
    },
    {
      path: "/:pathMatch(.*)*",
      name: "NotFound",
      component: () => import("@/views/NotFound.vue"),
    },
  ],
});

// 导航后同步浏览器标签页标题（与 createAppRouter 的口径一致：「页面名 - 灵境制造」）
router.afterEach((to) => {
  document.title = buildDocumentTitle(to.meta.title);
});

// 路由守卫：认证与权限检查（安全默认：非公开路由均要求登录）
router.beforeEach((to, _from, next) => {
  const authStore = useAuthStore();

  // 公开路由直接放行（首页、关于页等）
  const isPublic = to.meta?.public === true;
  if (isPublic) {
    next();
    return;
  }

  // 所有非公开路由默认要求认证
  if (!authStore.isAuthenticated) {
    ElMessage.warning("请先登录后再访问该页面");
    next("/login");
    return;
  }

  // 管理员权限检查
  const requiresAdmin = to.meta?.requiresAdmin === true;
  if (requiresAdmin && !authStore.isAdmin()) {
    ElMessage.warning("权限不足，无法访问该页面");
    next("/");
    return;
  }

  next();
});

export default router;
