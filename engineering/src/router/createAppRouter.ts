/**
 * P3-2 前端自主化：Router 封装层 —— createAppRouter 工厂。
 *
 * 统一创建 vue-router 实例，封装常见约定：
 *   - 统一 history 模式（createWebHashHistory）
 *   - 全局前置守卫（认证检查 + 路由元信息校验）
 *   - 全局后置守卫（文档标题）
 *   - 404 fallback
 *
 * 纯逻辑：路由守卫为纯函数可单测；不依赖具体页面组件。
 *
 * 用法：
 *   const router = createAppRouter({
 *     routes: [...],
 *     titleSuffix: '灵境制造',
 *     requireAuth: (to) => !to.meta.public,
 *     hasSession: () => !!localStorage.getItem('token'),
 *   })
 */

import {
  createRouter,
  createWebHashHistory,
  type RouteRecordRaw,
  type Router,
} from 'vue-router'

export interface AppRouterOptions {
  /** 路由表 */
  routes: RouteRecordRaw[]
  /** 文档标题后缀（如 '灵境制造'） */
  titleSuffix?: string
  /** 是否要求认证（默认：非 public 路由需要） */
  requireAuth?: (to: RouteRecordRaw | { meta?: Record<string, unknown> }) => boolean
  /** 会话检测（默认读 localStorage token） */
  hasSession?: () => boolean
  /** 未认证跳转路径（默认 /login） */
  loginPath?: string
  /** 认证失败回调（可注入测试/自定义） */
  onUnauthorized?: (to: unknown) => void
}

/** 默认认证判定：public 元信息为 true 的路由免认证 */
export function defaultRequireAuth(
  to: { meta?: Record<string, unknown> },
): boolean {
  return !(to.meta && to.meta.public === true)
}

/** 默认会话检测 */
export function defaultHasSession(): boolean {
  if (typeof localStorage === 'undefined') return false
  return Boolean(localStorage.getItem('token'))
}

/**
 * 创建应用 Router（统一守卫 + 标题 + 404）。
 */
export function createAppRouter(options: AppRouterOptions): Router {
  const {
    routes,
    titleSuffix = '',
    requireAuth = defaultRequireAuth,
    hasSession = defaultHasSession,
    loginPath = '/login',
    onUnauthorized,
  } = options

  const router = createRouter({
    history: createWebHashHistory(),
    routes: [
      ...routes,
      // 404 fallback
      { path: '/:pathMatch(.*)*', name: 'not-found', component: { template: '<div />' } },
    ],
  })

  router.beforeEach((to) => {
    // 认证守卫
    if (requireAuth(to) && !hasSession()) {
      if (onUnauthorized) {
        onUnauthorized(to)
      }
      return { path: loginPath, query: { redirect: to.fullPath } }
    }
    return true
  })

  router.afterEach((to) => {
    // 文档标题
    const base = typeof to.meta.title === 'string' ? to.meta.title : ''
    document.title = titleSuffix ? `${base}${base ? ' - ' : ''}${titleSuffix}` : base
  })

  return router
}

/** 校验路由表是否含重复 path（防误注册，纯函数可单测） */
export function findDuplicatePaths(routes: RouteRecordRaw[]): string[] {
  const seen = new Map<string, string | symbol>()
  const dupes: string[] = []
  for (const r of routes) {
    if (typeof r.path !== 'string') continue
    const key = r.path
    if (seen.has(key) && !dupes.includes(key)) {
      dupes.push(key)
    }
    seen.set(key, r.name || r.path)
  }
  return dupes
}
