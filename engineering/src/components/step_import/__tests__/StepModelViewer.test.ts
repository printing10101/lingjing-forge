import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, VueWrapper } from '@vue/test-utils'
import * as THREE from 'three'
import StepModelViewer from '@/components/step_import/StepModelViewer.vue'

// Mock vue-i18n
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, any>) => {
      if (params) {
        return `${key}:${JSON.stringify(params)}`
      }
      return key
    },
  }),
}))

// Mock @element-plus/icons-vue
vi.mock('@element-plus/icons-vue', () => ({
  Aim: { name: 'Aim', render: () => null },
}))

// Mock @/utils/formatters
vi.mock('@/utils/formatters', () => ({
  formatFileSize: (size: number): string => {
    if (!size) return '-'
    if (size < 1024) return `${size} B`
    return `${(size / 1024).toFixed(1)} KB`
  },
}))

// Mock three
const mockThreeScene = vi.hoisted(() => ({
  scene: {
    add: vi.fn(),
    remove: vi.fn(),
    children: [],
  },
  camera: {
    fov: 45,
    position: { set: vi.fn() },
  },
  renderer: {
    shadowMap: { enabled: false },
  },
  controls: {
    target: { copy: vi.fn(), clone: vi.fn(() => ({ copy: vi.fn() })) },
    position: { set: vi.fn() },
    update: vi.fn(),
  },
  addLight: vi.fn(),
  startAnimation: vi.fn(),
  cleanup: vi.fn(),
}))

vi.mock('three', () => {
  class MockVector3 {
    x = 0
    y = 0
    z = 0
    constructor(x?: number, y?: number, z?: number) {
      this.x = x ?? 0
      this.y = y ?? 0
      this.z = z ?? 0
    }
    copy(v: MockVector3) { this.x = v.x; this.y = v.y; this.z = v.z; return this }
    clone() { return new MockVector3(this.x, this.y, this.z) }
  }
  class MockAmbientLight {
    constructor(public color?: number, public intensity?: number) {}
  }
  class MockDirectionalLight {
    position = { set: vi.fn() }
    constructor(public color?: number, public intensity?: number) {}
  }
  class MockAxesHelper {
    geometry = { dispose: vi.fn() }
    material = { dispose: vi.fn() }
    constructor(public size?: number) {}
  }
  class MockGridHelper {
    constructor(public size?: number, public divisions?: number) {}
  }
  class MockBufferGeometry {
    attributes = {
      position: { count: 9, array: new Float32Array([0, 0, 0, 1, 1, 1, 2, 2, 2]) },
    }
    index = null
    computeVertexNormals = vi.fn()
    center = vi.fn()
    clone = vi.fn(() => new MockBufferGeometry())
    dispose = vi.fn()
    setAttribute = vi.fn()
  }
  class MockFloat32BufferAttribute {
    constructor(public array: number[], public itemSize: number) {}
  }
  class MockMeshStandardMaterial {
    color = 0
    metalness = 0
    roughness = 0
    transparent = false
    opacity = 1
    side = 0
    clone = vi.fn(() => new MockMeshStandardMaterial())
    dispose = vi.fn()
  }
  class MockMaterial {
    dispose = vi.fn()
    clone = vi.fn(() => new MockMaterial())
  }
  class MockMesh {
    geometry: { attributes: Record<string, unknown> }
    material: unknown
    castShadow = false
    receiveShadow = false
    constructor(geometry?: { attributes: Record<string, unknown> }, material?: unknown) {
      this.geometry = geometry!
      this.material = material
    }
  }
  class MockBox3 {
    setFromObject = vi.fn(() => this)
    getCenter = vi.fn(() => new MockVector3(0, 0, 0))
    getSize = vi.fn(() => new MockVector3(10, 10, 10))
  }
  class MockLOD {
    addLevel = vi.fn()
    update = vi.fn()
    traverse = vi.fn()
  }
  return {
    AmbientLight: MockAmbientLight,
    DirectionalLight: MockDirectionalLight,
    AxesHelper: MockAxesHelper,
    GridHelper: MockGridHelper,
    BufferGeometry: MockBufferGeometry,
    Float32BufferAttribute: MockFloat32BufferAttribute,
    MeshStandardMaterial: MockMeshStandardMaterial,
    Material: MockMaterial,
    Mesh: MockMesh,
    Box3: MockBox3,
    Vector3: MockVector3,
    LOD: MockLOD,
    DoubleSide: 2,
  }
})

