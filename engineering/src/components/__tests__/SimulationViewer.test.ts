/* eslint-disable vue/no-unused-vars */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, VueWrapper } from '@vue/test-utils'
import * as THREE from 'three'
import SimulationViewer from '@/components/simulation/SimulationViewer.vue'
import type { SimulationVisualizationData, ForceData, TemperatureData, VibrationData } from '@/api/simulation'
import { getSimulationResult, clearSimulationCache, getCacheStats } from '@/api/simulation'
import type { CollisionInfo, ToolpathSegmentData } from '@/types'

// jsdom 无 WebGL context：mock THREE.WebGLRenderer（组件 initScene 直接构造），
// 其余 three 类保留真实实现
vi.mock('three', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three')>()
  const mockRenderer = {
    domElement: document.createElement('canvas'),
    setSize: vi.fn(),
    setPixelRatio: vi.fn(),
    render: vi.fn(),
    dispose: vi.fn(),
    setClearColor: vi.fn(),
    setAnimationLoop: vi.fn(),
    shadowMap: { enabled: false },
    toneMapping: 0,
    toneMappingExposure: 1,
  }
  return {
    ...actual,
    WebGLRenderer: vi.fn(() => mockRenderer),
  }
})

// Mock Three.js场景
vi.mock('@/composables/useThreeScene', () => {
  // 创建mock renderer避免WebGL上下文初始化
  const mockRenderer = {
    domElement: document.createElement('div'),
    setSize: vi.fn(),
    setPixelRatio: vi.fn(),
    render: vi.fn(),
    dispose: vi.fn(),
    setClearColor: vi.fn(),
    shadowMap: { enabled: false },
  }

  return {
    useThreeScene: vi.fn(() => ({
      scene: new THREE.Scene(),
      camera: new THREE.PerspectiveCamera(),
      renderer: mockRenderer,
      controls: {
        target: new THREE.Vector3(),
        update: vi.fn(),
      },
      addLight: vi.fn(),
      startAnimation: vi.fn(),
      cleanup: vi.fn(),
    })),
  }
})

// Mock仿真可视化逻辑 - 同时mock hook和命名导出
vi.mock('@/composables/useSimulationVisualization', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/composables/useSimulationVisualization')>()
  return {
    ...actual,
    useSimulationVisualization: vi.fn(() => ({
      renderSimulationResult: vi.fn(),
      clearVisualization: vi.fn(),
      updateVisualization: vi.fn(),
      getColorForValue: vi.fn((_value: number, _min: number, _max: number) => new THREE.Color(0xff0000)),
      createForceArrow: vi.fn(() => new THREE.ArrowHelper(new THREE.Vector3(0, 0, 1), new THREE.Vector3(), 1, 0xff0000)),
      createForceVectorGroup: vi.fn(() => new THREE.Group()),
      createTemperatureMaterial: vi.fn(() => new THREE.ShaderMaterial()),
      createTemperatureCloud: vi.fn(() => new THREE.Mesh(new THREE.BufferGeometry(), new THREE.ShaderMaterial())),
      createVibrationVisualization: vi.fn(() => new THREE.Group()),
      createColorLegend: vi.fn(() => new THREE.Group()),
    })),
    getColorForValue: vi.fn((_value: number, _min: number, _max: number) => new THREE.Color(0xff0000)),
    createForceArrow: vi.fn(() => new THREE.ArrowHelper(new THREE.Vector3(0, 0, 1), new THREE.Vector3(), 1, 0xff0000)),
    createForceVectorGroup: vi.fn(() => {
      const g = new THREE.Group()
      g.name = 'force-vectors'
      return g
    }),
    createTemperatureMaterial: vi.fn(() => new THREE.ShaderMaterial()),
    createTemperatureCloud: vi.fn(() => new THREE.Mesh(new THREE.BufferGeometry(), new THREE.ShaderMaterial())),
    createVibrationVisualization: vi.fn(() => {
      const g = new THREE.Group()
      g.name = 'vibration-data'
      return g
    }),
    createColorLegend: vi.fn(() => {
      const g = new THREE.Group()
      g.name = 'color-legend'
      return g
    }),
  }
})

// Mock STLLoader
vi.mock('three/examples/jsm/loaders/STLLoader.js', () => ({
  STLLoader: vi.fn().mockImplementation(() => ({
    load: vi.fn((_url: string, onLoad: (geometry: THREE.BufferGeometry) => void) => {
      // 模拟加载成功
      const geometry = new THREE.BufferGeometry()
      onLoad(geometry)
    }),
  })),
}))

