import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { onBeforeUnmount } from 'vue'

export interface SceneOptions {
  container: HTMLElement
  backgroundColor?: string
  fov?: number
  cameraPosition?: [number, number, number]
  enableDamping?: boolean
  dampingFactor?: number
  autoRotate?: boolean
  autoRotateSpeed?: number
  showGrid?: boolean
  gridSize?: number
  gridDivisions?: number
}

export interface ThreeSceneReturn {
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  renderer: THREE.WebGLRenderer
  controls: OrbitControls
  addLight: (light: THREE.Light) => void
  startAnimation: (onFrame?: () => void) => void
  stopAnimation: () => void
  cleanup: () => void
}

export function useThreeScene(options: SceneOptions): ThreeSceneReturn {
  const {
    container,
    backgroundColor = '#1a1a2e',
    fov = 60,
    cameraPosition = [0, 50, 100],
    enableDamping = true,
    dampingFactor = 0.05,
    autoRotate = false,
    autoRotateSpeed = 1.0,
    showGrid = false,
    gridSize = 200,
    gridDivisions = 20,
  } = options

  const width = container.clientWidth
  const height = container.clientHeight

  const scene = new THREE.Scene()
  scene.background = new THREE.Color(backgroundColor)

  const camera = new THREE.PerspectiveCamera(fov, width / height, 0.1, 10000)
  camera.position.set(...cameraPosition)

  const renderer = new THREE.WebGLRenderer({
    antialias: true,
    alpha: false,
  })
  renderer.setSize(width, height)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.2
  container.appendChild(renderer.domElement)

  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = enableDamping
  controls.dampingFactor = dampingFactor
  controls.autoRotate = autoRotate
  controls.autoRotateSpeed = autoRotateSpeed

  function addLight(light: THREE.Light) {
    scene.add(light)
  }

  let gridHelper: THREE.GridHelper | null = null
  if (showGrid) {
    gridHelper = new THREE.GridHelper(gridSize, gridDivisions, 0x444444, 0x222222)
    scene.add(gridHelper)
  }

  const resizeObserver = new ResizeObserver(() => {
    const w = container.clientWidth
    const h = container.clientHeight
    camera.aspect = w / h
    camera.updateProjectionMatrix()
    renderer.setSize(w, h)
  })
  resizeObserver.observe(container)

  let animationId: number | null = null

  function startAnimation(onFrame?: () => void) {
    function animate() {
      animationId = requestAnimationFrame(animate)
      controls.update()
      onFrame?.()
      renderer.render(scene, camera)
    }
    animate()
  }

  function stopAnimation() {
    if (animationId) {
      cancelAnimationFrame(animationId)
      animationId = null
    }
  }

  function cleanup() {
    stopAnimation()
    controls.dispose()
    renderer.dispose()
    // 释放 GridHelper 的 geometry 和 material，避免 GPU 内存泄漏
    if (gridHelper) {
      gridHelper.geometry?.dispose()
      ;(gridHelper.material as THREE.Material)?.dispose?.()
      scene.remove(gridHelper)
      gridHelper = null
    }
    if (renderer.domElement.parentNode) {
      renderer.domElement.parentNode.removeChild(renderer.domElement)
    }
    resizeObserver.disconnect()
  }

  onBeforeUnmount(() => {
    cleanup()
  })

  return {
    scene,
    camera,
    renderer,
    controls,
    addLight,
    startAnimation,
    stopAnimation,
    cleanup,
  }
}
