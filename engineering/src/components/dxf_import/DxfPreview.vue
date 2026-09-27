<template>
  <div class="preview-section">
    <div class="preview-header">
      <h4>{{ $t('dxfImportDialog.preview') }}</h4>
      <el-button-group size="small">
        <el-button @click="resetView">
          <el-icon><Aim /></el-icon>{{ $t('dxfImportDialog.viewFit') }}
        </el-button>
        <el-button @click="viewTop">
          {{ $t('dxfImportDialog.viewTop') }}
        </el-button>
        <el-button @click="view3D">
          {{ $t('dxfImportDialog.viewIso') }}
        </el-button>
        <el-button @click="toggleWireframe">
          {{ wireframe ? $t('dxfImportDialog.viewSolid') : $t('dxfImportDialog.viewWire') }}
        </el-button>
      </el-button-group>
    </div>
    <div
      ref="previewContainer"
      class="preview-canvas"
    />
    <div
      v-if="previewLoading"
      class="preview-loading"
    >
      <el-icon class="is-loading">
        <Loading />
      </el-icon>
      <span>{{ $t('viewer.loading') }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onBeforeUnmount, nextTick } from 'vue'
import * as THREE from 'three'
import { useThreeScene } from '@/composables/useThreeScene'
import { Loading, Aim } from '@element-plus/icons-vue'
import type { DxfParseResponse } from '@/types'

const props = defineProps<{
  parseResult: DxfParseResponse | null
}>()

const PREVIEW_CAMERA_FOV = 45
const PREVIEW_CAMERA_POSITION: [number, number, number] = [0, -200, 200]
const PREVIEW_GRID_SIZE = 500
const PREVIEW_GRID_DIVISIONS = 25
const PREVIEW_AXES_SIZE = 50

const previewContainer = ref<HTMLDivElement | null>(null)
const previewLoading = ref(false)
const wireframe = ref(false)

let threeScene: ReturnType<typeof useThreeScene> | null = null
let contentGroup: THREE.Group | null = null

