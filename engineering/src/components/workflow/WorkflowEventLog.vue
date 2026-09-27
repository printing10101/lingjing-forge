<template>
  <div class="event-log-section">
    <div class="panel-header">
      <span class="panel-title">{{ title }}</span>
      <el-button
        v-if="events.length > 0"
        text
        size="small"
        @click="emit('clear')"
      >
        {{ btnClearText }}
      </el-button>
    </div>
    <div
      ref="eventLogEl"
      class="event-log-body"
    >
      <div
        v-if="events.length === 0"
        class="event-log-empty"
      >
        {{ emptyText }}
      </div>
      <div
        v-for="(ev, idx) in events"
        :key="`ev-${idx}`"
        class="event-log-entry"
        :class="`event-${ev.event_type}`"
      >
        <span class="event-time">{{ formatEventTime(ev.timestamp) }}</span>
        <span class="event-type">{{ ev.event_type }}</span>
        <span
          v-if="ev.node_id"
          class="event-node"
        >[{{ ev.node_id }}]</span>
        <span
          v-if="getEventMessage(ev)"
          class="event-msg"
        >{{ getEventMessage(ev) }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import type { WorkflowEvent } from '@/contracts/task'

const props = defineProps<{
  events: WorkflowEvent[]
  title: string
  emptyText: string
  btnClearText: string
}>()

const emit = defineEmits<{
  clear: []
}>()

// 自动滚动到底部
const eventLogEl = ref<HTMLElement | null>(null)

watch(
  () => props.events.length,
  async () => {
    await nextTick()
    if (eventLogEl.value) {
      eventLogEl.value.scrollTop = eventLogEl.value.scrollHeight
    }
  },
)

function getEventMessage(ev: WorkflowEvent): string {
  const payload = ev.payload as unknown as Record<string, unknown>
  if (typeof payload?.error === 'string') return payload.error
  if (typeof payload?.message === 'string') return payload.message
  if (typeof payload?.progress === 'number') return `${Math.round(payload.progress * 100)}%`
  return ''
}

function formatEventTime(ts: number): string {
  if (!ts) return '--:--:--'
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString('zh-CN', { hour12: false })
}
</script>

<style scoped>
.event-log-section {
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
.event-log-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px 12px;
  font-family: var(--font-mono);
  font-size: 12px;
  background: var(--el-fill-color-blank);
}
.event-log-empty {
  color: var(--el-text-color-secondary);
  text-align: center;
  padding: 20px 0;
}
.event-log-entry {
  padding: 3px 0;
  border-bottom: 1px dashed var(--el-border-color-lighter);
  display: flex;
  gap: 8px;
  align-items: baseline;
}
.event-time {
  color: var(--el-text-color-secondary);
  flex-shrink: 0;
}
.event-type {
  font-weight: 600;
  flex-shrink: 0;
  min-width: 110px;
}
.event-node {
  color: var(--accent-primary);
  flex-shrink: 0;
}
.event-msg {
  color: var(--el-text-color-regular);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.event-node_started .event-type { color: var(--accent-primary); }
.event-node_completed .event-type { color: var(--state-success); }
.event-node_failed .event-type { color: var(--state-error); }
.event-node_skipped .event-type { color: var(--el-text-color-secondary); }
.event-workflow_completed .event-type { color: var(--state-success); }
.event-workflow_failed .event-type { color: var(--state-error); }
</style>