import { createRouter, createWebHashHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      name: 'home',
      component: () => import('../views/Home.vue'),
      meta: { public: true },
    },
    {
      path: '/workspace',
      name: 'workspace',
      component: () => import('../views/Workspace.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/solo',
      name: 'solo',
      component: () => import('../views/SoloWorkspace.vue'),
      meta: { title: 'Solo 设计模式', requiresAuth: true },
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('../views/Settings.vue'),
      meta: { requiresAuth: true, requiresAdmin: true },
    },
    {
      path: '/about',
      name: 'about',
      component: () => import('../views/About.vue'),
      meta: { public: true },
    },
    {
      path: '/task-history',
      name: 'task-history',
      component: () => import('../views/TaskHistory.vue'),
    },
    {
      path: '/rule-editor',
      name: 'rule-editor',
      component: () => import('../views/RuleEditor.vue'),
      meta: { requiresAuth: true, requiresAdmin: true },
    },
    {
      path: '/toolpath-editor',
      name: 'toolpath-editor',
      component: () => import('../components/toolpath-editor/ToolpathEditor.vue'),
      meta: { requiresAuth: true, requiresAdmin: true },
    },
    {
      path: '/process-planning',
      name: 'process-planning',
      component: () => import('../views/ProcessPlanning.vue'),
      meta: { title: '工艺规划' },
    },
    {
      path: '/ux-demo',
      name: 'ux-demo',
      component: () => import('../views/UXDemo.vue'),
      meta: { title: 'UX 功能演示' },
    },
    {
      path: '/agent-dashboard',
      name: 'agent-dashboard',
      component: () => import('../views/AgentDashboard.vue'),
      meta: { title: '代理状态监控', requiresAuth: true, requiresAdmin: true },
    },
    {
      path: '/agent-detail/:id',
      name: 'agent-detail',
      component: () => import('../views/AgentDetail.vue'),
      meta: { title: '代理详情' },
    },
    {
      path: '/template-market',
      name: 'template-market',
      component: () => import('../views/TemplateMarket.vue'),
      meta: { title: '模板市场' },
    },
    {
      path: '/plugin-market',
      name: 'plugin-market',
      component: () => import('../views/PluginMarket.vue'),
      meta: { title: '插件市场' },
    },
    {
      path: '/dialect-manager',
      name: 'dialect-manager',
      component: () => import('../views/DialectManager.vue'),
      meta: { title: '后处理器方言' },
    },
    {
      path: '/task-board',
      name: 'task-board',
      component: () => import('../views/TaskBoard.vue'),
      meta: { title: '任务看板' },
    },
    {
      path: '/simulation',
      name: 'simulation',
      component: () => import('../views/Simulation.vue'),
      meta: { title: '仿真模拟' },
    },
    {
      path: '/quality-inspection',
      name: 'quality-inspection',
      component: () => import('../views/QualityInspection.vue'),
      meta: { title: '质量检测' },
    },
    {
      path: '/nl-modeling',
      name: 'nl-modeling',
      component: () => import('../views/NLModeling.vue'),
      meta: { title: '自然语言建模' },
    },
    {
      path: '/process-understanding',
      name: 'process-understanding',
      component: () => import('../views/ProcessUnderstanding.vue'),
      meta: { title: '工艺理解' },
    },
    {
      path: '/rl-agent',
      name: 'rl-agent',
      component: () => import('../views/RLAgent.vue'),
      meta: { title: 'RL 决策', requiresAuth: true },
    },
    {
      path: '/explainability',
      name: 'explainability',
      component: () => import('../views/Explainability.vue'),
      meta: { title: '可解释性', requiresAuth: true },
    },
    {
      path: '/:pathMatch(.*)*',
      name: 'NotFound',
      component: () => import('@/views/NotFound.vue'),
    },
  ],
})

// 路由守卫：认证与权限检查（安全默认：非公开路由均要求登录）
router.beforeEach((to, _from, next) => {
  const authStore = useAuthStore()

  // 公开路由直接放行（首页、关于页等）
  const isPublic = to.meta?.public === true
  if (isPublic) {
    next()
    return
  }

  // 所有非公开路由默认要求认证
  if (!authStore.isAuthenticated) {
    ElMessage.warning('请先登录后再访问该页面')
    next('/')
    return
  }

  // 管理员权限检查
  const requiresAdmin = to.meta?.requiresAdmin === true
  if (requiresAdmin && !authStore.isAdmin()) {
    ElMessage.warning('权限不足，无法访问该页面')
    next('/')
    return
  }

  next()
})

export default router