watch(
  () => props.parseResult,
  async (result) => {
    if (result) {
      await nextTick()
      initPreview(result)
    } else {
      disposePreview()
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  disposePreview()
})

function initPreview(result: DxfParseResponse) {
  if (!previewContainer.value) return
  previewLoading.value = true

  try {
    disposePreview()

    threeScene = useThreeScene({
      container: previewContainer.value,
      backgroundColor: getComputedStyle(document.documentElement)
        .getPropertyValue('--bg-3d-dxf').trim() || '#0e1525',
      fov: PREVIEW_CAMERA_FOV,
      cameraPosition: PREVIEW_CAMERA_POSITION,
      enableDamping: true,
      dampingFactor: 0.08,
      showGrid: true,
      gridSize: PREVIEW_GRID_SIZE,
      gridDivisions: PREVIEW_GRID_DIVISIONS,
    })

    const { scene, camera, addLight } = threeScene

    camera.up.set(0, 0, 1)
    camera.updateProjectionMatrix()

    addLight(new THREE.AmbientLight(0xffffff, 0.7))
    const dir = new THREE.DirectionalLight(0xffffff, 0.7)
    dir.position.set(50, 50, 100)
    addLight(dir)

    const grid = scene.children.find((c) => c instanceof THREE.GridHelper) as THREE.GridHelper
    if (grid) {
      grid.rotation.x = Math.PI / 2
    }

    const axes = new THREE.AxesHelper(PREVIEW_AXES_SIZE)
    scene.add(axes)

    contentGroup = createDxfGroup(result)
    scene.add(contentGroup)

    resetView()

    threeScene.startAnimation()
  } finally {
    previewLoading.value = false
  }
}

function createDxfGroup(result: DxfParseResponse): THREE.Group {
  const group = new THREE.Group()
  const lineColor = 0x5b9bd5
  const lineMat = new THREE.LineBasicMaterial({
    color: lineColor,
    linewidth: 1,
  })

  const ext = result.extents || {}
  const cx = ((ext.min_x ?? 0) + (ext.max_x ?? 0)) / 2
  const cy = ((ext.min_y ?? 0) + (ext.max_y ?? 0)) / 2
  const rangeX = Math.max((ext.max_x ?? 0) - (ext.min_x ?? 0), 1)
  const rangeY = Math.max((ext.max_y ?? 0) - (ext.min_y ?? 0), 1)
  const range = Math.max(rangeX, rangeY, 1)
  const scale = 100 / range

  if (result.lines && result.lines.length > 0) {
    const maxLines = 5000
    const lines = result.lines.slice(0, maxLines)
    const positions: number[] = []
    for (const ln of lines) {
      const [sx, sy] = ln.start
      const [ex, ey] = ln.end
      positions.push(
        (sx - cx) * scale, (sy - cy) * scale, 0,
        (ex - cx) * scale, (ey - cy) * scale, 0,
      )
    }
    const geo = new THREE.BufferGeometry()
    geo.setAttribute(
      'position',
      new THREE.Float32BufferAttribute(positions, 3),
    )
    const segs = new THREE.LineSegments(geo, lineMat)
    group.add(segs)
  }

  if (result.circles && result.circles.length > 0) {
    const maxCircles = 2000
    const circles = result.circles.slice(0, maxCircles)
    for (const c of circles) {
      const [ccx, ccy] = c.center
      const r = (c.radius ?? 0) * scale
      if (r <= 0) continue
      const segs = 48
      const pts: number[] = []
      for (let i = 0; i <= segs; i++) {
        const a = (i / segs) * Math.PI * 2
        pts.push(
          (ccx - cx) * scale + Math.cos(a) * r,
          (ccy - cy) * scale + Math.sin(a) * r,
          0,
        )
      }
      const geo = new THREE.BufferGeometry()
      geo.setAttribute(
        'position',
        new THREE.Float32BufferAttribute(pts, 3),
      )
      const ring = new THREE.Line(geo, lineMat)
      group.add(ring)
    }
  }

  return group
}

function resetView() {
  if (!threeScene || !contentGroup) return
  const { camera, controls } = threeScene
  const box = new THREE.Box3().setFromObject(contentGroup)
  if (box.isEmpty()) {
    camera.position.set(0, -200, 200)
    controls.target.set(0, 0, 0)
  } else {
    const center = box.getCenter(new THREE.Vector3())
    const size = box.getSize(new THREE.Vector3())
    const maxDim = Math.max(size.x, size.y, 1)
    const fov = camera.fov * (Math.PI / 180)
    const dist = (maxDim / (2 * Math.tan(fov / 2))) * 1.8
    camera.position.set(center.x, center.y - dist, center.z + dist * 0.4)
    controls.target.copy(center)
  }
  controls.update()
}

function viewTop() {
  if (!threeScene) return
  const { camera, controls } = threeScene
  const target = controls.target.clone()
  camera.position.set(target.x, target.y, target.z + 200)
  camera.up.set(0, 1, 0)
  controls.update()
}

function view3D() {
  if (!threeScene) return
  const { camera, controls } = threeScene
  const target = controls.target.clone()
  const d = 200
  camera.position.set(target.x + d, target.y - d, target.z + d)
  camera.up.set(0, 0, 1)
  controls.update()
}

function toggleWireframe() {
  wireframe.value = !wireframe.value
  if (!contentGroup) return
  contentGroup.traverse((obj) => {
    if (obj instanceof THREE.LineSegments || obj instanceof THREE.Line) {
      const mat = obj.material as THREE.LineBasicMaterial
      if (mat) mat.color.set(wireframe.value ? 0xffd166 : 0x5b9bd5)
    }
  })
}

function disposePreview() {
  if (contentGroup) {
    contentGroup.traverse((obj) => {
      if (obj instanceof THREE.LineSegments || obj instanceof THREE.Line) {
        obj.geometry?.dispose()
        if (Array.isArray(obj.material)) {
          obj.material.forEach((m) => m.dispose())
        } else {
          (obj.material as THREE.Material)?.dispose()
        }
      }
    })
    contentGroup = null
  }
  if (threeScene) {
    threeScene.cleanup()
    threeScene = null
  }
}
</script>

<style scoped>
.preview-section {
  margin-top: 8px;
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.preview-header h4 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.preview-canvas {
  width: 100%;
  height: 360px;
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: var(--bg-code);
  position: relative;
}

.preview-loading {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--bg-card);
  font-size: 14px;
  background: var(--bg-overlay);
  padding: 8px 16px;
  border-radius: var(--radius-xs);
}
</style>