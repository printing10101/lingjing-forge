<template>
  <div
    ref="containerRef"
    class="simulation-viewer"
    :style="containerStyle"
  >
    <!-- 3D 视口标签 -->
    <div class="viewport-label">
      {{ t('simulation.viewer.viewportLabel') }}
    </div>

    <!-- FPS 计数器 -->
    <div class="fps-counter">
      {{ fpsDisplay }} FPS
    </div>

    <!-- 无数据时的占位符 -->
    <div
      v-if="!initialized"
      class="viewer-placeholder"
    >
      <div class="placeholder-icon">
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="48"
          height="48"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.5"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" />
          <polyline points="3.27 6.96 12 12.01 20.73 6.96" />
          <line
            x1="12"
            y1="22.08"
            x2="12"
            y2="12"
          />
        </svg>
      </div>
      <div class="placeholder-text">
        {{ t('simulation.viewer.placeholderHint') }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, shallowRef, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

const { t } = useI18n()

// Props & Emits

interface Props {
  width?: string
  height?: string
  showGrid?: boolean
  showAxes?: boolean
  backgroundColor?: string
}

const props = withDefaults(defineProps<Props>(), {
  width: '100%',
  height: '400px',
  showGrid: true,
  showAxes: true,
  backgroundColor: getComputedStyle(document.documentElement)
    .getPropertyValue('--bg-3d-viewer').trim() || '#0A1220',
})

const emit = defineEmits<{
  (e: 'ready'): void
  (e: 'click', position: { x: number; y: number; z: number }): void
  (e: 'frame-change', frame: number): void
}>()

// Template refs

const containerRef = ref<HTMLElement>()

// Reactive state

const initialized = ref(false)
const fpsDisplay = ref(0)

// Three.js objects stored via shallowRef to avoid deep Vue reactivity overhead
const scene = shallowRef<THREE.Scene>()
const camera = shallowRef<THREE.PerspectiveCamera>()
const renderer = shallowRef<THREE.WebGLRenderer>()
const controls = shallowRef<OrbitControls>()

// Helpers that may need to be toggled at runtime
let gridHelper: THREE.GridHelper | null = null
let axesHelper: THREE.AxesHelper | null = null

// Animation bookkeeping
let animationFrameId: number | null = null
let resizeObserver: ResizeObserver | null = null

// FPS measurement
let fpsFrameCount = 0
let fpsLastTime = performance.now()

// Internal frame counter for emit
let internalFrameCount = 0

// Computed

const containerStyle = computed(() => ({
  width: props.width,
  height: props.height,
}))

// Scene initialisation

/** 从 CSS 变量解析 3D 场景背景色，失败时回退到 prop 默认值 */
function resolveBgColor(): string {
  const cssVar = getComputedStyle(document.documentElement)
    .getPropertyValue('--bg-3d-viewer')
    .trim()
  return cssVar || props.backgroundColor
}

function initScene(): void {
  const container = containerRef.value
  if (!container) return

  // Scene ---------------------------------------------------------------
  const _scene = new THREE.Scene()
  _scene.background = new THREE.Color(resolveBgColor())
  scene.value = _scene

  // Camera --------------------------------------------------------------
  const aspect = container.clientWidth / container.clientHeight
  const _camera = new THREE.PerspectiveCamera(50, aspect, 0.1, 2000)
  _camera.position.set(80, 60, 120)
  camera.value = _camera

  // Renderer ------------------------------------------------------------
  const _renderer = new THREE.WebGLRenderer({
    antialias: true,
    alpha: false,
  })
  _renderer.setSize(container.clientWidth, container.clientHeight)
  _renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  _renderer.toneMapping = THREE.ACESFilmicToneMapping
  _renderer.toneMappingExposure = 1.0
  container.appendChild(_renderer.domElement)
  renderer.value = _renderer

  // Controls -------------------------------------------------------------
  const _controls = new OrbitControls(_camera, _renderer.domElement)
  _controls.enableDamping = true
  _controls.dampingFactor = 0.08
  _controls.minDistance = 5
  _controls.maxDistance = 800
  controls.value = _controls

  // Lights --------------------------------------------------------------
  const ambientLight = new THREE.AmbientLight(0xffffff, 0.6)
  _scene.add(ambientLight)

  const directionalLight = new THREE.DirectionalLight(0xffffff, 0.9)
  directionalLight.position.set(50, 80, 60)
  _scene.add(directionalLight)

  const fillLight = new THREE.DirectionalLight(0x4a90d9, 0.3)
  fillLight.position.set(-40, 20, -30)
  _scene.add(fillLight)

  // Grid & Axes ----------------------------------------------------------
  if (props.showGrid) {
    gridHelper = new THREE.GridHelper(200, 20, 0x1a3a5c, 0x112240)
    _scene.add(gridHelper)
  }

  if (props.showAxes) {
    axesHelper = new THREE.AxesHelper(40)
    _scene.add(axesHelper)
  }

  // Raycaster for click events -------------------------------------------
  setupClickHandler(_renderer.domElement, _camera, _scene)

  // Mark as initialised
  initialized.value = true

  // Start the render loop
  startAnimationLoop()

  // Emit ready
  nextTick(() => emit('ready'))
}

// Click handler – raycasts into the scene and emits the hit point

function setupClickHandler(
  canvas: HTMLCanvasElement,
  cam: THREE.PerspectiveCamera,
  _scene: THREE.Scene,
): void {
  const raycaster = new THREE.Raycaster()
  const mouse = new THREE.Vector2()

  function onPointerDown(event: PointerEvent) {
    const rect = canvas.getBoundingClientRect()
    mouse.x = ((event.clientX - rect.left) / rect.width) * 2 - 1
    mouse.y = -((event.clientY - rect.top) / rect.height) * 2 + 1

    raycaster.setFromCamera(mouse, cam)

    // Intersect against a large invisible ground plane as a fallback
    const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0)
    const intersection = new THREE.Vector3()
    const hit = raycaster.ray.intersectPlane(groundPlane, intersection)
    if (hit) {
      emit('click', { x: intersection.x, y: intersection.y, z: intersection.z })
    }
  }

  canvas.addEventListener('pointerdown', onPointerDown)
  // We clean this up in the main dispose function via renderer.domElement removal
}

