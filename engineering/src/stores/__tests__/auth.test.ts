import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'

// mock http 客户端，避免真实网络请求
vi.mock('@/utils/http', () => ({
  default: {
    post: vi.fn(),
  },
}))

import http from '@/utils/http'

describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    sessionStorage.clear()
  })

  describe('initial state', () => {
    it('sessionStorage 无数据时 token 为 null', () => {
      const store = useAuthStore()
      expect(store.token).toBeNull()
    })

    it('sessionStorage 无数据时 user 为 null', () => {
      const store = useAuthStore()
      expect(store.user).toBeNull()
    })

    it('isAuthenticated 为 false', () => {
      const store = useAuthStore()
      expect(store.isAuthenticated).toBe(false)
    })

    it('userRole 默认为 viewer', () => {
      const store = useAuthStore()
      expect(store.userRole).toBe('viewer')
    })

    it('permissions 默认为空数组', () => {
      const store = useAuthStore()
      expect(store.permissions).toEqual([])
    })

    it('sessionStorage 有 token 时恢复 token', () => {
      sessionStorage.setItem('auth_token', 'test_token')
      const store = useAuthStore()
      expect(store.token).toBe('test_token')
      expect(store.isAuthenticated).toBe(true)
    })

    it('sessionStorage 有合法用户信息时恢复 user', () => {
      sessionStorage.setItem('auth_user', JSON.stringify({
        id: 'u1',
        username: 'admin',
        role: 'admin',
        permissions: ['all'],
      }))
      const store = useAuthStore()
      expect(store.user).not.toBeNull()
      expect(store.user?.username).toBe('admin')
      expect(store.userRole).toBe('admin')
    })

    it('sessionStorage 有非法 role 时降级为 viewer', () => {
      sessionStorage.setItem('auth_user', JSON.stringify({
        id: 'u1',
        username: 'hacker',
        role: 'superadmin',
        permissions: ['all'],
      }))
      const store = useAuthStore()
      expect(store.userRole).toBe('viewer')
    })

    it('sessionStorage 损坏的数据被清理且 user 为 null', () => {
      sessionStorage.setItem('auth_user', 'invalid json{{{')
      const store = useAuthStore()
      expect(store.user).toBeNull()
      expect(sessionStorage.getItem('auth_user')).toBeNull()
    })
  })

  describe('computed', () => {
    it('isAdmin 在 admin 角色时返回 true', () => {
      const store = useAuthStore()
      store.$patch({ user: { id: 'u1', username: 'admin', role: 'admin', permissions: ['all'] } })
      expect(store.isAdmin()).toBe(true)
    })

    it('isAdmin 在 operator 角色时返回 false', () => {
      const store = useAuthStore()
      store.$patch({ user: { id: 'u1', username: 'op', role: 'operator', permissions: ['read'] } })
      expect(store.isAdmin()).toBe(false)
    })

    it('isAdmin 在 viewer 角色时返回 false', () => {
      const store = useAuthStore()
      expect(store.isAdmin()).toBe(false)
    })

    it('hasPermission 包含权限时返回 true', () => {
      const store = useAuthStore()
      store.$patch({ user: { id: 'u1', username: 'op', role: 'operator', permissions: ['read', 'write'] } })
      expect(store.hasPermission('read')).toBe(true)
      expect(store.hasPermission('write')).toBe(true)
    })

    it('hasPermission 不包含权限时返回 false', () => {
      const store = useAuthStore()
      store.$patch({ user: { id: 'u1', username: 'op', role: 'operator', permissions: ['read'] } })
      expect(store.hasPermission('delete')).toBe(false)
    })

    it('hasPermission 无用户时返回 false', () => {
      const store = useAuthStore()
      expect(store.hasPermission('read')).toBe(false)
    })
  })

  describe('login', () => {
    it('登录成功时保存 token 和用户信息', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: {
          code: 0,
          data: {
            access_token: 'jwt_token',
            user: { username: 'testuser', role: 'operator', permissions: ['read'] },
          },
        },
      })
      const store = useAuthStore()
      const result = await store.login('testuser', 'pass123')
      expect(result.success).toBe(true)
      expect(store.token).toBe('jwt_token')
      expect(store.user?.username).toBe('testuser')
      expect(store.userRole).toBe('operator')
      expect(sessionStorage.getItem('auth_token')).toBe('jwt_token')
    })

    it('登录成功时 user.id 使用 username 回退', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: {
          code: 0,
          data: {
            access_token: 't',
            user: { username: 'noid_user', role: 'viewer', permissions: [] },
          },
        },
      })
      const store = useAuthStore()
      await store.login('noid_user', 'p')
      expect(store.user?.id).toBe('noid_user')
    })

    it('后端返回非 0 code 时返回错误信息', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1, message: '用户名或密码错误' },
      })
      const store = useAuthStore()
      const result = await store.login('baduser', 'wrong')
      expect(result.success).toBe(false)
      expect(result.error).toBe('用户名或密码错误')
      expect(store.token).toBeNull()
    })

    it('后端返回非 0 code 且无 message 时使用默认信息', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1 },
      })
      const store = useAuthStore()
      const result = await store.login('x', 'y')
      expect(result.success).toBe(false)
      expect(result.error).toBe('登录失败')
    })

    it('网络异常时返回错误信息', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('网络错误'))
      const store = useAuthStore()
      const result = await store.login('u', 'p')
      expect(result.success).toBe(false)
      expect(result.error).toBe('网络错误')
    })

    it('网络异常无 Error 对象时使用默认信息', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue('unknown')
      const store = useAuthStore()
      const result = await store.login('u', 'p')
      expect(result.success).toBe(false)
      expect(result.error).toBe('网络错误，登录失败')
    })

    it('后端返回未知 role 时降级为 viewer', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: {
          code: 0,
          data: {
            access_token: 't',
            user: { username: 'u', role: 'superadmin', permissions: ['all'] },
          },
        },
      })
      const store = useAuthStore()
      await store.login('u', 'p')
      expect(store.userRole).toBe('viewer')
    })
  })

  describe('logout', () => {
    it('清除 token 和 user', () => {
      sessionStorage.setItem('auth_token', 't')
      sessionStorage.setItem('auth_user', JSON.stringify({ id: 'u1', username: 'u', role: 'admin', permissions: [] }))
      const store = useAuthStore()
      expect(store.isAuthenticated).toBe(true)
      store.logout()
      expect(store.token).toBeNull()
      expect(store.user).toBeNull()
      expect(store.isAuthenticated).toBe(false)
    })

    it('同步清理 sessionStorage', () => {
      sessionStorage.setItem('auth_token', 't')
      sessionStorage.setItem('auth_user', '{"id":"u1","username":"u","role":"admin","permissions":[]}')
      const store = useAuthStore()
      store.logout()
      expect(sessionStorage.getItem('auth_token')).toBeNull()
      expect(sessionStorage.getItem('auth_user')).toBeNull()
    })
  })

  describe('setToken', () => {
    it('设置 token 并持久化', () => {
      const store = useAuthStore()
      store.setToken('new_token')
      expect(store.token).toBe('new_token')
      expect(sessionStorage.getItem('auth_token')).toBe('new_token')
    })
  })

  describe('setUser', () => {
    it('设置用户信息并持久化', () => {
      const store = useAuthStore()
      store.setUser({ id: 'u1', username: 'test', role: 'operator', permissions: ['read'] })
      expect(store.user?.username).toBe('test')
      expect(store.userRole).toBe('operator')
      const stored = JSON.parse(sessionStorage.getItem('auth_user')!)
      expect(stored.username).toBe('test')
    })
  })

  describe('isGuest', () => {
    it('is_guest 为 true 时返回 true', () => {
      const store = useAuthStore()
      store.$patch({ user: { id: 'g1', username: 'guest_abcd', role: 'guest', permissions: [], is_guest: true } })
      expect(store.isGuest).toBe(true)
    })

    it('guest 角色时返回 true', () => {
      const store = useAuthStore()
      store.$patch({ user: { id: 'g1', username: 'guest_abcd', role: 'guest', permissions: [] } })
      expect(store.isGuest).toBe(true)
    })

    it('普通用户返回 false', () => {
      const store = useAuthStore()
      store.$patch({ user: { id: 'u1', username: 'op', role: 'operator', permissions: [] } })
      expect(store.isGuest).toBe(false)
    })
  })

  describe('register', () => {
    it('后端返回非 0 code 时返回错误信息', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { code: 1, message: '用户名已存在' } })
      const store = useAuthStore()
      const result = await store.register('user1', 'pass1234')
      expect(result.success).toBe(false)
      expect(result.error).toBe('用户名已存在')
      expect(store.token).toBeNull()
    })

    it('注册成功后自动登录并保存会话', async () => {
      (http.post as ReturnType<typeof vi.fn>)
        .mockResolvedValueOnce({ data: { code: 0, data: { username: 'newbie' } } })
        .mockResolvedValueOnce({
          data: {
            code: 0,
            data: {
              access_token: 'new_token',
              user: { username: 'newbie', role: 'user', permissions: [] },
            },
          },
        })
      const store = useAuthStore()
      const result = await store.register('newbie', 'pass1234')
      expect(result.success).toBe(true)
      expect(store.token).toBe('new_token')
      expect(store.user?.username).toBe('newbie')
      expect(sessionStorage.getItem('auth_token')).toBe('new_token')
    })

    it('网络异常时返回错误信息', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('网络错误'))
      const store = useAuthStore()
      const result = await store.register('u', 'pass1234')
      expect(result.success).toBe(false)
      expect(result.error).toBe('网络错误')
    })
  })

  describe('guestLogin', () => {
    it('访客登录成功时保存会话并标记 is_guest', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: {
          code: 0,
          data: {
            access_token: 'guest_token',
            user: { username: 'guest_abcd', role: 'guest', is_guest: true },
          },
        },
      })
      const store = useAuthStore()
      const result = await store.guestLogin()
      expect(result.success).toBe(true)
      expect(store.token).toBe('guest_token')
      expect(store.userRole).toBe('guest')
      expect(store.isGuest).toBe(true)
      expect(store.user?.is_guest).toBe(true)
      expect(sessionStorage.getItem('auth_token')).toBe('guest_token')
    })

    it('访客模式关闭时返回错误信息', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { code: 1003, message: '访客模式已关闭' } })
      const store = useAuthStore()
      const result = await store.guestLogin()
      expect(result.success).toBe(false)
      expect(result.error).toBe('访客模式已关闭')
      expect(store.token).toBeNull()
    })

    it('网络异常时返回错误信息', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('网络错误'))
      const store = useAuthStore()
      const result = await store.guestLogin()
      expect(result.success).toBe(false)
      expect(result.error).toBe('网络错误')
    })
  })
})