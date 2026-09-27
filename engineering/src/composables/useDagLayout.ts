import { computed } from 'vue'
import type { WorkflowSpec, TaskStatus } from '@/contracts/task'

const nodeWidth = 160
const nodeHeight = 76
const layerGapX = 220
const padding = 40

export interface LayoutNode {
  node_id: string
  task_type: string
  x: number
  y: number
  layer: number
}

export interface LayoutEdge {
  path: string
  upstream: string
  downstream: string
}

export function useDagLayout(spec: () => WorkflowSpec | null) {
  return computed(() => {
    const s = spec()
    if (!s || s.nodes.length === 0) {
      return { nodes: [] as LayoutNode[], edges: [] as LayoutEdge[], width: 0, height: 0 }
    }

    const adj = new Map<string, string[]>()
    const inDegree = new Map<string, number>()
    s.nodes.forEach(n => { adj.set(n.node_id, []); inDegree.set(n.node_id, 0) })
    s.edges.forEach(e => {
      adj.get(e.upstream)?.push(e.downstream)
      inDegree.set(e.downstream, (inDegree.get(e.downstream) ?? 0) + 1)
    })

    const layers: string[][] = []
    const remaining = new Map(inDegree)
    const visited = new Set<string>()

    while (visited.size < s.nodes.length) {
      const layer = s.nodes.map(n => n.node_id).filter(id => !visited.has(id) && (remaining.get(id) ?? 0) === 0)
      if (layer.length === 0) {
        const leftover = s.nodes.map(n => n.node_id).filter(id => !visited.has(id))
        layers.push(leftover)
        leftover.forEach(id => visited.add(id))
        break
      }
      layers.push(layer)
      layer.forEach(id => {
        visited.add(id)
        adj.get(id)?.forEach(down => { remaining.set(down, (remaining.get(down) ?? 1) - 1) })
      })
    }

    const nodeMap = new Map(s.nodes.map(n => [n.node_id, n]))
    const lNodes: LayoutNode[] = []
    const maxLayerSize = Math.max(...layers.map(l => l.length), 1)

    layers.forEach((layer, layerIdx) => {
      const layerH = layer.length * nodeHeight + (layer.length - 1) * 20
      const startY = (maxLayerSize * (nodeHeight + 20) - layerH) / 2 + padding
      layer.forEach((nodeId, idx) => {
        const node = nodeMap.get(nodeId)
        lNodes.push({ node_id: nodeId, task_type: node?.task_type ?? '', x: padding + layerIdx * layerGapX, y: startY + idx * (nodeHeight + 20), layer: layerIdx })
      })
    })

    const nodePos = new Map(lNodes.map(n => [n.node_id, n]))
    const lEdges: LayoutEdge[] = s.edges.map(e => {
      const u = nodePos.get(e.upstream); const d = nodePos.get(e.downstream)
      if (!u || !d) return { path: '', upstream: e.upstream, downstream: e.downstream }
      const x1 = u.x + nodeWidth / 2; const y1 = u.y + nodeHeight
      const x2 = d.x + nodeWidth / 2; const y2 = d.y
      const midY = (y1 + y2) / 2
      return { path: `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`, upstream: e.upstream, downstream: e.downstream }
    }).filter(e => e.path !== '')

    return { nodes: lNodes, edges: lEdges, width: padding * 2 + layers.length * layerGapX, height: padding * 2 + maxLayerSize * (nodeHeight + 20) }
  })
}

export function isEdgeActive(edge: LayoutEdge, getNodeStatus: (id: string) => TaskStatus): boolean {
  return getNodeStatus(edge.upstream) === 'completed' && getNodeStatus(edge.downstream) !== 'pending'
}