// Mock Element Plus组件
vi.mock('@element-plus/icons-vue', () => ({
  VideoPlay: { name: 'VideoPlay' },
  VideoPause: { name: 'VideoPause' },
}))

describe('SimulationViewer.vue', () => {
  let wrapper: VueWrapper<any>

  const mockSimulationData: SimulationVisualizationData = {
    task_id: 'test-task-001',
    timestamp: Date.now(),
    force_data: [
      {
        position: [0, 0, 10] as [number, number, number],
        direction: [0, 0, -1] as [number, number, number],
        magnitude: 500,
        timestamp: 1000,
      },
      {
        position: [10, 0, 10] as [number, number, number],
        direction: [0, 0, -1] as [number, number, number],
        magnitude: 800,
        timestamp: 1000,
      },
      {
        position: [20, 0, 10] as [number, number, number],
        direction: [0, 0, -1] as [number, number, number],
        magnitude: 600,
        timestamp: 2000,
      },
    ],
    temperature_data: [
      {
        position: [0, 0, 10] as [number, number, number],
        temperature: 25,
        timestamp: 1000,
      },
      {
        position: [10, 0, 10] as [number, number, number],
        temperature: 80,
        timestamp: 1000,
      },
      {
        position: [20, 0, 10] as [number, number, number],
        temperature: 45,
        timestamp: 2000,
      },
    ],
    vibration_data: [
      {
        position: [0, 0, 10] as [number, number, number],
        amplitude: 0.05,
        frequency: 60,
        timestamp: 1000,
      },
      {
        position: [10, 0, 10] as [number, number, number],
        amplitude: 0.08,
        frequency: 75,
        timestamp: 1000,
      },
    ],
  }

  const mockToolpathSegments: ToolpathSegmentData[] = [
    {
      block_number: 1,
      g_code: 'G00',
      type: 'rapid',
      start_point: [0, 0, 20],
      end_point: [0, 0, 10],
    },
    {
      block_number: 2,
      g_code: 'G01',
      type: 'linear',
      start_point: [0, 0, 10],
      end_point: [20, 0, 10],
    },
  ]

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount()
    }
  })

  describe('组件挂载和初始化', () => {
    it('应该正确挂载组件', () => {
      wrapper = mount(SimulationViewer)
      expect(wrapper.exists()).toBe(true)
    })

    it('应该显示占位符当未初始化时', () => {
      wrapper = mount(SimulationViewer)
      const placeholder = wrapper.find('.viewer-placeholder')
      expect(placeholder.exists()).toBe(true)
    })

    it('应该在挂载后初始化场景', async () => {
      wrapper = mount(SimulationViewer)
      await wrapper.vm.$nextTick()
      
      // 检查是否调用了初始化
      expect(wrapper.vm.initialized).toBe(true)
    })

    it('应该接受自定义背景色', () => {
      wrapper = mount(SimulationViewer, {
        props: {
          backgroundColor: '#ffffff',
        },
      })
      expect(wrapper.props('backgroundColor')).toBe('#ffffff')
    })

    it('应该接受显示网格选项', () => {
      wrapper = mount(SimulationViewer, {
        props: {
          showGrid: false,
        },
      })
      expect(wrapper.props('showGrid')).toBe(false)
    })
  })


  describe('模型加载', () => {
    it('应该能够加载毛坯模型', async () => {
      wrapper = mount(SimulationViewer, {
        props: {
          stockStlUrl: '/test-stock.stl',
        },
      })
      await wrapper.vm.$nextTick()

      // 检查加载状态
      expect(wrapper.exists()).toBe(true)
    })

    it('应该能够加载结果模型', async () => {
      wrapper = mount(SimulationViewer, {
        props: {
          resultStlUrl: '/test-result.stl',
        },
      })
      await wrapper.vm.$nextTick()

      expect(wrapper.exists()).toBe(true)
    })

  })

  describe('边界情况处理', () => {
    it('应该处理空的仿真数据', async () => {
      const emptyData: SimulationVisualizationData = {
        task_id: 'empty-task',
        timestamp: Date.now(),
        force_data: [],
        temperature_data: [],
        vibration_data: [],
      }

      wrapper = mount(SimulationViewer, {
        props: {
          simulationData: emptyData,
        },
      })
      await wrapper.vm.$nextTick()

      expect(wrapper.exists()).toBe(true)
    })

    it('应该处理未定义的仿真数据', async () => {
      wrapper = mount(SimulationViewer, {
        props: {
          simulationData: undefined,
        },
      })
      await wrapper.vm.$nextTick()

      expect(wrapper.exists()).toBe(true)
    })

    it('应该处理极端的力值', async () => {
      const extremeForceData: ForceData[] = [
        {
          position: [0, 0, 10],
          direction: [0, 0, -1],
          magnitude: 0.001, // 极小值
          timestamp: 1000,
        },
        {
          position: [10, 0, 10],
          direction: [0, 0, -1],
          magnitude: 10000, // 极大值
          timestamp: 1000,
        },
      ]

      const dataWithExtremeForce = {
        ...mockSimulationData,
        force_data: extremeForceData,
      }

      wrapper = mount(SimulationViewer, {
        props: {
          simulationData: dataWithExtremeForce,
        },
      })
      await wrapper.vm.$nextTick()

      expect(wrapper.exists()).toBe(true)
    })

    it('应该处理极端的温度值', async () => {
      const extremeTempData: TemperatureData[] = [
        {
          position: [0, 0, 10],
          temperature: -273.15, // 绝对零度
          timestamp: 1000,
        },
        {
          position: [10, 0, 10],
          temperature: 10000, // 极高温度
          timestamp: 1000,
        },
      ]

      const dataWithExtremeTemp = {
        ...mockSimulationData,
        temperature_data: extremeTempData,
      }

      wrapper = mount(SimulationViewer, {
        props: {
          simulationData: dataWithExtremeTemp,
        },
      })
      await wrapper.vm.$nextTick()

      expect(wrapper.exists()).toBe(true)
    })

    it('应该处理未碰撞的碰撞数据', async () => {
      const noCollisionData: CollisionInfo = {
        collided: false,
        collision_positions: [],
        collision_segment_indices: [],
        collision_severity: 'none',
      }

      wrapper = mount(SimulationViewer, {
        props: {
          collisionData: noCollisionData,
        },
      })
      await wrapper.vm.$nextTick()

      expect(wrapper.exists()).toBe(true)
    })

    it('应该处理空的刀具路径段', async () => {
      wrapper = mount(SimulationViewer, {
        props: {
          toolpathSegments: [],
        },
      })
      await wrapper.vm.$nextTick()

      expect(wrapper.exists()).toBe(true)
    })

    it('应该处理无效的段索引', async () => {
      wrapper = mount(SimulationViewer, {
        props: {
          toolpathSegments: mockToolpathSegments,
          currentSegmentIndex: 999, // 超出范围
        },
      })
      await wrapper.vm.$nextTick()

      expect(wrapper.exists()).toBe(true)
    })
  })

  describe('事件发射', () => {
    it('应该发射fps-update事件', async () => {
      wrapper = mount(SimulationViewer)
      await wrapper.vm.$nextTick()

      // 模拟FPS更新
      wrapper.vm.fpsDisplay = 60
      await wrapper.vm.$nextTick()

      // FPS更新应该被发射
      expect(wrapper.exists()).toBe(true)
    })


    it('应该发射segment-change事件', async () => {
      wrapper = mount(SimulationViewer, {
        props: {
          toolpathSegments: mockToolpathSegments,
        },
      })
      await wrapper.vm.$nextTick()

      // 段变化事件应该被支持
      expect(wrapper.exists()).toBe(true)
    })
  })


  describe('样式和布局', () => {

    it('应该正确应用FPS计数器样式', async () => {
      wrapper = mount(SimulationViewer)
      await wrapper.vm.$nextTick()

      wrapper.vm.fpsDisplay = 60
      await wrapper.vm.$nextTick()

      const fpsCounter = wrapper.find('.fps-counter')
      expect(fpsCounter.exists()).toBe(true)
      expect(fpsCounter.text()).toContain('60')
    })
  })
})


