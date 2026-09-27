/**
 * 仿真结果可视化逻辑
 * 提供Three.js云图着色器、力矢量箭头、LOD优化等功能
 */

import * as THREE from 'three'
import type { ForceData, TemperatureData, VibrationData } from '@/api/simulation'

/** 颜色映射配置 */
export interface ColorMapConfig {
  min: number
  max: number
  colors?: THREE.Color[]
}

/** 可视化选项 */
export interface VisualizationOptions {
  showForceVectors: boolean
  showTemperatureMap: boolean
  showVibrationData: boolean
  forceArrowScale: number
  temperatureOpacity: number
  vibrationScale: number
  lodEnabled: boolean
  lodDistance: number
}

/** 默认可视化选项 */
const DEFAULT_OPTIONS: VisualizationOptions = {
  showForceVectors: true,
  showTemperatureMap: true,
  showVibrationData: true,
  forceArrowScale: 1.0,
  temperatureOpacity: 0.7,
  vibrationScale: 1.0,
  lodEnabled: true,
  lodDistance: 100,
}

/** 蓝→红渐变色（色盲友好） */
const COLOR_GRADIENT = [
  new THREE.Color(0x3b4cc0), // 深蓝
  new THREE.Color(0x6688ee), // 浅蓝
  new THREE.Color(0x88ccee), // 青色
  new THREE.Color(0xaaddaa), // 浅绿
  new THREE.Color(0xeeee66), // 黄色
  new THREE.Color(0xee8866), // 橙色
  new THREE.Color(0xcc3333), // 深红
]

/**
 * 根据值获取颜色（蓝→红渐变）
 */
export function getColorForValue(value: number, min: number, max: number): THREE.Color {
  const normalized = Math.max(0, Math.min(1, (value - min) / (max - min)))
  const colorIndex = normalized * (COLOR_GRADIENT.length - 1)
  const lowerIndex = Math.floor(colorIndex)
  const upperIndex = Math.ceil(colorIndex)
  const t = colorIndex - lowerIndex

  const color = new THREE.Color()
  color.lerpColors(COLOR_GRADIENT[lowerIndex], COLOR_GRADIENT[upperIndex], t)
  return color
}

/**
 * 创建力矢量箭头
 */
export function createForceArrow(
  position: [number, number, number],
  direction: [number, number, number],
  magnitude: number,
  scale: number = 1.0
): THREE.ArrowHelper {
  const dir = new THREE.Vector3(...direction).normalize()
  const origin = new THREE.Vector3(...position)
  const length = Math.max(0.1, magnitude * scale * 0.01)
  const color = getColorForValue(magnitude, 0, 1000)

  const arrow = new THREE.ArrowHelper(dir, origin, length, color.getHex(), length * 0.2, length * 0.1)
  return arrow
}

/**
 * 创建力矢量组
 */
export function createForceVectorGroup(
  forceData: ForceData[],
  options: Partial<VisualizationOptions> = {}
): THREE.Group {
  const opts = { ...DEFAULT_OPTIONS, ...options }
  const group = new THREE.Group()
  group.name = 'force-vectors'

  if (!opts.showForceVectors || forceData.length === 0) {
    return group
  }

  // 使用LOD优化
  if (opts.lodEnabled) {
    const lod = new THREE.LOD()

    // 高精度版本（近距离）
    const highDetailGroup = new THREE.Group()
    forceData.forEach((force) => {
      const arrow = createForceArrow(force.position, force.direction, force.magnitude, opts.forceArrowScale)
      highDetailGroup.add(arrow)
    })
    lod.addLevel(highDetailGroup, 0)

    // 低精度版本（远距离）- 只显示主要力矢量
    const lowDetailGroup = new THREE.Group()
    const step = Math.max(1, Math.floor(forceData.length / 20))
    for (let i = 0; i < forceData.length; i += step) {
      const force = forceData[i]
      const arrow = createForceArrow(force.position, force.direction, force.magnitude, opts.forceArrowScale)
      lowDetailGroup.add(arrow)
    }
    lod.addLevel(lowDetailGroup, opts.lodDistance)

    group.add(lod)
  } else {
    forceData.forEach((force) => {
      const arrow = createForceArrow(force.position, force.direction, force.magnitude, opts.forceArrowScale)
      group.add(arrow)
    })
  }

  return group
}

/**
 * 创建温度云图材质
 */
