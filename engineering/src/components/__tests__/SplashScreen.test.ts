import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, VueWrapper } from '@vue/test-utils'
import SplashScreen from '@/components/SplashScreen.vue'

// Mock vue-i18n
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => {
      // 返回 key 作为 appName 时拆分为字符
      if (key === 'splashScreen.appName') return 'LJZZ'
      return key
    },
  }),
}))

// Mock @/stores/version
vi.mock('@/stores/version', () => ({
  useVersionStore: () => ({
    frontendVersion: '4.0.0',
  }),
}))

describe('SplashScreen.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    if (wrapper) {
      wrapper.unmount()
    }
  })

  const mountComponent = () => {
    wrapper = mount(SplashScreen, {
      global: {
        stubs: {
          transition: false,
        },
      },
    })
    return wrapper
  }

  describe('组件挂载', () => {
    it('应该正确挂载组件', () => {
      mountComponent()
      expect(wrapper.exists()).toBe(true)
      expect(wrapper.find('.splash-overlay').exists()).toBe(true)
    })

    it('应该渲染粒子效果', () => {
      mountComponent()
      expect(wrapper.findAll('.particle').length).toBe(20)
    })

    it('应该渲染应用名称字符', () => {
      mountComponent()
      expect(wrapper.findAll('.app-name .char').length).toBe(4)
    })

    it('应该渲染应用副标题', () => {
      mountComponent()
      expect(wrapper.find('.app-subtitle').exists()).toBe(true)
    })

    it('应该渲染版本号', () => {
      mountComponent()
      expect(wrapper.find('.app-version-text').text()).toContain('4.0.0')
    })

    it('应该渲染进度条', () => {
      mountComponent()
      expect(wrapper.find('.progress-container').exists()).toBe(true)
      expect(wrapper.find('.progress-track').exists()).toBe(true)
    })

    it('应该渲染底部版权信息', () => {
      mountComponent()
      expect(wrapper.find('.splash-footer').exists()).toBe(true)
      expect(wrapper.find('.splash-footer').text()).toContain('Copyright')
    })
  })

  describe('version 计算属性', () => {
    it('应使用 versionStore 的版本号', () => {
      mountComponent()
      expect(wrapper.vm.version).toBe('4.0.0')
    })
  })

  describe('3D 立方体计算属性', () => {
    it('frontFace 应返回多边形点坐标字符串', () => {
      mountComponent()
      expect(typeof wrapper.vm.frontFace).toBe('string')
      expect(wrapper.vm.frontFace).toContain(',')
    })

    it('backFace 应返回多边形点坐标字符串', () => {
      mountComponent()
      expect(typeof wrapper.vm.backFace).toBe('string')
      expect(wrapper.vm.backFace).toContain(',')
    })

    it('allVertices 应返回 8 个顶点', () => {
      mountComponent()
      expect(wrapper.vm.allVertices.length).toBe(8)
    })

    it('connectingLines 应返回 4 条连接线', () => {
      mountComponent()
      expect(wrapper.vm.connectingLines.length).toBe(4)
    })

    it('每个顶点应包含 x/y 坐标', () => {
      mountComponent()
      const v = wrapper.vm.allVertices[0]
      expect(v.length).toBe(2)
    })

    it('每条连接线应包含 4 个坐标值', () => {
      mountComponent()
      const line = wrapper.vm.connectingLines[0]
      expect(line.length).toBe(4)
    })
  })

  describe('statusMessages 计算属性', () => {
    it('应返回 6 个状态消息', () => {
      mountComponent()
      expect(wrapper.vm.statusMessages.length).toBe(6)
    })

    it('首个状态 at 应为 0', () => {
      mountComponent()
      expect(wrapper.vm.statusMessages[0].at).toBe(0)
    })

    it('最后一个状态 at 应为 90', () => {
      mountComponent()
      expect(wrapper.vm.statusMessages[5].at).toBe(90)
    })
  })

  describe('particleStyle 方法', () => {
    it('应返回包含位置和尺寸的对象', () => {
      mountComponent()
      const style = wrapper.vm.particleStyle(1)
      expect(style).toHaveProperty('left')
      expect(style).toHaveProperty('top')
      expect(style).toHaveProperty('width')
      expect(style).toHaveProperty('height')
      expect(style).toHaveProperty('animationDelay')
      expect(style).toHaveProperty('animationDuration')
    })
  })

  describe('进度更新逻辑', () => {
    it('初始进度应为 0', () => {
      mountComponent()
      expect(wrapper.vm.progress).toBe(0)
    })

    it('定时器触发后进度应增加', () => {
      mountComponent()
      vi.advanceTimersByTime(80)
      expect(wrapper.vm.progress).toBeGreaterThan(0)
    })

    it('进度达到 100 后应触发隐藏', () => {
      mountComponent()
      wrapper.vm.progress = 99
      vi.advanceTimersByTime(80)
      expect(wrapper.vm.progress).toBe(100)
      // 下一个 interval tick（progress=100 的 else 分支）才注册隐藏 timeout
      vi.advanceTimersByTime(80)
      // 再前进 500ms 触发 visible = false
      vi.advanceTimersByTime(500)
      expect(wrapper.vm.visible).toBe(false)
    })

    it('进度不应超过 100', () => {
      mountComponent()
      wrapper.vm.progress = 99
      vi.advanceTimersByTime(80)
      expect(wrapper.vm.progress).toBeLessThanOrEqual(100)
    })

    it('状态文本应根据进度更新', () => {
      mountComponent()
      wrapper.vm.progress = 50
      vi.advanceTimersByTime(80)
      // 进度 >= 35 应显示 statusStartingBackend
      expect(wrapper.vm.statusText).toBe('splashScreen.statusStartingBackend')
    })
  })

  describe('组件卸载', () => {
    it('卸载后应清理定时器', () => {
      mountComponent()
      const clearIntervalSpy = vi.spyOn(globalThis, 'clearInterval')
      wrapper.unmount()
      expect(clearIntervalSpy).toHaveBeenCalled()
      clearIntervalSpy.mockRestore()
    })
  })
})
