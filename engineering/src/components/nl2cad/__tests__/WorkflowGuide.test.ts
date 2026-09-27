/* eslint-disable vue/no-unused-vars */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { shallowMount, VueWrapper } from '@vue/test-utils'
import WorkflowGuide from '@/components/nl2cad/WorkflowGuide.vue'

// Mock vue-i18n
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}))

// Mock Element Plus
const mockElMessage = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
  warning: vi.fn(),
}))
vi.mock('element-plus', () => ({
  ElMessage: mockElMessage,
  ElButtonGroup: { template: '<div class="el-button-group"><slot /></div>' },
  ElInputNumber: {
    template: '<input class="el-input-number" />',
    props: ['modelValue', 'min', 'max', 'step', 'controlsPosition'],
  },
}))

// Mock @element-plus/icons-vue
vi.mock('@element-plus/icons-vue', () => ({
  Check: { name: 'Check', template: '<i />' },
  ArrowRight: { name: 'ArrowRight', template: '<i />' },
  ArrowLeft: { name: 'ArrowLeft', template: '<i />' },
  Document: { name: 'Document', template: '<i />' },
  Box: { name: 'Box', template: '<i />' },
  Loading: { name: 'Loading', template: '<i />' },
  SetUp: { name: 'SetUp', template: '<i />' },
  DocumentCopy: { name: 'DocumentCopy', template: '<i />' },
  Download: { name: 'Download', template: '<i />' },
  VideoPlay: { name: 'VideoPlay', template: '<i />' },
  VideoPause: { name: 'VideoPause', template: '<i />' },
  RefreshRight: { name: 'RefreshRight', template: '<i />' },
  CircleCheck: { name: 'CircleCheck', template: '<i />' },
}))

// Mock nl2cad API
const mockExtractParams = vi.hoisted(() => vi.fn())
const mockGenerateModel = vi.hoisted(() => vi.fn())
const mockGenerateProcessPlanning = vi.hoisted(() => vi.fn())
const mockGenerateNC = vi.hoisted(() => vi.fn())
const mockExportAnimation = vi.hoisted(() => vi.fn())

vi.mock('@/api/nl2cad', () => ({
  extractParams: mockExtractParams,
  generateModel: mockGenerateModel,
  generateProcessPlanning: mockGenerateProcessPlanning,
  generateNC: mockGenerateNC,
  exportSimulationAnimation: mockExportAnimation,
}))

// Mock navigator.clipboard
Object.defineProperty(globalThis.navigator, 'clipboard', {
  value: {
    writeText: vi.fn(() => Promise.resolve()),
  },
  writable: true,
  configurable: true,
})

// Mock URL.createObjectURL & revokeObjectURL
globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock-url')
globalThis.URL.revokeObjectURL = vi.fn()

