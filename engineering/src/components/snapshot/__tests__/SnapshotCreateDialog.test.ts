/**
 * SnapshotCreateDialog.vue 组件测试
 *
 * 覆盖范围：
 *   1. 挂载与可见性（visible 控制 el-dialog 渲染）
 *   2. 打开时重置表单
 *   3. handleConfirm 校验链（config 空/非法 JSON、metrics 非法、dataset_versions 空）
 *   4. 提交成功 → emit('confirm', body)（解析/默认值）
 *   5. 取消按钮 → emit('update:visible', false)
 *   6. creating prop → 确认按钮 loading
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { shallowMount, VueWrapper } from '@vue/test-utils'

// Mock vue-i18n（$t 返回 key 本身）
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}))

// Mock element-plus（ElMessage + 组件存根——必须用 PascalCase 键覆盖 setup.ts 全局 stub）
const mockElMessage = vi.hoisted(() => ({
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  info: vi.fn(),
}))
vi.mock('element-plus', () => ({
  ElMessage: mockElMessage,
}))

import SnapshotCreateDialog from '@/components/snapshot/SnapshotCreateDialog.vue'

describe('SnapshotCreateDialog.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    wrapper?.unmount()
  })

  const mountComponent = (props: Record<string, unknown> = {}) => {
    wrapper = shallowMount(SnapshotCreateDialog, {
      props: {
        visible: true,
        creating: false,
        ...props,
      },
      global: {
        stubs: {
          ElDialog: {
            template:
              '<div v-if="modelValue" class="el-dialog"><slot /><slot name="footer" /></div>',
            props: ['modelValue', 'title', 'width', 'closeOnClickModal'],
            emits: ['update:modelValue'],
          },
          ElForm: { template: '<form class="el-form"><slot /></form>', props: ['model', 'labelWidth', 'labelPosition'] },
          ElFormItem: { template: '<div class="el-form-item"><slot /></div>', props: ['label'] },
          ElInput: {
            template:
              '<input class="el-input" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
            props: ['modelValue', 'type', 'rows', 'placeholder'],
            emits: ['update:modelValue'],
          },
          ElButton: {
            template:
              '<button class="el-button" :class="{ \'is-loading\': loading }" @click="$emit(\'click\')"><slot /></button>',
            props: ['type', 'loading'],
            emits: ['click'],
          },
        },
      },
    })
    return wrapper
  }

  describe('组件挂载', () => {
    it('visible 为 true 时渲染对话框', () => {
      mountComponent()
      expect(wrapper.find('.el-dialog').exists()).toBe(true)
    })

    it('visible 为 false 时不渲染对话框', () => {
      mountComponent({ visible: false })
      expect(wrapper.find('.el-dialog').exists()).toBe(false)
    })

    it('渲染 6 个表单项', () => {
      mountComponent()
      expect(wrapper.findAll('.el-form-item').length).toBe(6)
    })
  })

  describe('表单重置', () => {
    it('打开时重置表单（metricsStr 默认 {}）', async () => {
      wrapper = mountComponent()
      wrapper.vm.createForm.configStr = '{"lr": 0.001}'
      wrapper.vm.createForm.metricsStr = '{"val_loss": 0.1}'
      // 关闭再打开触发 watch
      await wrapper.setProps({ visible: false })
      await wrapper.setProps({ visible: true })
      expect(wrapper.vm.createForm.configStr).toBe('')
      expect(wrapper.vm.createForm.metricsStr).toBe('{}')
      expect(wrapper.vm.createForm.createdBy).toBe('')
    })
  })

  describe('handleConfirm 校验', () => {
    it('config 为空时提示 warning 且不提交', async () => {
      wrapper = mountComponent()
      wrapper.vm.createForm.configStr = '   '
      await wrapper.vm.handleConfirm()
      expect(mockElMessage.warning).toHaveBeenCalledWith('snapshotPanel.msgConfigEmpty')
      expect(wrapper.emitted('confirm')).toBeUndefined()
    })

    it('config 非法 JSON 时提示 warning 且不提交', async () => {
      wrapper = mountComponent()
      wrapper.vm.createForm.configStr = '{not-json'
      await wrapper.vm.handleConfirm()
      expect(mockElMessage.warning).toHaveBeenCalledWith('snapshotPanel.msgConfigInvalid')
      expect(wrapper.emitted('confirm')).toBeUndefined()
    })

    it('metrics 非法 JSON 时提示 warning 且不提交', async () => {
      wrapper = mountComponent()
      wrapper.vm.createForm.configStr = '{"lr": 0.001}'
      wrapper.vm.createForm.metricsStr = '{bad'
      await wrapper.vm.handleConfirm()
      expect(mockElMessage.warning).toHaveBeenCalledWith('snapshotPanel.msgMetricsInvalid')
      expect(wrapper.emitted('confirm')).toBeUndefined()
    })

    it('dataset_versions 为空时提示 warning 且不提交', async () => {
      wrapper = mountComponent()
      wrapper.vm.createForm.configStr = '{"lr": 0.001}'
      wrapper.vm.createForm.datasetVersionsStr = '\n  \n'
      await wrapper.vm.handleConfirm()
      expect(mockElMessage.warning).toHaveBeenCalledWith('snapshotPanel.msgDatasetVersionsEmpty')
      expect(wrapper.emitted('confirm')).toBeUndefined()
    })
  })

  describe('handleConfirm 提交', () => {
    it('提交成功时 emit confirm 携带解析后的 body', async () => {
      wrapper = mountComponent()
      wrapper.vm.createForm.configStr = '{"lr": 0.001}'
      wrapper.vm.createForm.datasetVersionsStr = 'dataset://phm2010/v1\ndataset://phm2010/v2'
      wrapper.vm.createForm.metricsStr = '{"val_loss": 0.06}'
      wrapper.vm.createForm.createdBy = 'user-1'
      wrapper.vm.createForm.notes = '备注'
      await wrapper.vm.handleConfirm()
      expect(wrapper.emitted('confirm')).toBeTruthy()
      expect(wrapper.emitted('confirm')![0]).toEqual([
        {
          config: { lr: 0.001 },
          dataset_versions: ['dataset://phm2010/v1', 'dataset://phm2010/v2'],
          model_uri: 'model://unknown',
          metrics: { val_loss: 0.06 },
          created_by: 'user-1',
          notes: '备注',
        },
      ])
    })

    it('model_uri 与 created_by 留空时使用默认值', async () => {
      wrapper = mountComponent()
      wrapper.vm.createForm.configStr = '{}'
      wrapper.vm.createForm.datasetVersionsStr = 'dataset://phm2010/v1'
      await wrapper.vm.handleConfirm()
      const body = wrapper.emitted('confirm')![0][0] as Record<string, unknown>
      expect(body.model_uri).toBe('model://unknown')
      expect(body.created_by).toBe('system:user')
      expect(body.notes).toBeUndefined()
    })
  })

  describe('按钮交互', () => {
    it('取消按钮触发 update:visible false', async () => {
      wrapper = mountComponent()
      const footerButtons = wrapper.findAll('.el-dialog .el-button')
      await footerButtons[0].trigger('click')
      expect(wrapper.emitted('update:visible')![0]).toEqual([false])
    })

    it('creating 为 true 时确认按钮 loading', () => {
      mountComponent({ creating: true })
      const buttons = wrapper.findAll('.el-dialog .el-button')
      expect(buttons[1].classes()).toContain('is-loading')
    })
  })
})
