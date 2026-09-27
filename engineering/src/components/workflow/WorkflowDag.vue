<template>
  <div class="dag-section">
    <div class="panel-header">
      <span class="panel-title">{{ title }}</span>
      <div
        v-if="currentRunId"
        class="dag-status"
      >
        <el-tag
          :type="getTaskStatusTagType(currentDisplayStatus)"
          size="small"
        >
          {{ getTaskStatusLabel(currentDisplayStatus) }}
        </el-tag>
        <span
          class="stream-indicator"
          :class="{ connected: isStreamConnected, done: isStreamDone }"
        >
          {{ streamStatusText }}
        </span>
      </div>
    </div>

    <div class="dag-canvas-wrapper">
      <el-empty
        v-if="!spec"
        :description="emptyText"
        :image-size="80"
      />
      <svg
        v-else
        :viewBox="`0 0 ${width} ${height}`"
        class="dag-svg"
        preserveAspectRatio="xMidYMid meet"
      >
        <!-- Edges -->
        <path
          v-for="(edge, idx) in edges"
          :key="`edge-${idx}`"
          :d="edge.path"
          :class="['dag-edge', { active: isEdgeActive(edge) }]"
          fill="none"
          @click="emit('edge-click', edge)"
        />
        <!-- Nodes -->
        <g
          v-for="node in nodes"
          :key="node.node_id"
          :transform="`translate(${node.x}, ${node.y})`"
          class="dag-node-group"
          @click="handleNodeClick(node.node_id)"
        >
          <rect
            :width="nodeWidth"
            :height="nodeHeight"
            :rx="6"
            :class="['dag-node-rect', `status-${getNodeStatus(node.node_id)}`]"
          />
          <text
            :x="nodeWidth / 2"
            :y="22"
            text-anchor="middle"
            class="dag-node-title"
          >
            {{ node.node_id }}
          </text>
          <text
            :x="nodeWidth / 2"
            :y="42"
            text-anchor="middle"
            class="dag-node-type"
          >
            {{ node.task_type }}
          </text>
          <text
            :x="nodeWidth / 2"
            :y="62"
            text-anchor="middle"
            class="dag-node-status"
          >
            {{ getTaskStatusLabel(getNodeStatus(node.node_id)) }}
          </text>
        </g>
      </svg>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { LayoutNode, LayoutEdge } from '@/composables/useDagLayout'
import type { WorkflowSpec, TaskStatus } from '@/contracts/task'
import { getTaskStatusTagType, getTaskStatusLabel } from '@/utils/statusHelpers'

const props = defineProps<{
  nodes: LayoutNode[]
  edges: LayoutEdge[]
  width: number
  height: number
  selectedNodeId: string
  nodeWidth: number
  nodeHeight: number
  spec: WorkflowSpec | null
  title: string
  emptyText: string
  currentDisplayStatus: string
  streamStatusText: string
  isStreamConnected: boolean
  isStreamDone: boolean
  currentRunId: string | null
  nodeStatuses: Record<string, TaskStatus>
}>()

const emit = defineEmits<{
  'update:selectedNodeId': [value: string]
  'node-click': [nodeId: string]
  'edge-click': [edge: LayoutEdge]
}>()

function getNodeStatus(nodeId: string): TaskStatus {
  return props.nodeStatuses[nodeId] || 'pending'
}

function isEdgeActive(edge: LayoutEdge): boolean {
  const u = getNodeStatus(edge.upstream)
  const d = getNodeStatus(edge.downstream)
  return u === 'completed' && d !== 'pending'
}

function handleNodeClick(nodeId: string) {
  emit('update:selectedNodeId', nodeId)
  emit('node-click', nodeId)
}
</script>

<style scoped>
.dag-section {
  display: flex;
  flex-direction: column;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-light);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: var(--el-fill-color-light);
}
.panel-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.dag-status {
  display: flex;
  align-items: center;
  gap: 8px;
}
.stream-indicator {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: var(--radius-2xs);
  background: var(--el-fill-color);
  color: var(--el-text-color-secondary);
}
.stream-indicator.connected {
  background: var(--state-success-bg);
  color: var(--state-success);
}
.stream-indicator.done {
  background: var(--el-fill-color-dark);
  color: var(--el-text-color-regular);
}
.dag-canvas-wrapper {
  flex: 1;
  overflow: auto;
  padding: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.dag-svg {
  width: 100%;
  height: 100%;
  min-height: 300px;
}
.dag-edge {
  stroke: var(--el-border-color);
  stroke-width: 1.5;
  fill: none;
  transition: stroke 0.3s;
  cursor: pointer;
}
.dag-edge.active {
  stroke: var(--accent-primary);
  stroke-width: 2;
}
.dag-node-group {
  cursor: pointer;
}
.dag-node-rect {
  stroke-width: 1.5;
  stroke: var(--el-border-color);
  fill: var(--el-bg-color);
  transition: fill 0.3s, stroke 0.3s;
}
.dag-node-rect.status-pending {
  fill: var(--el-fill-color-light);
  stroke: var(--el-border-color);
}
.dag-node-rect.status-running {
  fill: var(--accent-light);
  stroke: var(--accent-primary);
}
.dag-node-rect.status-completed {
  fill: var(--state-success-bg);
  stroke: var(--state-success);
}
.dag-node-rect.status-failed {
  fill: var(--state-error-bg);
  stroke: var(--state-error);
}
.dag-node-rect.status-skipped,
.dag-node-rect.status-cancelled {
  fill: var(--el-fill-color-dark);
  stroke: var(--el-text-color-disabled);
}
.dag-node-title {
  font-size: 12px;
  font-weight: 600;
  fill: var(--el-text-color-primary);
}
.dag-node-type {
  font-size: 10px;
  fill: var(--el-text-color-secondary);
}
.dag-node-status {
  font-size: 10px;
  font-weight: 500;
  fill: var(--el-text-color-regular);
}
</style>