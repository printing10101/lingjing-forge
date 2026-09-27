<template>
  <div
    ref="canvasRef"
    class="toolpath-canvas"
    @contextmenu.prevent
  >
    <div
      v-if="!initialized"
      class="canvas-placeholder"
    >
      <p>{{ $t('toolpathCanvas.sceneTitle') }}</p>
      <p class="hint">
        {{ $t('toolpathCanvas.loadHint') }}
      </p>
    </div>

    <div
      v-if="fps > 0"
      class="fps-counter"
    >
      {{ $t('toolpathCanvas.fpsLabel') }}: {{ fps }}
    </div>

    <div class="coordinate-axes">
      <span class="axis-x">X</span>
      <span class="axis-y">Y</span>
      <span class="axis-z">Z</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import * as THREE from 'three'
import type { EditableToolpathSegment } from './types/editor'
import {
  useToolpathInteraction,
  createSegmentLine,
  getSegmentColor,
} from './composables/useToolpathInteraction'
import { useThreeScene } from '@/composables/useThreeScene'

// 场景常量
const CAMERA_FOV = 50
const CAMERA_POSITION: [number, number, number] = [200, -200, 150]
const LOOK_AT_TARGET: [number, number, number] = [0, 0, 30]
const CONTROLS_TARGET: [number, number, number] = [0, 0, 25]
const DAMPING_FACTOR = 0.08
const GRID_SIZE = 300
const GRID_DIVISIONS = 20

const props = defineProps<{
  segments: EditableToolpathSegment[]
  hoveredSegmentId: string | null
}>()

const emit = defineEmits<{
  'hover-change': [segmentId: string | null]
  'segment-click': [segmentId: string]
  'context-menu': [x: number, y: number, segmentId: string]
}>()

const canvasRef = ref<HTMLElement>()
const initialized = ref(false)
const fps = ref(0)

let threeScene: ReturnType<typeof useThreeScene> | null = null
let resizeObserver: ResizeObserver | null = null

const cameraRef = ref<THREE.PerspectiveCamera | null>(null)
const segmentLines = ref<Map<string, THREE.Line>>(new Map())
let frameCount = 0
let fpsTime = performance.now()

const { onMouseMove, onContextMenuEvent, onClick, dispose: disposeInteraction } =
  useToolpathInteraction(
    canvasRef,
    cameraRef,
    segmentLines,
    computed(() => props.segments),
    (id) => emit('hover-change', id),
    (x, y, id) => emit('context-menu', x, y, id),
    (id) => emit('segment-click', id),
  )

onMounted(() => {
  initScene()
  initialized.value = true
})

onBeforeUnmount(() => {
  cleanup()
})

watch(
  () => props.segments,
  () => {
    redrawSegments()
  },
)

function initScene() {
  if (!canvasRef.value) return

  const sceneBg = getComputedStyle(document.documentElement)
    .getPropertyValue('--bg-3d-scene')
    .trim() || '#1a1a2e'

  threeScene = useThreeScene({
    container: canvasRef.value,
    backgroundColor: sceneBg,
    fov: CAMERA_FOV,
    cameraPosition: CAMERA_POSITION,
    enableDamping: true,
    dampingFactor: DAMPING_FACTOR,
    showGrid: true,
    gridSize: GRID_SIZE,
    gridDivisions: GRID_DIVISIONS,
  })

  const { camera, renderer, controls, addLight } = threeScene

  camera.lookAt(...LOOK_AT_TARGET)
  cameraRef.value = camera
  controls.target.set(...CONTROLS_TARGET)

  addLight(new THREE.AmbientLight(0xffffff, 0.6))
  const dir1 = new THREE.DirectionalLight(0xffffff, 0.8)
  dir1.position.set(100, 100, 150)
  addLight(dir1)
  const dir2 = new THREE.DirectionalLight(0xffffff, 0.3)
  dir2.position.set(-100, -50, 50)
  addLight(dir2)

  renderer.domElement.addEventListener('mousemove', onMouseMove)
  renderer.domElement.addEventListener('contextmenu', onContextMenuEvent)
  renderer.domElement.addEventListener('click', onClick)

  resizeObserver = new ResizeObserver(() => {
    if (!canvasRef.value) return
    // Resize is handled internally by useThreeScene's ResizeObserver
  })
  resizeObserver.observe(canvasRef.value)

  frameCount = 0
  fpsTime = performance.now()
  threeScene.startAnimation(() => {
    frameCount++
    const now = performance.now()
    if (now - fpsTime >= 1000) {
      fps.value = frameCount
      frameCount = 0
      fpsTime = now
    }
  })
}

function redrawSegments() {
  if (!threeScene) return
  const { scene } = threeScene

  segmentLines.value.forEach((line) => {
    scene.remove(line)
    line.geometry?.dispose()
    ;(line.material as THREE.Material)?.dispose()
  })
  segmentLines.value.clear()

  for (const seg of props.segments) {
    if (seg.isDeleted) continue

    const color = getSegmentColor(seg.type)
    const line = createSegmentLine(seg, color)
    scene.add(line)
    segmentLines.value.set(seg.id, line)
  }
}

function cleanup() {
  if (resizeObserver) {
    resizeObserver.disconnect()
    resizeObserver = null
  }
  if (threeScene) {
    const { renderer } = threeScene
    renderer.domElement.removeEventListener('mousemove', onMouseMove)
    renderer.domElement.removeEventListener('contextmenu', onContextMenuEvent)
    renderer.domElement.removeEventListener('click', onClick)
    threeScene.cleanup()
    threeScene = null
  }
  disposeInteraction()
  cameraRef.value = null
}
</script>

<style lang="scss" scoped>
.toolpath-canvas {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  cursor: crosshair;

  .canvas-placeholder {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    text-align: center;
    color: var(--text-tertiary);
    p { margin: 4px 0; font-size: 16px; }
    .hint { font-size: 13px; opacity: 0.7; }
  }

  .fps-counter {
    position: absolute;
    top: 12px;
    left: 12px;
    background: var(--bg-overlay-strong);
    color: var(--toolpath-color);
    padding: 4px 10px;
    border-radius: var(--radius-xs);
    font-size: 12px;
    font-family: var(--font-mono);
  }

  .coordinate-axes {
    position: absolute;
    bottom: 12px;
    left: 12px;
    display: flex;
    gap: 8px;
    span {
      width: 20px; height: 20px;
      display: flex; align-items: center; justify-content: center;
      border-radius: var(--radius-xs); font-size: 11px; font-weight: 700; color: var(--bg-card);
    }
    .axis-x { background: var(--axis-x); }
    .axis-y { background: var(--axis-y); }
    .axis-z { background: var(--axis-z); }
  }
}
</style>