export function createTemperatureMaterial(
  temperatureData: TemperatureData[],
  options: Partial<VisualizationOptions> = {}
): THREE.ShaderMaterial {
  const opts = { ...DEFAULT_OPTIONS, ...options }

  // 计算温度范围
  const temperatures = temperatureData.map((d) => d.temperature)
  const minTemp = Math.min(...temperatures)
  const maxTemp = Math.max(...temperatures)

  // 创建颜色纹理
  const canvas = document.createElement('canvas')
  canvas.width = 256
  canvas.height = 1
  const ctx = canvas.getContext('2d')
  if (!ctx) {
    // 现代浏览器中动态创建的 canvas 几乎不会失败，但 TypeScript 严格要求处理 null
    console.warn('[useSimulationVisualization] createTemperatureMaterial: failed to acquire 2d context')
    return new THREE.ShaderMaterial()
  }

  for (let i = 0; i < 256; i++) {
    const value = minTemp + (maxTemp - minTemp) * (i / 255)
    const color = getColorForValue(value, minTemp, maxTemp)
    ctx.fillStyle = `#${color.getHexString()}`
    ctx.fillRect(i, 0, 1, 1)
  }

  const texture = new THREE.CanvasTexture(canvas)
  texture.needsUpdate = true

  // 自定义着色器材质
  const material = new THREE.ShaderMaterial({
    uniforms: {
      colorMap: { value: texture },
      opacity: { value: opts.temperatureOpacity },
      minValue: { value: minTemp },
      maxValue: { value: maxTemp },
    },
    vertexShader: `
      varying vec3 vPosition;
      varying vec3 vNormal;
      void main() {
        vPosition = position;
        vNormal = normalize(normalMatrix * normal);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform sampler2D colorMap;
      uniform float opacity;
      uniform float minValue;
      uniform float maxValue;
      varying vec3 vPosition;
      varying vec3 vNormal;

      void main() {
        // 使用位置计算温度（简化版本）
        float normalizedPos = (vPosition.y - minValue) / (maxValue - minValue);
        normalizedPos = clamp(normalizedPos, 0.0, 1.0);

        vec4 color = texture2D(colorMap, vec2(normalizedPos, 0.5));

        // 添加简单的光照
        vec3 lightDir = normalize(vec3(0.5, 1.0, 0.5));
        float diffuse = max(dot(vNormal, lightDir), 0.3);

        gl_FragColor = vec4(color.rgb * diffuse, opacity);
      }
    `,
    transparent: true,
    side: THREE.DoubleSide,
  })

  return material
}

/**
 * 创建温度云图网格
 */
export function createTemperatureCloud(
  temperatureData: TemperatureData[],
  baseGeometry: THREE.BufferGeometry,
  options: Partial<VisualizationOptions> = {}
): THREE.Mesh {
  const material = createTemperatureMaterial(temperatureData, options)
  const mesh = new THREE.Mesh(baseGeometry, material)
  mesh.name = 'temperature-cloud'
  return mesh
}

/**
 * 创建振动可视化
 */
export function createVibrationVisualization(
  vibrationData: VibrationData[],
  options: Partial<VisualizationOptions> = {}
): THREE.Group {
  const opts = { ...DEFAULT_OPTIONS, ...options }
  const group = new THREE.Group()
  group.name = 'vibration-data'

  if (!opts.showVibrationData || vibrationData.length === 0) {
    return group
  }

  // 计算振幅范围
  const amplitudes = vibrationData.map((d) => d.amplitude)
  const maxAmplitude = Math.max(...amplitudes)

  vibrationData.forEach((vibration) => {
    // 使用球体表示振动强度
    const radius = Math.max(0.5, vibration.amplitude * opts.vibrationScale * 10)
    const geometry = new THREE.SphereGeometry(radius, 16, 16)
    const color = getColorForValue(vibration.amplitude, 0, maxAmplitude)
    const material = new THREE.MeshBasicMaterial({
      color: color.getHex(),
      transparent: true,
      opacity: 0.6,
      wireframe: true,
    })

    const sphere = new THREE.Mesh(geometry, material)
    sphere.position.set(...vibration.position)
    group.add(sphere)
  })

  return group
}

/**
 * 创建颜色图例
 */
