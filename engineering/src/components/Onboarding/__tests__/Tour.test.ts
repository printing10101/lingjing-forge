import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, VueWrapper } from '@vue/test-utils'
import Tour from '@/components/Onboarding/Tour.vue'

// Mock vue-i18n
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}))

interface TourStep {
  title: string
  description: string
  target?: string
  image?: string
  placement?: 'top' | 'bottom' | 'left' | 'right'
}

const buildSteps = (): TourStep[] => [
  { title: '步骤1', description: '描述1', target: '#target-1', placement: 'bottom' },
  { title: '步骤2', description: '描述2', target: '#target-2', placement: 'top' },
  { title: '步骤3', description: '描述3' },
]

describe('Tour.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
  })

  afterEach(() => {
    vi.useRealTimers()
    if (wrapper) {
      wrapper.unmount()
    }
    localStorage.clear()
  })

  const mountComponent = (props: Record<string, any> = {}) => {
    wrapper = mount(Tour, {
      props: {
        steps: buildSteps(),
        ...props,
      },
      global: {
        stubs: {
          transition: false,
          'el-button': true,
        },
      },
    })
    return wrapper
  }

  describe('组件挂载', () => {
    it('应该正确挂载组件', () => {
      mountComponent()
      expect(wrapper.exists()).toBe(true)
    })

    it('初始 visible 状态应为 false', () => {
      mountComponent()
      expect(wrapper.vm.visible).toBe(false)
    })

    it('初始 currentStepIndex 应为 0', () => {
      mountComponent()
      expect(wrapper.vm.currentStepIndex).toBe(0)
    })

    it('未启动时不应渲染遮罩层', () => {
      mountComponent()
      expect(wrapper.find('.tour-overlay').exists()).toBe(false)
    })
  })

  describe('currentStep 计算属性', () => {
    it('应返回当前索引对应的步骤', () => {
      mountComponent()
      expect(wrapper.vm.currentStep.title).toBe('步骤1')
    })

    it('索引超出范围时应返回 null', () => {
      mountComponent()
      wrapper.vm.currentStepIndex = 99
      expect(wrapper.vm.currentStep).toBeNull()
    })

    it('空步骤数组时应返回 null', () => {
      mountComponent({ steps: [] })
      expect(wrapper.vm.currentStep).toBeNull()
    })
  })

  describe('highlightStyle 计算属性', () => {
    it('无 targetRect 时应返回空对象', () => {
      mountComponent()
      expect(wrapper.vm.highlightStyle).toEqual({})
    })

    it('有 targetRect 时应返回包含定位样式的对象', () => {
      mountComponent()
      wrapper.vm.targetRect = {
        top: 100,
        left: 200,
        width: 50,
        height: 60,
        right: 250,
        bottom: 160,
        x: 200,
        y: 100,
        toJSON: () => ({}),
      } as DOMRect
      const style = wrapper.vm.highlightStyle
      expect(style).toHaveProperty('top')
      expect(style).toHaveProperty('left')
      expect(style).toHaveProperty('width')
      expect(style).toHaveProperty('height')
      expect(style.top).toContain('92px')
      expect(style.left).toContain('192px')
    })
  })

  describe('popoverStyle 计算属性', () => {
    it('无 target 时应返回空对象', () => {
      mountComponent()
      expect(wrapper.vm.popoverStyle).toEqual({})
    })

    it('有 target 但无 targetRect 时应返回空对象', () => {
      mountComponent()
      // currentStep 有 target，但 targetRect 为 null
      expect(wrapper.vm.popoverStyle).toEqual({})
    })

    it('placement 为 bottom 时应设置 top 和 left', () => {
      mountComponent()
      wrapper.vm.targetRect = {
        top: 100,
        left: 200,
        width: 50,
        height: 60,
        right: 250,
        bottom: 160,
        x: 200,
        y: 100,
        toJSON: () => ({}),
      } as DOMRect
      const style = wrapper.vm.popoverStyle
      expect(style).toHaveProperty('top')
      expect(style).toHaveProperty('left')
      expect(style).toHaveProperty('transform')
    })

    it('placement 为 top 时应设置 bottom', () => {
      mountComponent({
        steps: [
          { title: '步骤1', description: '描述1', target: '#target-1', placement: 'top' },
        ],
      })
      wrapper.vm.targetRect = {
        top: 100,
        left: 200,
        width: 50,
        height: 60,
        right: 250,
        bottom: 160,
        x: 200,
        y: 100,
        toJSON: () => ({}),
      } as DOMRect
      const style = wrapper.vm.popoverStyle
      expect(style).toHaveProperty('bottom')
    })

    it('placement 为 left 时应设置 right', () => {
      mountComponent({
        steps: [
          { title: '步骤1', description: '描述1', target: '#target-1', placement: 'left' },
        ],
      })
      wrapper.vm.targetRect = {
        top: 100,
        left: 200,
        width: 50,
        height: 60,
        right: 250,
        bottom: 160,
        x: 200,
        y: 100,
        toJSON: () => ({}),
      } as DOMRect
      const style = wrapper.vm.popoverStyle
      expect(style).toHaveProperty('right')
    })

    it('placement 为 right 时应设置 left', () => {
      mountComponent({
        steps: [
          { title: '步骤1', description: '描述1', target: '#target-1', placement: 'right' },
        ],
      })
      wrapper.vm.targetRect = {
        top: 100,
        left: 200,
        width: 50,
        height: 60,
        right: 250,
        bottom: 160,
        x: 200,
        y: 100,
        toJSON: () => ({}),
      } as DOMRect
      const style = wrapper.vm.popoverStyle
      expect(style).toHaveProperty('left')
    })
  })

  describe('start 方法', () => {
    it('应设置 visible 为 true', () => {
      mountComponent()
      wrapper.vm.start()
      expect(wrapper.vm.visible).toBe(true)
    })

    it('应触发 start 事件', async () => {
      mountComponent()
      wrapper.vm.start()
      await wrapper.vm.$nextTick()
      expect(wrapper.emitted('start')).toBeTruthy()
    })

    it('空步骤数组时不应启动', () => {
      mountComponent({ steps: [] })
      wrapper.vm.start()
      expect(wrapper.vm.visible).toBe(false)
      expect(wrapper.emitted('start')).toBeFalsy()
    })

    it('无保存进度时应从索引 0 开始', () => {
      mountComponent()
      wrapper.vm.start()
      expect(wrapper.vm.currentStepIndex).toBe(0)
    })

    it('有保存进度时应从保存的索引开始', () => {
      localStorage.setItem('tour_progress', JSON.stringify({
        currentStep: 1,
        timestamp: Date.now(),
      }))
      mountComponent()
      wrapper.vm.start()
      expect(wrapper.vm.currentStepIndex).toBe(1)
    })

    it('保存进度超出范围时应从 0 开始', () => {
      localStorage.setItem('tour_progress', JSON.stringify({
        currentStep: 99,
        timestamp: Date.now(),
      }))
      mountComponent()
      wrapper.vm.start()
      expect(wrapper.vm.currentStepIndex).toBe(0)
    })
  })

  describe('next 方法', () => {
    it('应增加 currentStepIndex', async () => {
      mountComponent()
      wrapper.vm.start()
      wrapper.vm.next()
      expect(wrapper.vm.currentStepIndex).toBe(1)
    })

    it('应触发 step-change 事件', async () => {
      mountComponent()
      wrapper.vm.start()
      wrapper.vm.next()
      await wrapper.vm.$nextTick()
      expect(wrapper.emitted('step-change')).toBeTruthy()
      expect(wrapper.emitted('step-change')![0]).toEqual([1])
    })

    it('到达最后一步时不应继续增加', () => {
      mountComponent()
      wrapper.vm.start()
      wrapper.vm.currentStepIndex = 2
      wrapper.vm.next()
      expect(wrapper.vm.currentStepIndex).toBe(2)
    })

    it('应保存进度到 localStorage', () => {
      mountComponent()
      wrapper.vm.start()
      wrapper.vm.next()
      const saved = localStorage.getItem('tour_progress')
      expect(saved).toBeTruthy()
      const data = JSON.parse(saved!)
      expect(data.currentStep).toBe(1)
    })
  })

  describe('prev 方法', () => {
    it('应减少 currentStepIndex', async () => {
      mountComponent()
      wrapper.vm.start()
      wrapper.vm.currentStepIndex = 1
      wrapper.vm.prev()
      expect(wrapper.vm.currentStepIndex).toBe(0)
    })

    it('应触发 step-change 事件', async () => {
      mountComponent()
      wrapper.vm.start()
      wrapper.vm.currentStepIndex = 1
      wrapper.vm.prev()
      await wrapper.vm.$nextTick()
      expect(wrapper.emitted('step-change')).toBeTruthy()
      expect(wrapper.emitted('step-change')![0]).toEqual([0])
    })

    it('在第一步时不应减少', () => {
      mountComponent()
      wrapper.vm.start()
      wrapper.vm.prev()
      expect(wrapper.vm.currentStepIndex).toBe(0)
    })
  })

  describe('skip 方法', () => {
    it('应设置 visible 为 false', () => {
      mountComponent()
      wrapper.vm.start()
      wrapper.vm.skip()
      expect(wrapper.vm.visible).toBe(false)
    })

    it('应触发 skip 事件并传递当前索引', () => {
      mountComponent()
      wrapper.vm.start()
      wrapper.vm.currentStepIndex = 1
      wrapper.vm.skip()
      expect(wrapper.emitted('skip')).toBeTruthy()
      expect(wrapper.emitted('skip')![0]).toEqual([1])
    })

    it('应清除 localStorage 进度', () => {
      localStorage.setItem('tour_progress', JSON.stringify({
        currentStep: 1,
        timestamp: Date.now(),
      }))
      mountComponent()
      wrapper.vm.start()
      wrapper.vm.skip()
      expect(localStorage.getItem('tour_progress')).toBeNull()
    })
  })

  describe('finish 方法', () => {
    it('应设置 visible 为 false', () => {
      mountComponent()
      wrapper.vm.start()
      wrapper.vm.finish()
      expect(wrapper.vm.visible).toBe(false)
    })

    it('应触发 finish 事件', () => {
      mountComponent()
      wrapper.vm.start()
      wrapper.vm.finish()
      expect(wrapper.emitted('finish')).toBeTruthy()
    })

    it('应清除 localStorage 进度', () => {
      localStorage.setItem('tour_progress', JSON.stringify({
        currentStep: 1,
        timestamp: Date.now(),
      }))
      mountComponent()
      wrapper.vm.start()
      wrapper.vm.finish()
      expect(localStorage.getItem('tour_progress')).toBeNull()
    })
  })

  describe('handleOverlayClick 方法', () => {
    it('调用后不应关闭引导', () => {
      mountComponent()
      wrapper.vm.start()
      wrapper.vm.handleOverlayClick()
      expect(wrapper.vm.visible).toBe(true)
    })
  })

  describe('updateTargetRect 方法', () => {
    it('当前步骤无 target 时应将 targetRect 设为 null', () => {
      mountComponent({
        steps: [{ title: '步骤', description: '描述' }],
      })
      wrapper.vm.start()
      wrapper.vm.updateTargetRect()
      expect(wrapper.vm.targetRect).toBeNull()
    })

    it('目标元素不存在时应将 targetRect 设为 null', () => {
      mountComponent()
      wrapper.vm.start()
      // querySelector 找不到 #target-1
      const spy = vi.spyOn(document, 'querySelector').mockReturnValue(null)
      wrapper.vm.updateTargetRect()
      expect(wrapper.vm.targetRect).toBeNull()
      spy.mockRestore()
    })

    it('目标元素存在时应设置 targetRect', () => {
      mountComponent()
      wrapper.vm.start()
      const mockElement = {
        getBoundingClientRect: () => ({
          top: 10, left: 20, width: 30, height: 40,
          right: 50, bottom: 50, x: 20, y: 10,
          toJSON: () => ({}),
        }),
        scrollIntoView: vi.fn(),
      }
      const spy = vi.spyOn(document, 'querySelector').mockReturnValue(mockElement as any)
      wrapper.vm.updateTargetRect()
      expect(wrapper.vm.targetRect).not.toBeNull()
      expect(wrapper.vm.targetRect.top).toBe(10)
      expect(mockElement.scrollIntoView).toHaveBeenCalled()
      spy.mockRestore()
    })
  })

  describe('saveProgress 方法', () => {
    it('应将当前进度保存到 localStorage', () => {
      mountComponent()
      wrapper.vm.currentStepIndex = 1
      wrapper.vm.saveProgress()
      const saved = localStorage.getItem('tour_progress')
      expect(saved).toBeTruthy()
      const data = JSON.parse(saved!)
      expect(data.currentStep).toBe(1)
      expect(data.timestamp).toBeDefined()
    })

    it('应使用自定义 storageKey', () => {
      mountComponent({ storageKey: 'custom_tour_key' })
      wrapper.vm.currentStepIndex = 2
      wrapper.vm.saveProgress()
      expect(localStorage.getItem('custom_tour_key')).toBeTruthy()
    })

    it('localStorage 异常时不应抛错', () => {
      mountComponent()
      const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
        throw new Error('QuotaExceededError')
      })
      expect(() => wrapper.vm.saveProgress()).not.toThrow()
      spy.mockRestore()
    })
  })

  describe('loadProgress 方法', () => {
    it('无保存数据时应返回 null', () => {
      mountComponent()
      expect(wrapper.vm.loadProgress()).toBeNull()
    })

    it('有有效保存数据时应返回索引', () => {
      localStorage.setItem('tour_progress', JSON.stringify({
        currentStep: 2,
        timestamp: Date.now(),
      }))
      mountComponent()
      expect(wrapper.vm.loadProgress()).toBe(2)
    })

    it('保存数据超过 7 天时应返回 null', () => {
      localStorage.setItem('tour_progress', JSON.stringify({
        currentStep: 1,
        timestamp: Date.now() - 8 * 24 * 60 * 60 * 1000,
      }))
      mountComponent()
      expect(wrapper.vm.loadProgress()).toBeNull()
    })

    it('保存数据损坏时应返回 null 并清理', () => {
      localStorage.setItem('tour_progress', 'invalid-json')
      mountComponent()
      expect(wrapper.vm.loadProgress()).toBeNull()
      expect(localStorage.getItem('tour_progress')).toBeNull()
    })

    it('localStorage 异常时应返回 null', () => {
      mountComponent()
      const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
        throw new Error('SecurityError')
      })
      expect(wrapper.vm.loadProgress()).toBeNull()
      spy.mockRestore()
    })
  })

  describe('clearProgress 方法', () => {
    it('应删除 localStorage 中的进度', () => {
      localStorage.setItem('tour_progress', JSON.stringify({
        currentStep: 1,
        timestamp: Date.now(),
      }))
      mountComponent()
      wrapper.vm.clearProgress()
      expect(localStorage.getItem('tour_progress')).toBeNull()
    })

    it('无保存数据时不应抛错', () => {
      mountComponent()
      expect(() => wrapper.vm.clearProgress()).not.toThrow()
    })

    it('localStorage 异常时不应抛错', () => {
      mountComponent()
      const spy = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
        throw new Error('SecurityError')
      })
      expect(() => wrapper.vm.clearProgress()).not.toThrow()
      spy.mockRestore()
    })
  })

  describe('handleResize 方法', () => {
    it('visible 为 true 时应调用 updateTargetRect', () => {
      mountComponent()
      // spyOn(vm) 不拦截 setup 内部调用；用 updateTargetRect 的副作用
      // （scrollIntoView）验证 handleResize → updateTargetRect 链路
      const scrollSpy = vi.fn()
      vi.spyOn(document, 'querySelector').mockReturnValue({
        getBoundingClientRect: () => ({ top: 0, left: 0, width: 100, height: 50 }),
        scrollIntoView: scrollSpy,
      } as unknown as Element)
      wrapper.vm.start()
      wrapper.vm.handleResize()
      expect(scrollSpy).toHaveBeenCalled()
      vi.restoreAllMocks()
    })

    it('visible 为 false 时不应调用 updateTargetRect', () => {
      mountComponent()
      const scrollSpy = vi.fn()
      vi.spyOn(document, 'querySelector').mockReturnValue({
        getBoundingClientRect: () => ({ top: 0, left: 0, width: 100, height: 50 }),
        scrollIntoView: scrollSpy,
      } as unknown as Element)
      wrapper.vm.handleResize()
      expect(scrollSpy).not.toHaveBeenCalled()
      vi.restoreAllMocks()
    })
  })

  describe('autoStart 配置', () => {
    it('autoStart 为 true 时应在 500ms 后自动启动', () => {
      mountComponent({ autoStart: true })
      expect(wrapper.vm.visible).toBe(false)
      vi.advanceTimersByTime(500)
      expect(wrapper.vm.visible).toBe(true)
      expect(wrapper.emitted('start')).toBeTruthy()
    })

    it('autoStart 为 false 时不应自动启动', () => {
      mountComponent({ autoStart: false })
      vi.advanceTimersByTime(1000)
      expect(wrapper.vm.visible).toBe(false)
    })

    it('autoStart 为 true 但步骤为空时不应启动', () => {
      mountComponent({ autoStart: true, steps: [] })
      vi.advanceTimersByTime(500)
      expect(wrapper.vm.visible).toBe(false)
    })
  })

  describe('生命周期', () => {
    it('挂载时应添加 resize 事件监听', () => {
      const spy = vi.spyOn(window, 'addEventListener')
      mountComponent()
      expect(spy).toHaveBeenCalledWith('resize', expect.any(Function))
      spy.mockRestore()
    })

    it('卸载时应移除 resize 事件监听', () => {
      const spy = vi.spyOn(window, 'removeEventListener')
      mountComponent()
      wrapper.unmount()
      expect(spy).toHaveBeenCalledWith('resize', expect.any(Function))
      spy.mockRestore()
    })

    it('autoStart 启动后卸载应清理定时器', () => {
      const spy = vi.spyOn(globalThis, 'clearTimeout')
      mountComponent({ autoStart: true })
      vi.advanceTimersByTime(300)
      wrapper.unmount()
      expect(spy).toHaveBeenCalled()
      spy.mockRestore()
    })
  })

  describe('defineExpose', () => {
    it('应暴露 start/next/prev/skip/finish 方法', () => {
      mountComponent()
      expect(typeof wrapper.vm.start).toBe('function')
      expect(typeof wrapper.vm.next).toBe('function')
      expect(typeof wrapper.vm.prev).toBe('function')
      expect(typeof wrapper.vm.skip).toBe('function')
      expect(typeof wrapper.vm.finish).toBe('function')
    })
  })
})