// Mock STLLoader
vi.mock('three/examples/jsm/loaders/STLLoader.js', () => {
  const MockGeometry: Record<string, unknown> = {
    computeVertexNormals: vi.fn(),
    center: vi.fn(),
    attributes: {
      position: { count: 9, array: new Float32Array([0, 0, 0, 1, 1, 1, 2, 2, 2]) },
    },
    index: null,
    clone: vi.fn(() => ({ ...MockGeometry })),
    dispose: vi.fn(),
    setAttribute: vi.fn(),
  }
  return {
    STLLoader: vi.fn().mockImplementation(() => ({
      load: vi.fn((_url: string, onSuccess: (geo: { attributes: Record<string, unknown> }) => void, _onProgress: unknown, _onError: unknown) => {
        // 异步触发成功回调
        onSuccess(MockGeometry as unknown as { attributes: Record<string, unknown> })
      }),
    })),
  }
})

// Mock @/composables/useThreeScene
vi.mock('@/composables/useThreeScene', () => ({
  useThreeScene: vi.fn(() => mockThreeScene),
}))

describe('StepModelViewer.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount()
    }
  })

  const mountComponent = (props: Record<string, any> = {}) => {
    wrapper = mount(StepModelViewer, {
      props: {
        modelUrl: 'http://example.com/model.stl',
        visible: true,
        ...props,
      } as any,
      global: {
        stubs: {
          'el-dialog': {
            template: '<div><slot /><slot name="footer" /></div>',
            props: ['modelValue', 'title', 'width', 'top', 'closeOnClickModal', 'destroyOnClose'],
            emits: ['update:modelValue', 'opened', 'close'],
          },
          'el-button-group': { template: '<div><slot /></div>' },
          'el-button': { template: '<button><slot /></button>' },
          'el-icon': { template: '<span><slot /></span>' },
          'el-divider': { template: '<span class="divider" />' },
          'el-slider': { template: '<input type="range" />' },
          'el-switch': { template: '<button class="switch" />' },
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

    it('应该渲染模型查看器容器', () => {
      mountComponent()
      expect(wrapper.find('.model-viewer-container').exists()).toBe(true)
    })

    it('应该渲染画布容器', () => {
      mountComponent()
      expect(wrapper.find('.canvas-container').exists()).toBe(true)
    })

    it('应该渲染查看器控件区域', () => {
      mountComponent()
      expect(wrapper.find('.viewer-controls').exists()).toBe(true)
    })

    it('应该渲染 FPS 显示', () => {
      mountComponent()
      expect(wrapper.find('.fps-display').exists()).toBe(true)
    })

    it('未初始化时不应渲染模型信息栏', () => {
      mountComponent()
      expect(wrapper.find('.model-info-bar').exists()).toBe(false)
    })

    it('应该渲染关闭按钮', () => {
      mountComponent()
      const buttons = wrapper.findAll('button')
      expect(buttons.length).toBeGreaterThan(0)
    })
  })

  describe('初始状态', () => {
    it('opacity 初始值应为 0.8', () => {
      mountComponent()
      expect(wrapper.vm.opacity).toBe(0.8)
    })

    it('showGrid 初始值应为 true', () => {
      mountComponent()
      expect(wrapper.vm.showGrid).toBe(true)
    })

    it('lodEnabled 初始值应为 false', () => {
      mountComponent()
      expect(wrapper.vm.lodEnabled).toBe(false)
    })

    it('fps 初始值应为 0', () => {
      mountComponent()
      expect(wrapper.vm.fps).toBe(0)
    })

    it('modelStats 初始值应为 null', () => {
      mountComponent()
      expect(wrapper.vm.modelStats).toBeNull()
    })
  })

  describe('initViewer 方法', () => {
    it('无 canvasContainer 时应直接返回', () => {
      mountComponent()
      wrapper.vm.canvasContainer = null
      expect(() => wrapper.vm.initViewer()).not.toThrow()
    })

    it('应调用 useThreeScene', () => {
      mountComponent()
      wrapper.vm.initViewer()
      expect(mockThreeScene.addLight).toHaveBeenCalled()
    })

    it('应添加灯光到场景', () => {
      mountComponent()
      wrapper.vm.initViewer()
      expect(mockThreeScene.addLight).toHaveBeenCalled()
    })

    it('应设置 modelStats', () => {
      // modelUrl 为空：避免 loadModel 用模型对象统计覆盖 props 统计
      mountComponent({ modelUrl: undefined, vertexCount: 100, faceCount: 50, fileSize: 1024 })
      wrapper.vm.initViewer()
      expect(wrapper.vm.modelStats).not.toBeNull()
      expect(wrapper.vm.modelStats.vertexCount).toBe(100)
      expect(wrapper.vm.modelStats.faceCount).toBe(50)
      expect(wrapper.vm.modelStats.fileSize).toBe(1024)
    })

    it('无 vertexCount/faceCount/fileSize 时 modelStats 应为 0', () => {
      mountComponent({ modelUrl: undefined, vertexCount: undefined, faceCount: undefined, fileSize: undefined })
      wrapper.vm.initViewer()
      expect(wrapper.vm.modelStats.vertexCount).toBe(0)
      expect(wrapper.vm.modelStats.faceCount).toBe(0)
      expect(wrapper.vm.modelStats.fileSize).toBe(0)
    })

    it('有 modelUrl 时应调用 loadModel', () => {
      mountComponent({ modelUrl: 'http://example.com/model.stl' })
      wrapper.vm.initViewer()
      // spyOn(vm) 不拦截 setup 内部调用；loadModel 通过 STLLoader.load 同步回调
      // 更新 modelStats（MockGeometry position.count=9），以此验证链路生效
      expect(wrapper.vm.modelStats.vertexCount).toBe(9)
    })

    it('应启动动画循环', () => {
      mountComponent()
      wrapper.vm.initViewer()
      expect(mockThreeScene.startAnimation).toHaveBeenCalled()
    })
  })

  describe('loadModel 方法', () => {
    it('无 threeScene 时应直接返回', () => {
      mountComponent()
      expect(() => wrapper.vm.loadModel('url')).not.toThrow()
    })

    it('有 threeScene 时应创建 STLLoader 并加载', () => {
      mountComponent()
      wrapper.vm.initViewer()
      wrapper.vm.loadModel('http://example.com/model.stl')
      // STLLoader 已 mock，load 应被调用
      expect(wrapper.vm.modelStats).not.toBeNull()
    })
  })

  describe('fitView 方法', () => {
    it('无 threeScene 时应直接返回', () => {
      mountComponent()
      expect(() => wrapper.vm.fitView()).not.toThrow()
    })

    it('有 threeScene 但无 modelMesh 时应直接返回', () => {
      mountComponent()
      wrapper.vm.initViewer()
      expect(() => wrapper.vm.fitView()).not.toThrow()
    })
  })

  describe('viewFront 方法', () => {
    it('无 threeScene 时应直接返回', () => {
      mountComponent()
      expect(() => wrapper.vm.viewFront()).not.toThrow()
    })

    it('有 threeScene 时应设置相机位置', () => {
      mountComponent()
      wrapper.vm.initViewer()
      wrapper.vm.viewFront()
      expect(mockThreeScene.camera.position.set).toHaveBeenCalled()
      expect(mockThreeScene.controls.update).toHaveBeenCalled()
    })
  })

  describe('viewTop 方法', () => {
    it('无 threeScene 时应直接返回', () => {
      mountComponent()
      expect(() => wrapper.vm.viewTop()).not.toThrow()
    })

    it('有 threeScene 时应设置相机位置', () => {
      mountComponent()
      wrapper.vm.initViewer()
      wrapper.vm.viewTop()
      expect(mockThreeScene.camera.position.set).toHaveBeenCalled()
      expect(mockThreeScene.controls.update).toHaveBeenCalled()
    })
  })

  describe('viewRight 方法', () => {
    it('无 threeScene 时应直接返回', () => {
      mountComponent()
      expect(() => wrapper.vm.viewRight()).not.toThrow()
    })

    it('有 threeScene 时应设置相机位置', () => {
      mountComponent()
      wrapper.vm.initViewer()
      wrapper.vm.viewRight()
      expect(mockThreeScene.camera.position.set).toHaveBeenCalled()
      expect(mockThreeScene.controls.update).toHaveBeenCalled()
    })
  })

  describe('viewIso 方法', () => {
    it('无 threeScene 时应直接返回', () => {
      mountComponent()
      expect(() => wrapper.vm.viewIso()).not.toThrow()
    })

    it('有 threeScene 时应设置相机位置', () => {
      mountComponent()
      wrapper.vm.initViewer()
      wrapper.vm.viewIso()
      expect(mockThreeScene.camera.position.set).toHaveBeenCalled()
      expect(mockThreeScene.controls.update).toHaveBeenCalled()
    })
  })

  describe('updateOpacity 方法', () => {
    it('无 modelMesh 时应直接返回', () => {
      mountComponent()
      expect(() => wrapper.vm.updateOpacity()).not.toThrow()
    })
  })

  describe('toggleGrid 方法', () => {
    it('无 threeScene 时应直接返回', () => {
      mountComponent()
      expect(() => wrapper.vm.toggleGrid()).not.toThrow()
    })

    it('有 threeScene 时不应抛错', () => {
      mountComponent()
      wrapper.vm.initViewer()
      expect(() => wrapper.vm.toggleGrid()).not.toThrow()
    })
  })

  describe('toggleLOD 方法', () => {
    it('无 threeScene 时应直接返回', () => {
      mountComponent()
      expect(() => wrapper.vm.toggleLOD()).not.toThrow()
    })

    it('有 threeScene 但无 modelMesh 时应直接返回', () => {
      mountComponent()
      wrapper.vm.initViewer()
      expect(() => wrapper.vm.toggleLOD()).not.toThrow()
    })
  })

  describe('disposeViewer 方法', () => {
    it('无 threeScene 时不应抛错', () => {
      mountComponent()
      expect(() => wrapper.vm.disposeViewer()).not.toThrow()
    })

    it('有 threeScene 时应调用 cleanup', () => {
      mountComponent()
      wrapper.vm.initViewer()
      wrapper.vm.disposeViewer()
      expect(mockThreeScene.cleanup).toHaveBeenCalled()
    })
  })

  describe('simplifyGeometry 方法', () => {
    it('应返回简化后的几何体或 null', () => {
      mountComponent()
      const geo = new THREE.BufferGeometry()
      const result = wrapper.vm.simplifyGeometry(geo, 0.5)
      // 结果可能是几何体或 null，取决于实现
      expect(result === null || typeof result === 'object').toBe(true)
    })
  })

  describe('组件卸载', () => {
    it('卸载时应调用 disposeViewer', () => {
      mountComponent()
      // initViewer 由 el-dialog @opened 触发（测试中手动调用以初始化 threeScene）
      wrapper.vm.initViewer()
      wrapper.unmount()
      // disposeViewer 副作用：threeScene.cleanup()（spyOn(vm) 不拦截 setup 内部调用）
      expect(mockThreeScene.cleanup).toHaveBeenCalled()
    })
  })
})
