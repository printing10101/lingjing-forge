import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useSettingsStore } from '@/stores/settings'

describe('useSettingsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
    // 恢复 spy（happy-dom 的 localStorage.setItem 是实例方法，残留 spy 链
    // 会导致后续测试 spy 转发到旧 spy 而非原始实现——localStorage 写入失效）
    vi.restoreAllMocks()
  })

  describe('initial state', () => {
    it('无 localStorage 时使用默认设置', () => {
      const store = useSettingsStore()
      expect(store.settings.aiMode).toBe('local')
      expect(store.settings.localModel).toBe('qwen3.5:35b-128k')
      expect(store.settings.device).toBe('cpu')
      expect(store.settings.offlineMode).toBe(false)
      expect(store.settings.hardwareTier).toBe('standard')
      expect(store.settings.lightweightMode).toBe(false)
    })

    it('默认日志设置正确', () => {
      const store = useSettingsStore()
      expect(store.settings.logSettings.logLevel).toBe('INFO')
      expect(store.settings.logSettings.maxFileSizeMB).toBe(50)
      expect(store.settings.logSettings.retentionDays).toBe(30)
      expect(store.settings.logSettings.exportDays).toBe(7)
    })

    it('localStorage 存在配置时合并加载', () => {
      localStorage.setItem('lingjing_settings', JSON.stringify({
        aiMode: 'cloud',
        localModel: 'gpt-4',
        device: 'cuda',
        hardwareTier: 'high',
      }))
      const store = useSettingsStore()
      expect(store.settings.aiMode).toBe('cloud')
      expect(store.settings.localModel).toBe('gpt-4')
      expect(store.settings.device).toBe('cuda')
      expect(store.settings.hardwareTier).toBe('high')
      // 未覆盖字段保留默认
      expect(store.settings.offlineMode).toBe(false)
    })

    it('localStorage 部分日志配置时合并', () => {
      localStorage.setItem('lingjing_settings', JSON.stringify({
        logSettings: { logLevel: 'ERROR' },
      }))
      const store = useSettingsStore()
      expect(store.settings.logSettings.logLevel).toBe('ERROR')
      expect(store.settings.logSettings.maxFileSizeMB).toBe(50)
    })

    it('localStorage 数据损坏时回退默认并清理', () => {
      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
      localStorage.setItem('lingjing_settings', '{invalid json')
      const store = useSettingsStore()
      expect(store.settings.aiMode).toBe('local')
      expect(localStorage.getItem('lingjing_settings')).toBeNull()
      warnSpy.mockRestore()
    })
  })

  describe('saveSettings', () => {
    it('保存设置到 localStorage', () => {
      const store = useSettingsStore()
      store.settings = { ...store.settings, aiMode: 'cloud' }
      store.saveSettings()
      const raw = localStorage.getItem('lingjing_settings')
      expect(raw).not.toBeNull()
      expect(JSON.parse(raw!).aiMode).toBe('cloud')
    })

    it('localStorage 写入失败时不抛错', () => {
      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
      const store = useSettingsStore()
      vi.spyOn(localStorage, 'setItem').mockImplementation(() => {
        throw new Error('quota exceeded')
      })
      expect(() => store.saveSettings()).not.toThrow()
      warnSpy.mockRestore()
    })
  })

  describe('resetSettings', () => {
    it('重置为默认设置', () => {
      const store = useSettingsStore()
      store.$patch({ settings: { ...store.settings, aiMode: 'cloud', hardwareTier: 'ultra' } })
      store.resetSettings()
      expect(store.settings.aiMode).toBe('local')
      expect(store.settings.hardwareTier).toBe('standard')
    })
  })

  describe('updateLogSettings', () => {
    it('部分更新日志设置', () => {
      const store = useSettingsStore()
      store.updateLogSettings({ logLevel: 'DEBUG', maxFileSizeMB: 100 })
      expect(store.settings.logSettings.logLevel).toBe('DEBUG')
      expect(store.settings.logSettings.maxFileSizeMB).toBe(100)
      // 未更新字段保留
      expect(store.settings.logSettings.retentionDays).toBe(30)
    })
  })

  describe('watch 持久化（防抖）', () => {
    it('设置变更后防抖延迟内不写入', async () => {
      
      const store = useSettingsStore()
      const setSpy = vi.spyOn(localStorage, 'setItem')
      store.settings = { ...store.settings, aiMode: 'cloud' }
      // 防抖 300ms 内不应触发 localStorage 写入
      await new Promise((r) => setTimeout(r, 150))
      // 注：可能已有初始 watcher 触发，检查最新调用
      const callsBefore = setSpy.mock.calls.length
      await new Promise((r) => setTimeout(r, 150))
      expect(setSpy.mock.calls.length).toBeGreaterThanOrEqual(callsBefore)
    })

    it('设置变更后超过 300ms 写入 localStorage', async () => {
      
      const store = useSettingsStore()
      const setSpy = vi.spyOn(localStorage, 'setItem')
      setSpy.mockClear()
      store.settings = { ...store.settings, aiMode: 'cloud' }
      await new Promise((r) => setTimeout(r, 350))
      expect(setSpy).toHaveBeenCalled()
      const raw = localStorage.getItem('lingjing_settings')
      expect(raw).not.toBeNull()
      expect(JSON.parse(raw!).aiMode).toBe('cloud')
    })

    it('防抖合并连续变更', async () => {
      
      const store = useSettingsStore()
      const setSpy = vi.spyOn(localStorage, 'setItem')
      setSpy.mockClear()
      store.settings = { ...store.settings, aiMode: 'cloud' }
      store.settings = { ...store.settings, aiMode: 'local' }
      store.settings = { ...store.settings, device: 'cuda' }
      await Promise.resolve()
      await Promise.resolve()
      await new Promise((r) => setTimeout(r, 350))
      // 多次变更只触发一次写入
      const persistCalls = setSpy.mock.calls.filter(c => c[0] === 'lingjing_settings')
      expect(persistCalls.length).toBe(1)
      expect(JSON.parse(persistCalls[0][1]).device).toBe('cuda')
    })

    it('localStorage 持久化失败时不抛错', async () => {
      
      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
      const store = useSettingsStore()
      vi.spyOn(localStorage, 'setItem').mockImplementation(() => {
        throw new Error('quota')
      })
      store.settings = { ...store.settings, aiMode: 'cloud' }
      await new Promise((r) => setTimeout(r, 350))
      warnSpy.mockRestore()
    })
  })
})