export function createColorLegend(
  min: number,
  max: number,
  _label: string = '数值'
): THREE.Group {
  const group = new THREE.Group()
  group.name = 'color-legend'

  // 创建颜色条
  const canvas = document.createElement('canvas')
  canvas.width = 256
  canvas.height = 32
  const ctx = canvas.getContext('2d')
  if (!ctx) {
    // 现代浏览器中动态创建的 canvas 几乎不会失败，但 TypeScript 严格要求处理 null
    console.warn('[useSimulationVisualization] createColorLegend: failed to acquire 2d context')
    return group
  }

  for (let i = 0; i < 256; i++) {
    const value = min + (max - min) * (i / 255)
    const color = getColorForValue(value, min, max)
    ctx.fillStyle = `#${color.getHexString()}`
    ctx.fillRect(i, 0, 1, 32)
  }

  const texture = new THREE.CanvasTexture(canvas)
  const geometry = new THREE.PlaneGeometry(2, 0.25)
  const material = new THREE.MeshBasicMaterial({
    map: texture,
    side: THREE.DoubleSide,
  })

  const legendMesh = new THREE.Mesh(geometry, material)
  legendMesh.position.set(0, -2, 0)
  group.add(legendMesh)

  return group
}

/**
 * 仿真可视化组合式函数
 */
export function useSimulationVisualization() {
  /**
   * 渲染仿真结果到场景
   */
  function renderSimulationResult(
    scene: THREE.Scene,
    forceData: ForceData[],
    temperatureData: TemperatureData[],
    vibrationData: VibrationData[],
    baseGeometry: THREE.BufferGeometry | null,
    options: Partial<VisualizationOptions> = {}
  ): {
    forceGroup: THREE.Group
    temperatureMesh: THREE.Mesh | null
    vibrationGroup: THREE.Group
    legendGroup: THREE.Group
  } {
    // 创建力矢量
    const forceGroup = createForceVectorGroup(forceData, options)
    scene.add(forceGroup)

    // 创建温度云图
    let temperatureMesh: THREE.Mesh | null = null
    if (baseGeometry && temperatureData.length > 0) {
      temperatureMesh = createTemperatureCloud(temperatureData, baseGeometry, options)
      scene.add(temperatureMesh)
    }

    // 创建振动可视化
    const vibrationGroup = createVibrationVisualization(vibrationData, options)
    scene.add(vibrationGroup)

    // 创建图例
    const legendGroup = new THREE.Group()
    if (forceData.length > 0) {
      const forces = forceData.map((d) => d.magnitude)
      const forceLegend = createColorLegend(Math.min(...forces), Math.max(...forces), '力 (N)')
      forceLegend.position.set(-3, -2, 0)
      legendGroup.add(forceLegend)
    }

    if (temperatureData.length > 0) {
      const temps = temperatureData.map((d) => d.temperature)
      const tempLegend = createColorLegend(Math.min(...temps), Math.max(...temps), '温度 (°C)')
      tempLegend.position.set(3, -2, 0)
      legendGroup.add(tempLegend)
    }

    scene.add(legendGroup)

    return {
      forceGroup,
      temperatureMesh,
      vibrationGroup,
      legendGroup,
    }
  }

  /**
   * 清除场景中的仿真可视化元素
   */
  function clearVisualization(scene: THREE.Scene): void {
    const objectsToRemove: THREE.Object3D[] = []

    scene.traverse((obj) => {
      if (
        obj.name === 'force-vectors' ||
        obj.name === 'temperature-cloud' ||
        obj.name === 'vibration-data' ||
        obj.name === 'color-legend'
      ) {
        objectsToRemove.push(obj)
      }
    })

    objectsToRemove.forEach((obj) => {
      if (obj.parent) {
        obj.parent.remove(obj)
      }

      // 清理资源
      if (obj instanceof THREE.Mesh) {
        obj.geometry?.dispose()
        if (Array.isArray(obj.material)) {
          obj.material.forEach((m) => m.dispose())
        } else {
          obj.material?.dispose()
        }
      }
    })
  }

  /**
   * 更新可视化数据（平滑过渡）
   */
  function updateVisualization(
    scene: THREE.Scene,
    forceData: ForceData[],
    temperatureData: TemperatureData[],
    vibrationData: VibrationData[],
    options: Partial<VisualizationOptions> = {}
  ): void {
    // 先清除旧的可视化元素
    clearVisualization(scene)

    // 重新渲染
    renderSimulationResult(scene, forceData, temperatureData, vibrationData, null, options)
  }

  return {
    renderSimulationResult,
    clearVisualization,
    updateVisualization,
    getColorForValue,
    createForceArrow,
    createForceVectorGroup,
    createTemperatureMaterial,
    createTemperatureCloud,
    createVibrationVisualization,
    createColorLegend,
  }
}
