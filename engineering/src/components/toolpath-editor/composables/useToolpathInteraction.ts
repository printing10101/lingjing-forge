import * as THREE from 'three'
import type { EditableToolpathSegment } from '../types/editor'
import type { Ref } from 'vue'

export interface ToolpathInteractionState {
  hoveredSegmentId: string | null
  selectedSegmentId: string | null
  contextMenuTarget: { x: number; y: number; segmentId: string } | null
}

export function useToolpathInteraction(
  canvasRef: Ref<HTMLElement | undefined>,
  camera: Ref<THREE.PerspectiveCamera | null>,
  segmentLines: Ref<Map<string, THREE.Line>>,
  _segments: Ref<EditableToolpathSegment[]>,
  onHoverChange: (segmentId: string | null) => void,
  onContextMenu: (x: number, y: number, segmentId: string) => void,
  onSegmentClick: (segmentId: string) => void,
) {
  const raycaster = new THREE.Raycaster()
  raycaster.params.Line = { threshold: 0.3 }

  const hoverHighlightMaterial = new THREE.LineBasicMaterial({
    color: 0xffd740,
    linewidth: 2,
    transparent: true,
    opacity: 1.0,
  })

  const selectedHighlightMaterial = new THREE.LineBasicMaterial({
    color: 0x00e5ff,
    linewidth: 2,
    transparent: true,
    opacity: 1.0,
  })

  let currentHoveredLine: THREE.Line | null = null
  let currentHoveredId: string | null = null
  const originalMaterials = new Map<THREE.Line, THREE.Material>()
  let highlightTimeoutId: number | null = null

  function onMouseMove(event: MouseEvent): void {
    if (!canvasRef.value || !camera.value) return

    const rect = canvasRef.value.getBoundingClientRect()
    const mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    )

    raycaster.setFromCamera(mouse, camera.value)

    const lineObjects: THREE.Line[] = []
    segmentLines.value.forEach((line) => lineObjects.push(line))

    const intersects = raycaster.intersectObjects(lineObjects, false)

    if (intersects.length > 0) {
      const hitLine = intersects[0].object as THREE.Line
      const hitId = findSegmentIdByLine(hitLine, segmentLines.value)

      if (hitId && hitId !== currentHoveredId) {
        clearHoverHighlight()
        hoverHighlight(hitLine)
        currentHoveredLine = hitLine
        currentHoveredId = hitId
        onHoverChange(hitId)
      }
    } else {
      if (currentHoveredLine) {
        clearHoverHighlight()
        currentHoveredLine = null
        currentHoveredId = null
        onHoverChange(null)
      }
    }
  }

  function onContextMenuEvent(event: MouseEvent): void {
    event.preventDefault()
    if (!canvasRef.value || !camera.value) return

    const rect = canvasRef.value.getBoundingClientRect()
    const mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    )

    raycaster.setFromCamera(mouse, camera.value)

    const lineObjects: THREE.Line[] = []
    segmentLines.value.forEach((line) => lineObjects.push(line))

    const intersects = raycaster.intersectObjects(lineObjects, false)

    if (intersects.length > 0) {
      const hitLine = intersects[0].object as THREE.Line
      const hitId = findSegmentIdByLine(hitLine, segmentLines.value)

      if (hitId) {
        onContextMenu(event.clientX, event.clientY, hitId)
      }
    }
  }

  function onClick(event: MouseEvent): void {
    if (!canvasRef.value || !camera.value) return

    const rect = canvasRef.value.getBoundingClientRect()
    const mouse = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    )

    raycaster.setFromCamera(mouse, camera.value)

    const lineObjects: THREE.Line[] = []
    segmentLines.value.forEach((line) => lineObjects.push(line))

    const intersects = raycaster.intersectObjects(lineObjects, false)

    if (intersects.length > 0) {
      const hitLine = intersects[0].object as THREE.Line
      const hitId = findSegmentIdByLine(hitLine, segmentLines.value)
      if (hitId) {
        onSegmentClick(hitId)
      }
    }
  }

  function hoverHighlight(line: THREE.Line): void {
    if (!originalMaterials.has(line)) {
      originalMaterials.set(line, line.material as THREE.Material)
    }
    line.material = hoverHighlightMaterial
  }

  function clearHoverHighlight(): void {
    if (currentHoveredLine && originalMaterials.has(currentHoveredLine)) {
      // has() 已确保存在，但 TS 无法通过 has 窄化 Map.get 的返回类型，故先取出再校验
      const material = originalMaterials.get(currentHoveredLine)
      if (material) {
        currentHoveredLine.material = material
      }
      originalMaterials.delete(currentHoveredLine)
    }
  }

  function selectHighlight(line: THREE.Line): void {
    line.material = selectedHighlightMaterial
    highlightTimeoutId = window.setTimeout(() => {
      if (line && originalMaterials.has(line)) {
        const material = originalMaterials.get(line)
        if (material) {
          line.material = material
        }
        originalMaterials.delete(line)
      }
      highlightTimeoutId = null
    }, 800)
  }

  function findSegmentIdByLine(
    line: THREE.Line,
    lineMap: Map<string, THREE.Line>,
  ): string | null {
    for (const [id, l] of lineMap.entries()) {
      if (l === line) return id
    }
    return null
  }

  function dispose(): void {
    if (highlightTimeoutId !== null) {
      clearTimeout(highlightTimeoutId)
      highlightTimeoutId = null
    }
    clearHoverHighlight()
    hoverHighlightMaterial.dispose()
    selectedHighlightMaterial.dispose()
  }

  return {
    onMouseMove,
    onContextMenuEvent,
    onClick,
    selectHighlight,
    dispose,
  }
}

export function createSegmentLine(
  segment: EditableToolpathSegment,
  color: number,
  opacity: number = 0.9,
): THREE.Line {
  const points = [
    new THREE.Vector3(...segment.startPoint),
    new THREE.Vector3(...segment.endPoint),
  ]
  const geometry = new THREE.BufferGeometry().setFromPoints(points)
  const material = new THREE.LineBasicMaterial({
    color,
    linewidth: 1,
    transparent: true,
    opacity: segment.type === 'rapid' ? 0.4 : opacity,
  })
  return new THREE.Line(geometry, material)
}

export function getSegmentColor(type: string): number {
  switch (type) {
    case 'rapid': return 0xff5252
    case 'linear': return 0x4caf50
    case 'arc': return 0x448aff
    case 'dwell': return 0xffc107
    default: return 0xffffff
  }
}
