import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import http from '@/utils/http'
import { API_CONFIG, buildApiPath } from '@/config/api'

export type UserRole = 'admin' | 'operator' | 'viewer' | 'guest'

// P1 安全修复：role 字段白名单。后端响应若被中间人篡改或 JWT 解码异常，
// 未知 role 必须降级为 'viewer'，避免绕过 isAdmin() 等权限判断造成权限提升。
const ALLOWED_ROLES: ReadonlySet<UserRole> = new Set(['admin', 'operator', 'viewer', 'guest'])

function normalizeRole(role: unknown): UserRole {
  return ALLOWED_ROLES.has(role as UserRole) ? (role as UserRole) : 'viewer'
}

// P1 安全修复：安全解析 sessionStorage 中的用户信息。
// 若数据被篡改/版本迁移后字段不匹配导致 JSON.parse 抛 SyntaxError，
// 必须降级为 null 并清理脏数据，避免应用启动崩溃。
function safeParseUser(raw: string | null): UserInfo | null {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as UserInfo
    // 同步校验解析后的 role 字段，防止持久化阶段被污染
    return { ...parsed, role: normalizeRole(parsed.role) }
  } catch (e) {
    // 损坏的 sessionStorage 数据清理后降级为未登录状态
    sessionStorage.removeItem('auth_user')
    console.warn('[auth] auth_user 数据损坏，已清理:', e)
    return null
  }
}

interface UserInfo {
  id: string
  username: string
  role: UserRole
  permissions: string[]
  is_guest?: boolean
}

export const useAuthStore = defineStore('auth', () => {
  // 安全考虑：token 不再写入 localStorage（XSS 可窃取 localStorage 长期凭证）。
  // 改为 sessionStorage 存储：会话级有效，关闭标签页后自动清除，降低 XSS 凭证窃取风险。
  // 仍可在刷新页面后从 sessionStorage 恢复，避免权限判断失真。
  const token = ref<string | null>(sessionStorage.getItem('auth_token'))
  // 刷新页面后从 sessionStorage 恢复用户信息，避免权限判断失真
  const _storedUser = sessionStorage.getItem('auth_user')
  const user = ref<UserInfo | null>(safeParseUser(_storedUser))

  const isAuthenticated = computed(() => !!token.value)
  const userRole = computed(() => user.value?.role ?? 'viewer')
  const permissions = computed(() => user.value?.permissions ?? [])
  const isGuest = computed(() => !!user.value?.is_guest || userRole.value === 'guest')

  function isAdmin() {
    return userRole.value === 'admin'
  }

  function hasPermission(perm: string) {
    return permissions.value.includes(perm)
  }

  // 统一处理登录/注册/访客登录成功后的会话写入（token + 用户信息）
  function _setSession(data: Record<string, any>, fallbackUsername?: string) {
    token.value = data.access_token
    // 安全修复：token 写入 sessionStorage 而非 localStorage，降低 XSS 凭证窃取风险
    sessionStorage.setItem('auth_token', data.access_token)
    const userInfo: UserInfo = {
      id: data.user?.username ?? fallbackUsername ?? '',
      username: data.user?.username ?? fallbackUsername ?? '',
      // P1 安全修复：白名单校验后端返回的 role，未知值降级为 'viewer' 防权限提升
      role: normalizeRole(data.user?.role),
      permissions: data.user?.permissions ?? [],
      is_guest: !!data.user?.is_guest,
    }
    user.value = userInfo
    sessionStorage.setItem('auth_user', JSON.stringify(userInfo))
  }

  async function login(username: string, password: string): Promise<{ success: boolean; error?: string }> {
    try {
      const res = await http.post(buildApiPath(API_CONFIG.AUTH, '/login'), { username, password })
      const data = res.data
      if (data.code === 0 && data.data) {
        _setSession(data.data, username)
        return { success: true }
      }
      return { success: false, error: data.message || '登录失败' }
    } catch (err) {
      const msg = (err as { response?: { data?: { message?: string } } })?.response?.data?.message
        || (err instanceof Error ? err.message : '网络错误，登录失败')
      return { success: false, error: msg }
    }
  }

  async function register(username: string, password: string): Promise<{ success: boolean; error?: string }> {
    try {
      const res = await http.post(buildApiPath(API_CONFIG.AUTH, '/register'), { username, password })
      const data = res.data
      if (data.code === 0 && data.data) {
        // 注册成功后自动登录，提供无缝体验
        return await login(username, password)
      }
      return { success: false, error: data.message || '注册失败' }
    } catch (err) {
      const msg = (err as { response?: { data?: { message?: string } } })?.response?.data?.message
        || (err instanceof Error ? err.message : '网络错误，注册失败')
      return { success: false, error: msg }
    }
  }

  async function guestLogin(): Promise<{ success: boolean; error?: string }> {
    try {
      const res = await http.post(buildApiPath(API_CONFIG.AUTH, '/guest'))
      const data = res.data
      if (data.code === 0 && data.data) {
        _setSession(data.data)
        return { success: true }
      }
      return { success: false, error: data.message || '访客登录失败' }
    } catch (err) {
      const msg = (err as { response?: { data?: { message?: string } } })?.response?.data?.message
        || (err instanceof Error ? err.message : '网络错误，访客登录失败')
      return { success: false, error: msg }
    }
  }

  function logout() {
    token.value = null
    user.value = null
    // 安全修复：同步清理 sessionStorage 中的凭证
    sessionStorage.removeItem('auth_token')
    sessionStorage.removeItem('auth_user')
  }

  function setToken(t: string) {
    token.value = t
    // 安全修复：token 写入 sessionStorage 而非 localStorage
    sessionStorage.setItem('auth_token', t)
  }

  function setUser(info: UserInfo) {
    user.value = info
    // 持久化到 sessionStorage，刷新页面后可恢复用户信息
    sessionStorage.setItem('auth_user', JSON.stringify(info))
  }

  return {
    token,
    user,
    isAuthenticated,
    userRole,
    permissions,
    isGuest,
    isAdmin,
    hasPermission,
    login,
    register,
    guestLogin,
    logout,
    setUser,
    setToken,
  }
})
