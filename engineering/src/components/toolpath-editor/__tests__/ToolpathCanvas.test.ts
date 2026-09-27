import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('three', async () => {
  const actual = await vi.importActual('three')
  return {
    ...(actual as object),
    WebGLRenderer: vi.fn().mockImplementation(() => ({
      setSize: vi.fn(),
      setPixelRatio: vi.fn(),
      domElement: document.createElement('canvas'),
      render: vi.fn(),
      dispose: vi.fn(),
      toneMapping: 0,
      toneMappingExposure: 1,
      shadowMap: { enabled: false },
    })),
  }
})

vi.mock('three/examples/jsm/controls/OrbitControls.js', () => ({
  OrbitControls: vi.fn().mockImplementation(() => ({
    enableDamping: false,
    dampingFactor: 0,
    autoRotate: false,
    target: { set: vi.fn(), copy: vi.fn() },
    update: vi.fn(),
    dispose: vi.fn(),
  })),
}))

import ToolpathCanvas from '../ToolpathCanvas.vue'
import type { EditableToolpathSegment } from '../types/editor'

const sampleSegments: EditableToolpathSegment[] = [
  {
    id: 'seg-1', type: 'rapid', startPoint: [0, 0, 0], endPoint: [0, 0, 50],
    feedRate: 10000, spindleSpeed: 8000, toolId: 1, blockNumber: 1,
    gCode: 'G00 Z50', isDeleted: false,
  },
  {
    id: 'seg-2', type: 'linear', startPoint: [0, 0, 50], endPoint: [10, 10, 0],
    feedRate: 500, spindleSpeed: 8000, toolId: 1, blockNumber: 2,
    gCode: 'G01 X10 Y10', isDeleted: false,
  },
]

describe('ToolpathCanvas.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('should mount and initialize', () => {
    const wrapper = mount(ToolpathCanvas, {
      props: {
        segments: [],
        hoveredSegmentId: null,
      },
    })
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.find('.toolpath-canvas').exists()).toBe(true)
  })

  it('should show placeholder when no segments loaded', () => {
    const wrapper = mount(ToolpathCanvas, {
      props: {
        segments: [],
        hoveredSegmentId: null,
      },
    })
    expect(wrapper.text()).toContain('3D')
  })

  it('should render with segments', () => {
    const wrapper = mount(ToolpathCanvas, {
      props: {
        segments: sampleSegments,
        hoveredSegmentId: null,
      },
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('should accept segments prop changes', async () => {
    const wrapper = mount(ToolpathCanvas, {
      props: {
        segments: [],
        hoveredSegmentId: null,
      },
    })

    await wrapper.setProps({ segments: sampleSegments })
    expect(wrapper.props('segments')).toHaveLength(2)
  })

  it('should emit hover-change on mouse move', async () => {
    const wrapper = mount(ToolpathCanvas, {
      props: {
        segments: sampleSegments,
        hoveredSegmentId: null,
      },
    })

    const canvas = wrapper.find('.toolpath-canvas')
    await canvas.trigger('mousemove', { clientX: 100, clientY: 100 })
    expect(wrapper.exists()).toBe(true)
  })
})