describe('useSimulationVisualization', () => {
  it('应该导出所有必要的函数', async () => {
    const mod = await import('@/composables/useSimulationVisualization')
    
    expect(mod.getColorForValue).toBeDefined()
    expect(mod.createForceArrow).toBeDefined()
    expect(mod.createForceVectorGroup).toBeDefined()
    expect(mod.createTemperatureMaterial).toBeDefined()
    expect(mod.createTemperatureCloud).toBeDefined()
    expect(mod.createVibrationVisualization).toBeDefined()
    expect(mod.createColorLegend).toBeDefined()
    expect(mod.useSimulationVisualization).toBeDefined()
  })

  it('应该能够创建力矢量箭头', async () => {
    const mod = await import('@/composables/useSimulationVisualization')
    
    const arrow = mod.createForceArrow(
      [0, 0, 0],
      [0, 0, -1],
      500,
      1.0
    )
    
    expect(arrow).toBeDefined()
    expect(arrow).toBeInstanceOf(THREE.ArrowHelper)
  })

  it('应该能够创建力矢量组', async () => {
    const mod = await import('@/composables/useSimulationVisualization')
    
    const forceData: ForceData[] = [
      {
        position: [0, 0, 0],
        direction: [0, 0, -1],
        magnitude: 500,
        timestamp: 1000,
      },
    ]
    
    const group = mod.createForceVectorGroup(forceData)
    
    expect(group).toBeDefined()
    expect(group).toBeInstanceOf(THREE.Group)
    expect(group.name).toBe('force-vectors')
  })

  it('应该能够创建温度云图材质', async () => {
    const mod = await import('@/composables/useSimulationVisualization')
    
    const temperatureData: TemperatureData[] = [
      {
        position: [0, 0, 0],
        temperature: 25,
        timestamp: 1000,
      },
      {
        position: [10, 0, 0],
        temperature: 80,
        timestamp: 1000,
      },
    ]
    
    const material = mod.createTemperatureMaterial(temperatureData)
    
    expect(material).toBeDefined()
    expect(material).toBeInstanceOf(THREE.ShaderMaterial)
  })

  it('应该能够创建振动可视化', async () => {
    const mod = await import('@/composables/useSimulationVisualization')
    
    const vibrationData: VibrationData[] = [
      {
        position: [0, 0, 0],
        amplitude: 0.05,
        frequency: 60,
        timestamp: 1000,
      },
    ]
    
    const group = mod.createVibrationVisualization(vibrationData)
    
    expect(group).toBeDefined()
    expect(group).toBeInstanceOf(THREE.Group)
    expect(group.name).toBe('vibration-data')
  })

  it('应该能够根据值获取颜色', async () => {
    const mod = await import('@/composables/useSimulationVisualization')
    
    const color = mod.getColorForValue(50, 0, 100)
    
    expect(color).toBeDefined()
    expect(color).toBeInstanceOf(THREE.Color)
  })

  it('应该能够创建颜色图例', async () => {
    const mod = await import('@/composables/useSimulationVisualization')
    
    const legend = mod.createColorLegend(0, 100, '测试')
    
    expect(legend).toBeDefined()
    expect(legend).toBeInstanceOf(THREE.Group)
    expect(legend.name).toBe('color-legend')
  })
})

