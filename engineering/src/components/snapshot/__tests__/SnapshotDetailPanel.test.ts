/**
 * SnapshotDetailPanel.vue 组件测试
 *
 * 覆盖范围：
 *   1. 无选中快照渲染 el-empty、无操作按钮
 *   2. 有选中渲染详情（id/标签/dataset uri/metrics/config）
 *   3. 操作按钮（reproduce/closeDetail）事件
 *   4. lineage_record_id / mlflow_run_id / notes 条件渲染
 *   5. formatTime 空值边界
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { shallowMount, VueWrapper } from '@vue/test-utils'
import type { ExperimentSnapshot } from '@/contracts/observability'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}))

vi.mock('@element-plus/icons-vue', () => ({
  VideoPlay: { name: 'VideoPlay', template: '<i class="icon-video-play" />' },
  Close: { name: 'Close', template: '<i class="icon-close" />' },
}))

import SnapshotDetailPanel from '@/components/snapshot/SnapshotDetailPanel.vue'

function makeSnapshot(overrides: Partial<ExperimentSnapshot> = {}): ExperimentSnapshot {
  return {
    snapshot_id: 'snap-001-abcdefgh',
    created_at: '2026-07-13T10:00:00Z',
    created_by: 'user-1',
    git_sha: 'abc123def456789',
    code_dirty: false,
    config: { lr: 0.001, epochs: 100 },
    dataset_versions: ['dataset://phm2010/v1', 'dataset://phm2010/v2'],
    model_uri: 'model://ltc-v1',
    metrics: { val_loss: 0.06, pcc: 0.51 },
    environment: { python: '3.10', torch: '2.0.1' },
    ...overrides,
  }
}

describe('SnapshotDetailPanel.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    wrapper?.unmount()
  })

  const mountComponent = (props: Record<string, unknown> = {}) => {
    wrapper = shallowMount(SnapshotDetailPanel, {
      props: {
        currentSnapshot: null,
        currentLoading: false,
        reproducing: false,
        ...props,
      },
      global: {
        stubs: {
          ElButton: {
            template: '<button class="el-button" @click="$emit(\'click\')"><slot /></button>',
            props: ['size', 'type', 'icon', 'loading'],
            emits: ['click'],
          },
          ElTag: { template: '<span class="el-tag"><slot /></span>', props: ['size', 'type'] },
          ElEmpty: { template: '<div class="el-empty" />', props: ['description', 'imageSize'] },
          ElDescriptions: {
            template: '<div class="el-descriptions"><slot /></div>',
            props: ['column', 'border'],
          },
          ElDescriptionsItem: {
            template:
              '<div class="el-descriptions-item"><span class="label">{{ label }}</span><span class="content"><slot /></span></div>',
            props: ['label'],
          },
        },
      },
    })
    return wrapper
  }

  describe('空状态', () => {
    it('无选中且非加载中时渲染 el-empty', () => {
      mountComponent()
      expect(wrapper.find('.el-empty').exists()).toBe(true)
    })

    it('加载中时不渲染 el-empty', () => {
      mountComponent({ currentLoading: true })
      expect(wrapper.find('.el-empty').exists()).toBe(false)
    })

    it('无选中时不渲染操作按钮', () => {
      mountComponent()
      expect(wrapper.find('.panel-header-actions').exists()).toBe(false)
    })
  })

  describe('详情渲染', () => {
    it('有选中时渲染详情内容', () => {
      mountComponent({ currentSnapshot: makeSnapshot() })
      expect(wrapper.find('.snapshot-detail-content').exists()).toBe(true)
      expect(wrapper.find('.el-descriptions-item').exists()).toBe(true)
    })

    it('渲染 dataset uri 列表', () => {
      mountComponent({ currentSnapshot: makeSnapshot() })
      expect(wrapper.findAll('.uri-item').length).toBe(2)
      expect(wrapper.find('.uri-item').text()).toBe('dataset://phm2010/v1')
    })

    it('dataset_versions 为空时渲染 -', () => {
      mountComponent({
        currentSnapshot: makeSnapshot({ dataset_versions: [] }),
      })
      expect(wrapper.find('.uri-item').exists()).toBe(false)
    })

    it('渲染 metrics JSON 块', () => {
      mountComponent({ currentSnapshot: makeSnapshot() })
      expect(wrapper.find('.json-block').text()).toContain('val_loss')
    })

    it('渲染 config 区块', () => {
      mountComponent({ currentSnapshot: makeSnapshot() })
      expect(wrapper.find('.config-section').exists()).toBe(true)
      expect(wrapper.find('.config-section .json-block').text()).toContain('epochs')
    })
  })

  describe('条件字段', () => {
    it('有 lineage_record_id 时渲染对应项', () => {
      mountComponent({
        currentSnapshot: makeSnapshot({ lineage_record_id: 'lineage-1' }),
      })
      const items = wrapper.findAll('.el-descriptions-item')
      expect(
        items.some((i) => i.find('.label').text() === 'snapshotPanel.detailLineageRecord'),
      ).toBe(true)
    })

    it('无 lineage_record_id 时不渲染对应项', () => {
      mountComponent({ currentSnapshot: makeSnapshot() })
      const items = wrapper.findAll('.el-descriptions-item')
      expect(
        items.some((i) => i.find('.label').text() === 'snapshotPanel.detailLineageRecord'),
      ).toBe(false)
    })

    it('有 notes 时渲染备注', () => {
      mountComponent({
        currentSnapshot: makeSnapshot({ notes: '测试备注' }),
      })
      expect(wrapper.find('.el-descriptions').text()).toContain('测试备注')
    })
  })

  describe('操作按钮', () => {
    it('点击复现按钮触发 reproduce 事件', async () => {
      mountComponent({ currentSnapshot: makeSnapshot() })
      const buttons = wrapper.findAll('.panel-header-actions .el-button')
      await buttons[0].trigger('click')
      expect(wrapper.emitted('reproduce')).toBeTruthy()
    })

    it('点击关闭按钮触发 closeDetail 事件', async () => {
      mountComponent({ currentSnapshot: makeSnapshot() })
      const buttons = wrapper.findAll('.panel-header-actions .el-button')
      await buttons[1].trigger('click')
      expect(wrapper.emitted('closeDetail')).toBeTruthy()
    })
  })

  describe('工具函数边界', () => {
    it('formatTime 处理空值', () => {
      mountComponent()
      expect(wrapper.vm.formatTime(undefined)).toBe('-')
    })
  })
})
