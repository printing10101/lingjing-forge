/**
 * DxfParseStats.vue 组件测试
 *
 * 覆盖范围：
 *   1. 4 个统计卡片（lines/arcs/circles/features——toLocaleString 格式化）
 *   2. 文件元信息描述（file_name/file_size/dxf_version/parse_time/total_entities）
 *   3. dxf_version 为空时显示 -
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { shallowMount, VueWrapper } from '@vue/test-utils'
import type { DxfParseResponse } from '@/types'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}))

vi.mock('@/utils/formatters', () => ({
  formatFileSize: vi.fn((size: number) => `${size} B`),
}))

import DxfParseStats from '@/components/dxf_import/DxfParseStats.vue'

function makeParseResult(overrides: Partial<DxfParseResponse> = {}): DxfParseResponse {
  return {
    file_id: 'f-001',
    file_name: 'drawing.dxf',
    file_size: 2048,
    lines_count: 120,
    arcs_count: 30,
    circles_count: 15,
    texts_count: 0,
    dimensions_count: 2,
    total_entities: 165,
    dxf_version: 'AC1015',
    parse_time_ms: 42,
    entity_counts: {},
    extents: { min_x: 0, min_y: 0, max_x: 10, max_y: 10 },
    lines: [],
    circles: [],
    dimensions: [],
    warnings: [],
    ...overrides,
  }
}

describe('DxfParseStats.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    wrapper?.unmount()
  })

  const mountComponent = (props: Record<string, unknown> = {}) => {
    wrapper = shallowMount(DxfParseStats, {
      props: {
        parseResult: makeParseResult(),
        featuresCount: 5,
        ...props,
      },
      global: {
        stubs: {
          ElDescriptions: {
            template: '<div class="el-descriptions"><slot /></div>',
            props: ['column', 'border', 'size'],
          },
          ElDescriptionsItem: {
            template:
              '<div class="el-descriptions-item"><span class="label">{{ label }}</span><span class="content"><slot /></span></div>',
            props: ['label', 'span'],
          },
        },
      },
    })
    return wrapper
  }

  describe('统计卡片', () => {
    it('渲染 4 个统计卡片', () => {
      mountComponent()
      expect(wrapper.findAll('.stat-card').length).toBe(4)
    })

    it('显示 lines/arcs/circles 统计值（toLocaleString）', () => {
      mountComponent()
      const values = wrapper.findAll('.stat-value').map((v) => v.text())
      expect(values).toContain('120')
      expect(values).toContain('30')
      expect(values).toContain('15')
    })

    it('显示 featuresCount（highlight 卡片）', () => {
      mountComponent({ featuresCount: 8 })
      const values = wrapper.findAll('.stat-value').map((v) => v.text())
      expect(values).toContain('8')
      expect(wrapper.find('.stat-card.highlight').exists()).toBe(true)
    })
  })

  describe('文件元信息', () => {
    it('显示文件名与格式化大小', () => {
      mountComponent()
      const items = wrapper.findAll('.el-descriptions-item')
      expect(items.some((i) => i.text().includes('drawing.dxf'))).toBe(true)
      expect(items.some((i) => i.text().includes('2048 B'))).toBe(true)
    })

    it('显示 dxf_version 与 parse_time', () => {
      mountComponent()
      const items = wrapper.findAll('.el-descriptions-item')
      expect(items.some((i) => i.text().includes('AC1015'))).toBe(true)
      expect(items.some((i) => i.text().includes('42 ms'))).toBe(true)
    })

    it('dxf_version 为空时显示 -', () => {
      mountComponent({ parseResult: makeParseResult({ dxf_version: undefined }) })
      const items = wrapper.findAll('.el-descriptions-item')
      expect(items.some((i) => i.text().includes('-'))).toBe(true)
    })

    it('显示 total_entities', () => {
      mountComponent()
      const items = wrapper.findAll('.el-descriptions-item')
      expect(items.some((i) => i.text().includes('165'))).toBe(true)
    })
  })
})