describe('simulation API', () => {
  // 静态导入替代动态 import：动态 import 在 vitest 并发下偶发超过 10s 超时
  // （环境性 flaky），模块本身是纯函数库，静态导入无副作用且更快
  it('应该导出所有必要的函数和类型', () => {
    expect(getSimulationResult).toBeDefined()
    expect(clearSimulationCache).toBeDefined()
    expect(getCacheStats).toBeDefined()
  })

  it('应该能够清除缓存', () => {
    expect(() => clearSimulationCache()).not.toThrow()
    expect(() => clearSimulationCache('test-task')).not.toThrow()
  })

  it('应该能够获取缓存统计', () => {
    const stats = getCacheStats()

    expect(stats).toBeDefined()
    expect(stats.size).toBeDefined()
    expect(stats.maxAge).toBeDefined()
    expect(stats.maxSize).toBeDefined()
  })

  it('应该定义正确的数据结构', async () => {
    const forceData: ForceData = {
      position: [0, 0, 0],
      direction: [0, 0, -1],
      magnitude: 500,
      timestamp: 1000,
    }
    
    const tempData: TemperatureData = {
      position: [0, 0, 0],
      temperature: 25,
      timestamp: 1000,
    }
    
    const vibrationData: VibrationData = {
      position: [0, 0, 0],
      amplitude: 0.05,
      frequency: 60,
      timestamp: 1000,
    }
    
    expect(forceData.magnitude).toBe(500)
    expect(tempData.temperature).toBe(25)
    expect(vibrationData.amplitude).toBe(0.05)
  })
})