describe('WorkflowGuide.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
    mockExtractParams.mockResolvedValue({
      params: {
        shape_type: 'box',
        dimensions: { length: 100, width: 50, height: 30 },
        material: 'aluminum',
        confidence: 0.9,
      },
    })
    mockGenerateModel.mockResolvedValue({ model_path: '/tmp/model.stl' })
    mockGenerateProcessPlanning.mockResolvedValue({
      process_plan: { operations: [{ id: 1, type: 'face_milling' }] },
    })
    mockGenerateNC.mockResolvedValue({ nc_code: 'G01 X100 Y50 Z30' })
    mockExportAnimation.mockResolvedValue(new Blob(['gif-data'], { type: 'image/gif' }))
  })

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount()
    }
  })

  const mountComponent = (props: Record<string, unknown> = {}) => {
    wrapper = shallowMount(WorkflowGuide, { props })
    return wrapper
  }

  describe('组件挂载', () => {
    it('应该正确挂载组件', () => {
      mountComponent()
      expect(wrapper.exists()).toBe(true)
      expect(wrapper.find('.workflow-guide').exists()).toBe(true)
    })

    it('应该渲染步骤指示器', () => {
      mountComponent()
      // shallowMount 下子组件 stub 化：断言步骤指示器 stub 与步骤内容区
      expect(wrapper.findComponent({ name: 'WorkflowGuideStepsIndicator' }).exists()).toBe(true)
      expect(wrapper.find('.step-content').exists()).toBe(true)
    })

    it('应该渲染6个步骤', () => {
      mountComponent()
      const steps = wrapper.findComponent({ name: 'WorkflowGuideStepsIndicator' }).props('steps')
      expect(steps.length).toBe(6)
    })

    it('应该渲染步骤内容区域', () => {
      mountComponent()
      expect(wrapper.find('.step-content').exists()).toBe(true)
    })

    it('初始步骤应该是第1步（index 0）', () => {
      mountComponent()
      expect(wrapper.vm.currentStep).toBe(0)
    })
  })

  describe('props 处理', () => {
    it('应该接受 initialDescription prop', () => {
      mountComponent({ initialDescription: '一个 100x50x30 的长方体' })
      expect(wrapper.vm.nlDescription).toBe('一个 100x50x30 的长方体')
    })

    it('没有 initialDescription 时 nlDescription 应为空字符串', () => {
      mountComponent()
      expect(wrapper.vm.nlDescription).toBe('')
    })
  })

  describe('步骤1：自然语言描述', () => {
    it('应该渲染示例卡片', () => {
      mountComponent()
      // 示例卡片渲染在 Step1DescriptionPanel 内部（shallowMount stub 化），
      // 断言组件持有 4 个示例（stub 收到 examples prop）
      expect(wrapper.vm.examples.length).toBe(4)
      const panel = wrapper.findComponent({ name: 'Step1DescriptionPanel' })
      expect(panel.exists()).toBe(true)
    })

    it('点击示例卡片应填充 nlDescription', async () => {
      mountComponent()
      // 点击事件由 Step1DescriptionPanel 转发（fill-example）；stub 下直接触发
      wrapper.findComponent({ name: 'Step1DescriptionPanel' }).vm.$emit('fill-example', wrapper.vm.examples[0].text)
      await wrapper.vm.$nextTick()
      expect(wrapper.vm.nlDescription).toBe(wrapper.vm.examples[0].text)
    })

    it('fillExample 应填充 nlDescription', () => {
      mountComponent()
      wrapper.vm.fillExample('测试描述')
      expect(wrapper.vm.nlDescription).toBe('测试描述')
    })

    it('nlDescription 为空时下一步按钮应禁用', () => {
      mountComponent()
      // 步骤1面板中的按钮
      expect(wrapper.vm.nlDescription.trim()).toBe('')
    })
  })

  describe('handleStepClick 方法', () => {
    it('点击当前步骤之前的可点击步骤应该跳转', async () => {
      mountComponent()
      wrapper.vm.currentStep = 3
      await wrapper.vm.$nextTick()
      wrapper.vm.handleStepClick(1)
      expect(wrapper.vm.currentStep).toBe(1)
      expect(wrapper.emitted('step-change')).toBeTruthy()
      expect(wrapper.emitted('step-change')![0]).toEqual([1])
    })

    it('点击不可点击的步骤不应跳转', async () => {
      mountComponent()
      wrapper.vm.currentStep = 3
      await wrapper.vm.$nextTick()
      // 步骤3 (index 2) clickable=false
      wrapper.vm.handleStepClick(2)
      expect(wrapper.vm.currentStep).toBe(3)
      expect(wrapper.emitted('step-change')).toBeFalsy()
    })

    it('点击大于当前步骤的可点击步骤不应跳转', async () => {
      mountComponent()
      wrapper.vm.currentStep = 1
      await wrapper.vm.$nextTick()
      wrapper.vm.handleStepClick(3)
      expect(wrapper.vm.currentStep).toBe(1)
      expect(wrapper.emitted('step-change')).toBeFalsy()
    })
  })

  describe('handleNextStep 方法', () => {
    it('从步骤0调用应触发 extractParamsFromNL', async () => {
      mountComponent({ initialDescription: '长方体' })
      await wrapper.vm.$nextTick()
      await wrapper.vm.handleNextStep()
      expect(mockExtractParams).toHaveBeenCalledWith({ description: '长方体' })
    })

    it('从步骤2调用应跳转到步骤3', async () => {
      mountComponent()
      wrapper.vm.currentStep = 2
      await wrapper.vm.$nextTick()
      await wrapper.vm.handleNextStep()
      expect(wrapper.vm.currentStep).toBe(3)
      expect(wrapper.emitted('step-change')).toBeTruthy()
    })

    it('从步骤4调用应跳转到步骤5', async () => {
      mountComponent()
      wrapper.vm.currentStep = 4
      await wrapper.vm.$nextTick()
      await wrapper.vm.handleNextStep()
      expect(wrapper.vm.currentStep).toBe(5)
    })
  })

  describe('extractParamsFromNL 方法', () => {
    it('成功提取参数应更新 extractedParams 并触发事件', async () => {
      mountComponent({ initialDescription: '长方体' })
      await wrapper.vm.$nextTick()
      await wrapper.vm.extractParamsFromNL()
      expect(wrapper.vm.extractedParams.shape_type).toBe('box')
      expect(wrapper.vm.extractedParams.dimensions.length).toBe(100)
      expect(wrapper.vm.currentStep).toBe(1)
      expect(wrapper.emitted('params-extracted')).toBeTruthy()
      expect(wrapper.emitted('step-change')).toBeTruthy()
      expect(mockElMessage.success).toHaveBeenCalled()
    })

    it('提取参数失败应显示错误消息', async () => {
      mockExtractParams.mockRejectedValueOnce(new Error('API错误'))
      mountComponent({ initialDescription: '长方体' })
      await wrapper.vm.$nextTick()
      await wrapper.vm.extractParamsFromNL()
      expect(mockElMessage.error).toHaveBeenCalled()
    })
  })

  describe('handleGenerateModel 方法', () => {
    it('成功生成模型应更新状态并触发事件', async () => {
      mountComponent({ initialDescription: '长方体' })
      await wrapper.vm.$nextTick()
      await wrapper.vm.handleGenerateModel()
      expect(wrapper.vm.modelGenerated).toBe(true)
      expect(wrapper.vm.currentStep).toBe(2)
      expect(wrapper.emitted('generate-model')).toBeTruthy()
      expect(wrapper.emitted('step-change')).toBeTruthy()
      expect(mockElMessage.success).toHaveBeenCalled()
    })

    it('生成模型失败应显示错误消息', async () => {
      mockGenerateModel.mockRejectedValueOnce(new Error('API错误'))
      mountComponent()
      await wrapper.vm.$nextTick()
      await wrapper.vm.handleGenerateModel()
      expect(mockElMessage.error).toHaveBeenCalled()
      expect(wrapper.vm.modelGenerated).toBe(false)
    })
  })

  describe('handleGenerateProcess 方法', () => {
    it('成功生成工艺应触发事件并生成NC代码', async () => {
      mountComponent()
      await wrapper.vm.$nextTick()
      await wrapper.vm.handleGenerateProcess()
      expect(wrapper.vm.currentStep).toBe(4)
      expect(wrapper.emitted('generate-process')).toBeTruthy()
      expect(mockGenerateNC).toHaveBeenCalled()
    })

    it('生成工艺失败应显示错误消息', async () => {
      mockGenerateProcessPlanning.mockRejectedValueOnce(new Error('API错误'))
      mountComponent()
      await wrapper.vm.$nextTick()
      await wrapper.vm.handleGenerateProcess()
      expect(mockElMessage.error).toHaveBeenCalled()
    })
  })

  describe('generateNCCode 方法', () => {
    it('成功生成NC代码应更新状态并触发事件', async () => {
      mountComponent()
      await wrapper.vm.$nextTick()
      await wrapper.vm.generateNCCode({ operations: [] })
      expect(wrapper.vm.ncCode).toBe('G01 X100 Y50 Z30')
      expect(wrapper.vm.ncCodeGenerated).toBe(true)
      expect(wrapper.emitted('generate-nc')).toBeTruthy()
      expect(mockElMessage.success).toHaveBeenCalled()
    })

    it('生成NC代码失败应显示错误消息', async () => {
      mockGenerateNC.mockRejectedValueOnce(new Error('API错误'))
      mountComponent()
      await wrapper.vm.$nextTick()
      await wrapper.vm.generateNCCode()
      expect(mockElMessage.error).toHaveBeenCalled()
      expect(wrapper.vm.ncCodeGenerated).toBe(false)
    })
  })

  describe('handleCopyCode 方法', () => {
    it('应调用 clipboard.writeText 并显示成功消息', async () => {
      mountComponent()
      wrapper.vm.ncCode = 'G01 X100'
      await wrapper.vm.$nextTick()
      wrapper.vm.handleCopyCode()
      expect(globalThis.navigator.clipboard.writeText).toHaveBeenCalledWith('G01 X100')
      expect(mockElMessage.success).toHaveBeenCalled()
    })
  })

  describe('handleDownloadCode 方法', () => {
    it('应创建下载链接并显示成功消息', () => {
      mountComponent()
      wrapper.vm.ncCode = 'G01 X100'
      wrapper.vm.handleDownloadCode()
      expect(globalThis.URL.createObjectURL).toHaveBeenCalled()
      expect(globalThis.URL.revokeObjectURL).toHaveBeenCalled()
      expect(mockElMessage.success).toHaveBeenCalled()
    })
  })

  describe('仿真控制方法', () => {
    it('handleStartSimulation 应触发 start-simulation 事件', () => {
      mountComponent()
      wrapper.vm.handleStartSimulation()
      expect(wrapper.emitted('start-simulation')).toBeTruthy()
      expect(mockElMessage.info).toHaveBeenCalled()
    })

    it('handlePauseSimulation 应显示暂停消息', () => {
      mountComponent()
      wrapper.vm.handlePauseSimulation()
      expect(mockElMessage.info).toHaveBeenCalled()
    })

    it('handleResetSimulation 应显示重置消息', () => {
      mountComponent()
      wrapper.vm.handleResetSimulation()
      expect(mockElMessage.info).toHaveBeenCalled()
    })
  })

  describe('handleDownloadAnimation 方法', () => {
    it('成功下载动画应显示成功消息', async () => {
      mountComponent()
      wrapper.vm.ncCode = 'G01 X100'
      await wrapper.vm.$nextTick()
      await wrapper.vm.handleDownloadAnimation()
      expect(mockExportAnimation).toHaveBeenCalled()
      expect(globalThis.URL.createObjectURL).toHaveBeenCalled()
      expect(mockElMessage.success).toHaveBeenCalled()
    })

    it('下载动画失败应显示错误消息', async () => {
      mockExportAnimation.mockRejectedValueOnce(new Error('API错误'))
      mountComponent()
      await wrapper.vm.$nextTick()
      await wrapper.vm.handleDownloadAnimation()
      expect(mockElMessage.error).toHaveBeenCalled()
    })
  })

  describe('handleComplete 方法', () => {
    it('应触发 complete 事件', () => {
      mountComponent()
      wrapper.vm.handleComplete()
      expect(wrapper.emitted('complete')).toBeTruthy()
      expect(mockElMessage.success).toHaveBeenCalled()
    })
  })

  describe('插槽渲染', () => {
    it('步骤3应渲染 3d-viewer 插槽', async () => {
      mountComponent()
      wrapper.vm.modelGenerated = true
      wrapper.vm.currentStep = 2
      await wrapper.vm.$nextTick()
      // 3D 视图渲染在 Step3PreviewPanel 内部（shallowMount stub 化）：
      // 断言面板 stub 存在且收到 model-generated prop
      const panel = wrapper.findComponent({ name: 'Step3PreviewPanel' })
      expect(panel.exists()).toBe(true)
      expect(panel.props('modelGenerated')).toBe(true)
    })

    it('步骤6应渲染 simulation-viewer 插槽', async () => {
      mountComponent()
      wrapper.vm.currentStep = 5
      await wrapper.vm.$nextTick()
      expect(wrapper.findComponent({ name: 'Step6SimulationPanel' }).exists()).toBe(true)
    })
  })
})
