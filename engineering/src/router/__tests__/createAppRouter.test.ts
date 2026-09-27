// P3-2 Router 封装工厂测试（纯逻辑：守卫/标题/404/重复路径）
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

import {
  createAppRouter,
  defaultHasSession,
  defaultRequireAuth,
  findDuplicatePaths,
} from '@/router/createAppRouter'

const Home = { template: '<div>home</div>' }
const Login = { template: '<div>login</div>' }

describe('defaultRequireAuth', () => {
  it('requires auth for routes without public meta', () => {
    expect(defaultRequireAuth({ meta: {} })).toBe(true)
    expect(defaultRequireAuth({})).toBe(true)
  })

  it('skips auth for public routes', () => {
    expect(defaultRequireAuth({ meta: { public: true } })).toBe(false)
  })
})

describe('defaultHasSession', () => {
  const originalLocalStorage = globalThis.localStorage

  afterEach(() => {
    Object.defineProperty(globalThis, 'localStorage', {
      value: originalLocalStorage,
      configurable: true,
    })
  })

  it('returns true when token present', () => {
    const store = new Map<string, string>([['token', 'abc']])
    Object.defineProperty(globalThis, 'localStorage', {
      value: { getItem: (k: string) => store.get(k) ?? null },
      configurable: true,
    })
    expect(defaultHasSession()).toBe(true)
  })

  it('returns false when no token', () => {
    const store = new Map<string, string>()
    Object.defineProperty(globalThis, 'localStorage', {
      value: { getItem: (k: string) => store.get(k) ?? null },
      configurable: true,
    })
    expect(defaultHasSession()).toBe(false)
  })
})

describe('createAppRouter', () => {
  beforeEach(() => {
    // 清理 localStorage 避免测试间污染
    const store = new Map<string, string>()
    Object.defineProperty(globalThis, 'localStorage', {
      value: {
        getItem: (k: string) => store.get(k) ?? null,
        setItem: (k: string, v: string) => store.set(k, v),
        removeItem: (k: string) => store.delete(k),
      },
      configurable: true,
    })
  })

  it('creates router with routes and 404 fallback', () => {
    const router = createAppRouter({
      routes: [
        { path: '/', name: 'home', component: Home, meta: { public: true } },
        { path: '/login', name: 'login', component: Login, meta: { public: true } },
      ],
    })
    expect(router).toBeTruthy()
    // 404 fallback 已注册
    const resolved = router.resolve('/nonexistent')
    expect(resolved.name).toBe('not-found')
  })

  it('redirects unauthenticated users to login', async () => {
    const router = createAppRouter({
      routes: [
        { path: '/', name: 'home', component: Home },
        { path: '/login', name: 'login', component: Login, meta: { public: true } },
      ],
      loginPath: '/login',
    })
    await router.push('/')
    expect(router.currentRoute.value.path).toBe('/login')
    expect(router.currentRoute.value.query.redirect).toBe('/')
  })

  it('allows authenticated users through', async () => {
    localStorage.setItem('token', 'x')
    const router = createAppRouter({
      routes: [
        { path: '/', name: 'home', component: Home },
        { path: '/login', name: 'login', component: Login, meta: { public: true } },
      ],
    })
    await router.push('/')
    expect(router.currentRoute.value.path).toBe('/')
  })

  it('public routes bypass auth', async () => {
    const router = createAppRouter({
      routes: [
        { path: '/', name: 'home', component: Home, meta: { public: true } },
      ],
    })
    await router.push('/')
    expect(router.currentRoute.value.path).toBe('/')
  })

  it('calls onUnauthorized callback when blocked', async () => {
    const onUnauthorized = vi.fn()
    const router = createAppRouter({
      routes: [
        { path: '/', name: 'home', component: Home },
        { path: '/login', name: 'login', component: Login, meta: { public: true } },
      ],
      onUnauthorized,
    })
    await router.push('/')
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('sets document title after navigation', async () => {
    const router = createAppRouter({
      routes: [
        { path: '/', name: 'home', component: Home, meta: { public: true, title: '首页' } },
      ],
      titleSuffix: '灵境制造',
    })
    await router.push('/')
    expect(document.title).toBe('首页 - 灵境制造')
  })

  it('sets title to suffix only when route has no title', async () => {
    const router = createAppRouter({
      routes: [
        { path: '/', name: 'home', component: Home, meta: { public: true } },
      ],
      titleSuffix: '灵境制造',
    })
    await router.push('/')
    expect(document.title).toBe('灵境制造')
  })

  it('custom requireAuth and hasSession are honored', async () => {
    const router = createAppRouter({
      routes: [
        { path: '/', name: 'home', component: Home },
      ],
      requireAuth: () => false, // 永不要求认证
      hasSession: () => false,
    })
    await router.push('/')
    expect(router.currentRoute.value.path).toBe('/')
  })
})

describe('findDuplicatePaths', () => {
  it('detects duplicate paths', () => {
    const routes = [
      { path: '/a', name: 'a', component: Home },
      { path: '/a', name: 'a2', component: Home },
      { path: '/b', name: 'b', component: Home },
    ]
    expect(findDuplicatePaths(routes)).toEqual(['/a'])
  })

  it('returns empty for unique paths', () => {
    const routes = [
      { path: '/a', name: 'a', component: Home },
      { path: '/b', name: 'b', component: Home },
    ]
    expect(findDuplicatePaths(routes)).toEqual([])
  })
})
