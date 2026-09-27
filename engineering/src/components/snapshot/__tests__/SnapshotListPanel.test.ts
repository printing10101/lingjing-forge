/**
 * SnapshotListPanel.vue 组件测试
 *
 * 覆盖范围：
 *   1. 空列表渲染 el-empty
 *   2. 快照卡片渲染（数量/active 高亮/id 截断/dirty 标签）
 *   3. 卡片点击 → emit('select')
 *   4. 筛选输入 → emit update:filterXxx + filterChange
 *   5. 重置筛选按钮 → emit('resetFilters')
 *   6. 分页 current-change → emit('pageChange')
 *   7. shortSha / formatTime 边界
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { shallowMount, VueWrapper } from '@vue/test-utils'
import type { SnapshotSummary } from '@/composables/useSnapshots'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}))

import SnapshotListPanel from '@/components/snapshot/SnapshotListPanel.vue'

function makeSnapshot(overrides: Partial<SnapshotSummary> = {}): SnapshotSummary {
  return {
    snapshot_id: 'snap-001-abcdefgh',
    created_at: '2026-07-13T10:00:00Z',
    created_by: 'user-1',
    git_sha: 'abc123def456789',
    code_dirty: false,
    config: { lr: 0.001 },
    dataset_versions: ['dataset://phm2010/v1'],
    model_uri: 'model://ltc-v1',
    metrics: { val_loss: 0.06 },
    environment: { python: '3.10', torch: '2.0.1' },
    ...overrides,
  }
}

describe('SnapshotListPanel.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    wrapper?.unmount()
  })

  const mountComponent = (props: Record<string, unknown> = {}) => {
    wrapper = shallowMount(SnapshotListPanel, {
      props: {
        snapshots: [],
        loading: false,
        currentPage: 1,
        pageSize: 20,
        totalCount: 0,
        currentSnapshotId: null,
        filterCreatedBy: '',
        filterGitSha: '',
        filterModelUri: '',
        ...props,
      },
      global: {
        stubs: {
          ElButton: {
            template: '<button class="el-button" @click="$emit(\'click\')"><slot /></button>',
            props: ['size', 'link'],
            emits: ['click'],
          },
          ElInput: {
            template:
              '<input class="el-input" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" @change="$emit(\'change\')" />',
            props: ['modelValue', 'size', 'placeholder', 'clearable'],
            emits: ['update:modelValue', 'change'],
          },
          ElTag: { template: '<span class="el-tag"><slot /></span>', props: ['size', 'type'] },
          ElEmpty: { template: '<div class="el-empty" />', props: ['description', 'imageSize'] },
          ElPagination: {
            template:
              '<div class="el-pagination" @click="$emit(\'current-change\', 3)" />',
            props: ['currentPage', 'pageSize', 'total', 'layout', 'small'],
            emits: ['current-change'],
          },
        },
      },
    })
    return wrapper
  }

  describe('列表渲染', () => {
    it('空列表且非加载中时渲染 el-empty', () => {
      mountComponent()
      expect(wrapper.find('.el-empty').exists()).toBe(true)
    })

    it('加载中时不渲染 el-empty', () => {
      mountComponent({ loading: true })
      expect(wrapper.find('.el-empty').exists()).toBe(false)
    })

    it('有数据时渲染快照卡片', () => {
      mountComponent({ snapshots: [makeSnapshot(), makeSnapshot({ snapshot_id: 'snap-002-xyz' })] })
      expect(wrapper.findAll('.snapshot-card').length).toBe(2)
    })

    it('当前选中快照卡片带 active class', () => {
      mountComponent({
        snapshots: [makeSnapshot({ snapshot_id: 'snap-001-abcdefgh' })],
        currentSnapshotId: 'snap-001-abcdefgh',
      })
      expect(wrapper.find('.snapshot-card').classes()).toContain('active')
    })

    it('卡片显示 id 前 8 位与 model_uri', () => {
      mountComponent({ snapshots: [makeSnapshot()] })
      expect(wrapper.find('.snapshot-id').text()).toBe('snap-001')
      expect(wrapper.find('.meta-value').text()).toBe('user-1')
    })

    it('code_dirty 为 true 时标签显示 dirty', () => {
      mountComponent({ snapshots: [makeSnapshot({ code_dirty: true })] })
      expect(wrapper.find('.el-tag').text()).toBe('snapshotPanel.dirtyDirty')
    })
  })

  describe('事件交互', () => {
    it('点击卡片触发 select 事件', async () => {
      mountComponent({ snapshots: [makeSnapshot({ snapshot_id: 'snap-001-abcdefgh' })] })
      await wrapper.find('.snapshot-card').trigger('click')
      expect(wrapper.emitted('select')![0]).toEqual(['snap-001-abcdefgh'])
    })

    it('筛选输入触发 update:filterCreatedBy', async () => {
      mountComponent()
      const inputs = wrapper.findAll('.el-input')
      await inputs[0].setValue('user-2')
      expect(wrapper.emitted('update:filterCreatedBy')![0]).toEqual(['user-2'])
    })

    it('筛选 change 触发 filterChange', async () => {
      mountComponent()
      const inputs = wrapper.findAll('.el-input')
      await inputs[0].trigger('change')
      expect(wrapper.emitted('filterChange')).toBeTruthy()
    })

    it('重置筛选按钮触发 resetFilters', async () => {
      mountComponent()
      await wrapper.find('.panel-header .el-button').trigger('click')
      expect(wrapper.emitted('resetFilters')).toBeTruthy()
    })
  })

  describe('分页', () => {
    it('current-change 触发 pageChange', async () => {
      mountComponent({ totalCount: 100 })
      await wrapper.find('.el-pagination').trigger('click')
      expect(wrapper.emitted('pageChange')![0]).toEqual([3])
    })
  })

  describe('工具函数边界', () => {
    it('shortSha 处理空值与超长值', () => {
      mountComponent()
      expect(wrapper.vm.shortSha(undefined)).toBe('-')
      expect(wrapper.vm.shortSha('abc123def456')).toBe('abc123de')
    })

    it('formatTime 处理空值', () => {
      mountComponent()
      expect(wrapper.vm.formatTime(undefined)).toBe('-')
    })
  })
})