// Animation loop

function startAnimationLoop(): void {
  function animate() {
    animationFrameId = requestAnimationFrame(animate)

    if (controls.value) {
      controls.value.update()
    }

    if (renderer.value && scene.value && camera.value) {
      renderer.value.render(scene.value, camera.value)
    }

    // FPS measurement (update once per second)
    fpsFrameCount++
    internalFrameCount++
    const now = performance.now()
    if (now - fpsLastTime >= 1000) {
      fpsDisplay.value = Math.round((fpsFrameCount * 1000) / (now - fpsLastTime))
      fpsFrameCount = 0
      fpsLastTime = now
    }
  }

  animate()
}

// Resize handling

function handleResize(): void {
  const container = containerRef.value
  if (!container || !camera.value || !renderer.value) return

  const w = container.clientWidth
  const h = container.clientHeight

  if (w === 0 || h === 0) return

  camera.value.aspect = w / h
  camera.value.updateProjectionMatrix()
  renderer.value.setSize(w, h)
}

// Grid / Axes toggling (react to prop changes)

watch(
  () => props.showGrid,
  (show: boolean) => {
    if (!scene.value) return
    if (show && !gridHelper) {
      gridHelper = new THREE.GridHelper(200, 20, 0x1a3a5c, 0x112240)
      scene.value.add(gridHelper)
    } else if (!show && gridHelper) {
      scene.value.remove(gridHelper)
      gridHelper.dispose()
      gridHelper = null
    }
  },
)

