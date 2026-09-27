<template>
  <el-dialog
    v-model="visible"
    :title="$t('stepModelViewer.title')"
    width="900px"
    top="3vh"
    :close-on-click-modal="false"
    destroy-on-close
    @opened="initViewer"
    @close="disposeViewer"
  >
    <div class="model-viewer-container">
      <div
        ref="canvasContainer"
        class="canvas-container"
      />

      <div class="viewer-controls">
        <el-button-group size="small">
          <el-button @click="fitView">
            <el-icon><Aim /></el-icon> {{ $t('stepModelViewer.btnFit') }}
          </el-button>
          <el-button @click="viewFront">
            {{ $t('stepModelViewer.btnFront') }}
          </el-button>
          <el-button @click="viewTop">
            {{ $t('stepModelViewer.btnTop') }}
          </el-button>
          <el-button @click="viewRight">
            {{ $t('stepModelViewer.btnRight') }}
          </el-button>
          <el-button @click="viewIso">
            {{ $t('stepModelViewer.btnIso') }}
          </el-button>
        </el-button-group>

        <el-divider direction="vertical" />

        <span class="control-label">{{ $t('stepModelViewer.labelOpacity') }}</span>
        <el-slider
          v-model="opacity"
          :min="0.1"
          :max="1.0"
          :step="0.05"
          style="width: 120px;"
          @input="updateOpacity"
        />

        <el-divider direction="vertical" />

        <el-switch
          v-model="showGrid"
          :active-text="$t('stepModelViewer.switchGrid')"
          size="small"
          @change="toggleGrid"
        />

        <el-divider direction="vertical" />

        <el-switch
          v-model="lodEnabled"
          :active-text="$t('stepModelViewer.switchLod')"
          size="small"
          @change="toggleLOD"
        />

        <el-divider direction="vertical" />

        <span class="fps-display">{{ $t('stepModelViewer.fpsLabel', { fps }) }}</span>
      </div>

      <div
        v-if="modelStats"
        class="model-info-bar"
      >
        <span>{{ $t('stepModelViewer.statVertices', { count: modelStats.vertexCount?.toLocaleString() ?? 0 }) }}</span>
        <span>{{ $t('stepModelViewer.statFaces', { count: modelStats.faceCount?.toLocaleString() ?? 0 }) }}</span>
        <span>{{ $t('stepModelViewer.statFile', { size: modelStats.fileSize ? formatFileSize(modelStats.fileSize) : '-' }) }}</span>
      </div>
    </div>

    <template #footer>
      <el-button @click="visible = false">
        {{ $t('stepModelViewer.close') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue'
import { Aim } from '@element-plus/icons-vue'
import * as THREE from 'three'
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js'
import { formatFileSize } from '@/utils/formatters'
import { useThreeScene } from '@/composables/useThreeScene'

// 视图距离常量
const VIEW_DISTANCE = {
  FRONT: 300,
  TOP: 300,
  RIGHT: 300,
  ISO: 200,
} as const

// LOD 简化比例与距离常量
const LOD_SIMPLIFY_RATIOS = [0.5, 0.8] as const
const LOD_DISTANCES = [200, 500] as const

// 网格与辅助工具常量
const GRID_SIZE = 500
const GRID_DIVISIONS = 20
const AXES_SIZE = 100

const props = defineProps<{
  modelUrl: string
  modelName?: string
  faceCount?: number
  vertexCount?: number
  fileSize?: number
}>()

const visible = defineModel<boolean>({ required: true })

const canvasContainer = ref<HTMLDivElement>()
const opacity = ref(0.8)
const showGrid = ref(true)
const lodEnabled = ref(false)
const fps = ref(0)
const modelStats = ref<{
  vertexCount: number
  faceCount: number
  fileSize: number
} | null>(null)

let threeScene: ReturnType<typeof useThreeScene> | null = null
let modelMesh: THREE.Mesh | null = null
let gridHelper: THREE.GridHelper | null = null
let axesHelperRef: THREE.AxesHelper | null = null
let fpsFrames = 0
let fpsLastTime = 0
let lod: THREE.LOD | null = null
// 修复竞态条件：每次发起 STL 加载时递增 loadEpoch，
// loader.load 的异步回调执行时检查 epoch 是否仍为最新，
// 否则放弃对 modelMesh / scene 的写入，避免：
//   1. 连续切换 modelUrl（A → B）：A 后完成时旧 mesh 仍添加到场景，
//      覆盖 B 的 modelMesh 引用 → 场景中残留孤儿 A mesh（GPU 内存泄漏）
//   2. 对话框关闭后 STL 才加载完：回调里若不检查会写入已 dispose 的
//      threeScene / modelMesh 引用，造成内存泄漏或场景图错乱
let loadEpoch = 0

function initViewer() {
  if (!canvasContainer.value) return

  threeScene = useThreeScene({
    container: canvasContainer.value,
    backgroundColor: getComputedStyle(document.documentElement)
      .getPropertyValue('--bg-3d-scene').trim() || '#1a1a2e',
    fov: 45,
    cameraPosition: [200, -200, 150],
    enableDamping: true,
    dampingFactor: 0.08,
    showGrid: showGrid.value,
    gridSize: GRID_SIZE,
    gridDivisions: GRID_DIVISIONS,
  })

  const { scene, camera, renderer, addLight, startAnimation } = threeScene

  // 灯光
  addLight(new THREE.AmbientLight(0xffffff, 0.6))
  const dirLight1 = new THREE.DirectionalLight(0xffffff, 0.8)
  dirLight1.position.set(1, 1, 1)
  addLight(dirLight1)
  const dirLight2 = new THREE.DirectionalLight(0xffffff, 0.4)
  dirLight2.position.set(-1, -0.5, -0.5)
  addLight(dirLight2)

  // 坐标轴辅助
  axesHelperRef = new THREE.AxesHelper(AXES_SIZE)
  scene.add(axesHelperRef)

  // 阴影
  renderer.shadowMap.enabled = true

  modelStats.value = {
    vertexCount: props.vertexCount ?? 0,
    faceCount: props.faceCount ?? 0,
    fileSize: props.fileSize ?? 0,
  }

  if (props.modelUrl) {
    loadModel(props.modelUrl)
  }

  // FPS 计算与动画循环
  fpsFrames = 0
  fpsLastTime = performance.now()
  startAnimation(() => {
    fpsFrames++
    const now = performance.now()
    if (now - fpsLastTime >= 1000) {
      fps.value = fpsFrames
      fpsFrames = 0
      fpsLastTime = now
    }
    // LOD 更新
    if (lod && lodEnabled.value) {
      lod.update(camera)
    }
  })
}

function loadModel(url: string) {
  if (!threeScene) return
  const { scene } = threeScene

  // 递增 epoch 使任何正在进行的 STL 加载回调失效
  loadEpoch += 1
  const currentEpoch = loadEpoch

  if (modelMesh) {
    scene.remove(modelMesh)
    modelMesh.geometry?.dispose()
    ;(modelMesh.material as THREE.Material)?.dispose()
    modelMesh = null
  }
  if (lod) {
    scene.remove(lod)
    lod = null
  }

  const loader = new STLLoader()
  loader.load(
    url,
    (geometry) => {
      // 修复竞态：如果期间已发起新加载或对话框已关闭，丢弃本次结果
      if (currentEpoch !== loadEpoch || !threeScene) return
      const { scene } = threeScene

      geometry.computeVertexNormals()
      geometry.center()

      const meshColor = getComputedStyle(document.documentElement)
        .getPropertyValue('--color-dxf-line')
        .trim() || '#5b9bd5'

      const material = new THREE.MeshStandardMaterial({
        color: meshColor,
        metalness: 0.3,
        roughness: 0.4,
        transparent: true,
        opacity: opacity.value,
        side: THREE.DoubleSide,
      })

      const mesh = new THREE.Mesh(geometry, material)
      mesh.castShadow = true
      mesh.receiveShadow = true
      modelMesh = mesh
      scene.add(mesh)

      modelStats.value = {
        vertexCount: geometry.attributes.position.count,
        faceCount: geometry.index
          ? geometry.index.count / 3
          : geometry.attributes.position.count / 3,
        fileSize: props.fileSize ?? 0,
      }

      if (lodEnabled.value) {
        buildLOD(geometry, material)
      }

      fitView()
    },
    () => {},
    () => {
      // STL 加载失败，静默处理；不需要清理 epoch（旧回调自然失效）
    },
  )
}

function buildLOD(geometry: THREE.BufferGeometry, baseMaterial: THREE.Material) {
  if (!threeScene || !modelMesh) return
  const { scene } = threeScene

  scene.remove(modelMesh)

  lod = new THREE.LOD()

  const fullGeo = geometry.clone()
  const fullMesh = new THREE.Mesh(fullGeo, baseMaterial.clone())
  lod.addLevel(fullMesh, 0)

  for (let i = 0; i < LOD_SIMPLIFY_RATIOS.length; i++) {
    const simplified = simplifyGeometry(geometry, LOD_SIMPLIFY_RATIOS[i])
    if (simplified) {
      const mat = baseMaterial.clone()
      const simpleMesh = new THREE.Mesh(simplified, mat)
      lod.addLevel(simpleMesh, LOD_DISTANCES[i])
    }
  }

  scene.add(lod)
  modelMesh = fullMesh
}

function simplifyGeometry(
  geometry: THREE.BufferGeometry,
  ratio: number,
): THREE.BufferGeometry | null {
  try {
    const positions = geometry.attributes.position.array as Float32Array
    const vertexCount = positions.length / 3

    const step = Math.max(2, Math.round(1 / (1 - ratio)))
    if (step <= 1) return null

    const newPositions: number[] = []
    const seen = new Set<string>()

    for (let i = 0; i < vertexCount; i += step) {
      const x = positions[i * 3]
      const y = positions[i * 3 + 1]
      const z = positions[i * 3 + 2]
      const key = `${Math.round(x * 100)},${Math.round(y * 100)},${Math.round(z * 100)}`
      if (!seen.has(key)) {
        seen.add(key)
        newPositions.push(x, y, z)
      }
    }

    const newGeo = new THREE.BufferGeometry()
    newGeo.setAttribute(
      'position',
      new THREE.Float32BufferAttribute(newPositions, 3),
    )
    newGeo.computeVertexNormals()
    return newGeo
  } catch {
    return null
  }
}

function fitView() {
  if (!threeScene || !modelMesh) return
  const { camera, controls } = threeScene
  const box = new THREE.Box3().setFromObject(modelMesh)
  const center = box.getCenter(new THREE.Vector3())
  const size = box.getSize(new THREE.Vector3())
  const maxDim = Math.max(size.x, size.y, size.z)
  const fov = camera.fov * (Math.PI / 180)
  const distance = maxDim / (2 * Math.tan(fov / 2)) * 1.5

  controls.target.copy(center)
  camera.position.set(
    center.x + distance * 0.7,
    center.y - distance * 0.6,
    center.z + distance * 0.5,
  )
  controls.update()
}

function viewFront() {
  if (!threeScene) return
  const { camera, controls } = threeScene
  const target = controls.target.clone()
  camera.position.set(target.x, target.y, target.z + VIEW_DISTANCE.FRONT)
  controls.update()
}

function viewTop() {
  if (!threeScene) return
  const { camera, controls } = threeScene
  const target = controls.target.clone()
  camera.position.set(target.x, target.y + VIEW_DISTANCE.TOP, target.z)
  controls.update()
}

function viewRight() {
  if (!threeScene) return
  const { camera, controls } = threeScene
  const target = controls.target.clone()
  camera.position.set(target.x + VIEW_DISTANCE.RIGHT, target.y, target.z)
  controls.update()
}

function viewIso() {
  if (!threeScene) return
  const { camera, controls } = threeScene
  const target = controls.target.clone()
  const d = VIEW_DISTANCE.ISO
  camera.position.set(target.x + d, target.y - d, target.z + d)
  controls.update()
}

function updateOpacity() {
  if (!modelMesh) return
  const mat = modelMesh.material as THREE.MeshStandardMaterial
  mat.opacity = opacity.value
}

function toggleGrid() {
  if (!threeScene) return
  const { scene } = threeScene
  if (showGrid.value) {
    if (gridHelper) scene.add(gridHelper)
  } else {
    if (gridHelper) scene.remove(gridHelper)
  }
}

function toggleLOD() {
  if (!threeScene || !modelMesh) return
  const { scene } = threeScene
  if (lodEnabled.value) {
    const geometry = modelMesh.geometry
    const material = modelMesh.material as THREE.Material
    buildLOD(geometry, material)
  } else {
    if (lod) {
      scene.remove(lod)
      lod = null
    }
    if (modelMesh && !scene.children.includes(modelMesh)) {
      scene.add(modelMesh)
    }
  }
}

function disposeViewer() {
  // 递增 epoch，使任何 in-flight STL 加载回调立即失效，
  // 防止回调写入已 dispose 的 threeScene / modelMesh 引用
  loadEpoch += 1

  if (threeScene) {
    threeScene.cleanup()
    threeScene = null
  }

  if (modelMesh) {
    modelMesh.geometry?.dispose()
    ;(modelMesh.material as THREE.Material)?.dispose()
    modelMesh = null
  }
  if (lod) {
    lod.traverse((obj) => {
      if (obj instanceof THREE.Mesh) {
        obj.geometry?.dispose()
        ;(obj.material as THREE.Material)?.dispose()
      }
    })
    lod = null
  }
  if (axesHelperRef) {
    axesHelperRef.geometry?.dispose()
    ;(axesHelperRef.material as THREE.Material)?.dispose?.()
    axesHelperRef = null
  }

  if (canvasContainer.value) {
    canvasContainer.value.innerHTML = ''
  }
}

watch(() => props.modelUrl, (newUrl) => {
  if (newUrl && threeScene) {
    loadModel(newUrl)
  }
})

// 组件卸载时释放 Three.js 资源，避免 GPU 内存泄漏
onBeforeUnmount(() => {
  disposeViewer()
})
</script>

<style scoped>
.model-viewer-container {
  display: flex;
  flex-direction: column;
  height: 60vh;
}

.canvas-container {
  flex: 1;
  min-height: 400px;
  border-radius: var(--radius-md);
  overflow: hidden;
  background: var(--bg-code-elevated);
}

.viewer-controls {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  margin-top: 8px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  flex-wrap: wrap;
}

.control-label {
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.fps-display {
  font-size: 12px;
  color: var(--success);
  font-weight: 600;
  font-family: var(--font-mono);
}

.model-info-bar {
  display: flex;
  gap: 16px;
  padding: 6px 12px;
  font-size: 12px;
  color: var(--text-tertiary);
  background: var(--bg-secondary);
  border-radius: var(--radius-xs);
  margin-top: 4px;
}

.model-info-bar span {
  white-space: nowrap;
}
</style>