watch(
  () => props.showAxes,
  (show: boolean) => {
    if (!scene.value) return
    if (show && !axesHelper) {
      axesHelper = new THREE.AxesHelper(40)
      scene.value.add(axesHelper)
    } else if (!show && axesHelper) {
      scene.value.remove(axesHelper)
      axesHelper.dispose()
      axesHelper = null
    }
  },
)

// Background colour toggling

watch(
  () => props.backgroundColor,
  (color: string) => {
    if (scene.value) {
      scene.value.background = new THREE.Color(color)
    }
  },
)

// Voxel data loader (placeholder – logs a message)

function loadVoxelData(data: unknown): void {
  // 占位实现：体素网格渲染待仿真后端就绪后实现
  // 调用时记录告警，便于在运行时识别该路径被触发但未渲染
  console.warn('[SimulationViewer] loadVoxelData not implemented yet, data ignored:', data)
}

// Lifecycle

onMounted(() => {
  nextTick(() => {
    initScene()

    if (containerRef.value) {
      resizeObserver = new ResizeObserver(() => {
        handleResize()
      })
      resizeObserver.observe(containerRef.value)
    }
  })
})

onUnmounted(() => {
  // Stop animation
  if (animationFrameId !== null) {
    cancelAnimationFrame(animationFrameId)
    animationFrameId = null
  }

  // Disconnect resize observer
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }

  // Dispose Three.js resources
  if (controls.value) {
    controls.value.dispose()
  }

  if (renderer.value) {
    renderer.value.dispose()
    if (renderer.value.domElement.parentNode) {
      renderer.value.domElement.parentNode.removeChild(renderer.value.domElement)
    }
  }

  if (gridHelper) {
    gridHelper.dispose()
    gridHelper = null
  }

  if (axesHelper) {
    axesHelper.dispose()
    axesHelper = null
  }

  // Clear refs
  scene.value = undefined
  camera.value = undefined
  renderer.value = undefined
  controls.value = undefined
  initialized.value = false
})

// Expose public API

defineExpose({
  loadVoxelData,
  /** Access the raw Three.js scene (read-only) */
  getScene: () => scene.value,
  /** Access the camera */
  getCamera: () => camera.value,
  /** Access the renderer */
  getRenderer: () => renderer.value,
  /** Access the orbit controls */
  getControls: () => controls.value,
})
</script>

<style scoped>
.simulation-viewer {
  position: relative;
  width: 100%;
  height: 100%;
  background: var(--bg-3d-viewer);
  border-radius: var(--radius-lg);
  overflow: hidden;
  box-sizing: border-box;
}

/* ---- Viewport label (top-left) ---- */
.viewport-label {
  position: absolute;
  top: 12px;
  left: 12px;
  z-index: 10;
  padding: 4px 10px;
  font-size: 12px;
  font-weight: 500;
  line-height: 1;
  color: var(--text-muted);
  background: var(--bg-3d-overlay);
  border: 1px solid var(--accent-border);
  border-radius: var(--radius-sm);
  pointer-events: none;
  user-select: none;
  backdrop-filter: blur(6px);
}

/* ---- FPS counter (bottom-right) ---- */
.fps-counter {
  position: absolute;
  bottom: 10px;
  right: 12px;
  z-index: 10;
  padding: 3px 8px;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 500;
  line-height: 1;
  color: var(--accent-primary);
  background: var(--bg-3d-overlay);
  border: 1px solid var(--accent-border);
  border-radius: var(--radius-xs);
  pointer-events: none;
  user-select: none;
  backdrop-filter: blur(6px);
}

/* ---- Placeholder (centered) ---- */
.viewer-placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  z-index: 5;
  pointer-events: none;
  user-select: none;
}

.placeholder-icon {
  color: var(--text-muted);
  opacity: 0.45;
}

.placeholder-text {
  font-size: 14px;
  color: var(--text-muted);
  opacity: 0.55;
  text-align: center;
}
</style>
