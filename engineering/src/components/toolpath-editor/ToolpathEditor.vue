<template>
  <div class="toolpath-editor-page">
    <div class="editor-toolbar">
      <div class="toolbar-left">
        <h2 class="page-title">
          {{ $t('toolpathEditor.pageTitle') }}
        </h2>
        <el-button
          size="small"
          type="primary"
          @click="showImportDialog = true"
        >
          <el-icon><FolderOpened /></el-icon>
          {{ $t('toolpathEditor.importGcode') }}
        </el-button>
      </div>

      <div class="toolbar-center">
        <el-button-group>
          <el-button
            size="small"
            :disabled="!store.canUndo"
            :title="$t('toolpathEditor.undoKb')"
            @click="store.undo()"
          >
            <el-icon><RefreshLeft /></el-icon>
            {{ $t('toolpathEditor.undo', { count: store.undoCount }) }} <kbd>{{ $t('toolpathEditor.undoKb') }}</kbd>
          </el-button>
          <el-button
            size="small"
            :disabled="!store.canRedo"
            :title="$t('toolpathEditor.redoKb')"
            @click="store.redo()"
          >
            <el-icon><RefreshRight /></el-icon>
            {{ $t('toolpathEditor.redo', { count: store.redoCount }) }} <kbd>{{ $t('toolpathEditor.redoKb') }}</kbd>
          </el-button>
        </el-button-group>
      </div>

      <div class="toolbar-right">
        <el-tag
          v-if="store.isDirty"
          type="warning"
          effect="dark"
        >
          {{ $t('toolpathEditor.unsavedTag') }}
        </el-tag>
        <el-button
          size="small"
          type="success"
          :disabled="store.activeSegments.length === 0"
          @click="showExportDialog = true"
        >
          <el-icon><Download /></el-icon>
          {{ $t('toolpathEditor.exportGcode') }}
        </el-button>
      </div>
    </div>

    <div class="editor-main">
      <div class="editor-canvas">
        <ToolpathCanvas
          :segments="store.segments"
          :hovered-segment-id="store.hoveredSegmentId"
          @hover-change="onHoverChange"
          @segment-click="onSegmentClick"
          @context-menu="onContextMenu"
        />
      </div>

      <div class="editor-sidebar">
        <div class="sidebar-section">
          <h4>{{ $t('toolpathEditor.infoTitle') }}</h4>
          <div class="stat-row">
            <span>{{ $t('toolpathEditor.statTotal') }}</span>
            <span>{{ store.segments.length }}</span>
          </div>
          <div class="stat-row">
            <span>{{ $t('toolpathEditor.statActive') }}</span>
            <span>{{ store.activeSegments.length }}</span>
          </div>
          <div class="stat-row">
            <span>{{ $t('toolpathEditor.statDeleted') }}</span>
            <span>{{ store.segments.length - store.activeSegments.length }}</span>
          </div>
        </div>

        <div
          v-if="selectedSegment"
          class="sidebar-section"
        >
          <h4>{{ $t('toolpathEditor.selectedTitle') }}</h4>
          <div class="stat-row">
            <span>{{ $t('toolpathEditor.labelType') }}</span>
            <el-tag
              :type="segmentTypeTag(selectedSegment.type)"
              size="small"
            >
              {{ selectedSegment.type.toUpperCase() }}
            </el-tag>
          </div>
          <div class="stat-row">
            <span>{{ $t('toolpathEditor.labelBlock') }}</span>
            <span>#{{ selectedSegment.blockNumber }}</span>
          </div>
          <div class="stat-row">
            <span>{{ $t('toolpathEditor.labelFeedRate') }}</span>
            <span>{{ selectedSegment.feedRate }} {{ $t('toolpathEditor.unitFeedRate') }}</span>
          </div>
          <div class="stat-row">
            <span>{{ $t('toolpathEditor.labelStart') }}</span>
            <span class="coord">X{{ fmt(selectedSegment.startPoint[0]) }} Y{{ fmt(selectedSegment.startPoint[1]) }} Z{{ fmt(selectedSegment.startPoint[2]) }}</span>
          </div>
          <div class="stat-row">
            <span>{{ $t('toolpathEditor.labelEnd') }}</span>
            <span class="coord">X{{ fmt(selectedSegment.endPoint[0]) }} Y{{ fmt(selectedSegment.endPoint[1]) }} Z{{ fmt(selectedSegment.endPoint[2]) }}</span>
          </div>
          <div class="stat-row">
            <span>{{ $t('toolpathEditor.labelSpindle') }}</span>
            <span>{{ selectedSegment.spindleSpeed }} {{ $t('toolpathEditor.unitSpindle') }}</span>
          </div>
        </div>
      </div>
    </div>

    <RightClickMenu
      :visible="contextMenu.visible"
      :position="contextMenu.position"
      :segment-id="contextMenu.segmentId"
      @delete="onDeleteSegment"
      @adjust-feed="onAdjustFeed"
      @click-outside="closeContextMenu"
    />

    <FeedRateDialog
      v-if="feedRateDialog.segmentId"
      v-model:visible="feedRateDialog.visible"
      :segment-id="feedRateDialog.segmentId"
      :current-feed-rate="feedRateDialog.currentFeedRate"
      :segment-type="feedRateDialog.segmentType"
      :segment-block="feedRateDialog.segmentBlock"
      @confirm="onConfirmFeedRate"
    />

    <GCodeExportDialog v-model:visible="showExportDialog" />

    <ToolpathImportDialog v-model:visible="showImportDialog" />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { FolderOpened, RefreshLeft, RefreshRight, Download } from '@element-plus/icons-vue'
import ToolpathCanvas from './ToolpathCanvas.vue'
import RightClickMenu from './RightClickMenu.vue'
import FeedRateDialog from './FeedRateDialog.vue'
import GCodeExportDialog from './GCodeExportDialog.vue'
import ToolpathImportDialog from './ToolpathImportDialog.vue'
import { useToolpathEditorStore } from './stores/toolpathEditor'

const store = useToolpathEditorStore()

const showExportDialog = ref(false)
const showImportDialog = ref(false)

const contextMenu = reactive({
  visible: false,
  position: { x: 0, y: 0 },
  segmentId: '',
})

const feedRateDialog = reactive({
  visible: false,
  segmentId: '',
  currentFeedRate: 500,
  segmentType: '',
  segmentBlock: 0,
})

const selectedSegment = computed(() => {
  if (!store.selectedSegmentId) return null
  return store.segments.find((s) => s.id === store.selectedSegmentId) || null
})

function onHoverChange(segmentId: string | null) {
  store.hoveredSegmentId = segmentId
}

function onSegmentClick(segmentId: string) {
  store.selectedSegmentId = segmentId
  closeContextMenu()
}

function onContextMenu(x: number, y: number, segmentId: string) {
  contextMenu.visible = true
  contextMenu.position = { x, y }
  contextMenu.segmentId = segmentId
}

function closeContextMenu() {
  contextMenu.visible = false
}

function onDeleteSegment(segmentId: string) {
  store.deleteSegment(segmentId)
  closeContextMenu()
}

function onAdjustFeed(segmentId: string) {
  const seg = store.segments.find((s) => s.id === segmentId)
  if (!seg) return

  feedRateDialog.segmentId = segmentId
  feedRateDialog.currentFeedRate = seg.feedRate
  feedRateDialog.segmentType = seg.type
  feedRateDialog.segmentBlock = seg.blockNumber
  feedRateDialog.visible = true
  closeContextMenu()
}

function onConfirmFeedRate(segmentId: string, newFeedRate: number) {
  store.modifyFeedRate(segmentId, newFeedRate)
}

function handleKeydown(e: KeyboardEvent) {
  if (e.ctrlKey && e.key === 'z' && !e.shiftKey) {
    e.preventDefault()
    if (store.canUndo) store.undo()
    return
  }

  if (
    (e.ctrlKey && e.key === 'y') ||
    (e.ctrlKey && e.shiftKey && e.key === 'z')
  ) {
    e.preventDefault()
    if (store.canRedo) store.redo()
    return
  }

  if (e.key === 'Delete' || e.key === 'Backspace') {
    if (
      store.selectedSegmentId &&
      !contextMenu.visible &&
      !showExportDialog.value &&
      !showImportDialog.value &&
      !feedRateDialog.visible
    ) {
      e.preventDefault()
      const seg = store.segments.find((s) => s.id === store.selectedSegmentId)
      if (seg && !seg.isDeleted) {
        store.deleteSegment(store.selectedSegmentId)
      }
    }
  }

  if (e.key === 'Escape') {
    closeContextMenu()
    store.selectedSegmentId = null
  }
}

function handleClickOutside() {
  closeContextMenu()
}

onMounted(() => {
  document.addEventListener('keydown', handleKeydown)
  document.addEventListener('click', handleClickOutside)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleKeydown)
  document.removeEventListener('click', handleClickOutside)
})

function segmentTypeTag(type: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  switch (type) {
    case 'rapid': return 'danger'
    case 'linear': return 'success'
    case 'arc': return 'info'
    case 'dwell': return 'warning'
    default: return 'info'
  }
}

function fmt(v: number): string {
  return v.toFixed(2)
}
</script>

<style lang="scss" scoped>
.toolpath-editor-page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--bg-code);
  color: var(--text-code);
}

.editor-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  background: var(--bg-code-surface);
  border-bottom: 1px solid var(--border-code);
  height: 48px;
  flex-shrink: 0;

  .toolbar-left {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .page-title {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
    color: var(--text-code);
  }

  .toolbar-right {
    display: flex;
    align-items: center;
    gap: 10px;
  }
}

.editor-main {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.editor-canvas {
  flex: 1;
  min-width: 0;
}

.editor-sidebar {
  width: 260px;
  flex-shrink: 0;
  background: var(--bg-code-surface);
  border-left: 1px solid var(--border-code);
  overflow-y: auto;
  padding: 12px;
}

.sidebar-section {
  margin-bottom: 16px;

  h4 {
    margin: 0 0 8px;
    font-size: 13px;
    font-weight: 600;
    color: var(--text-code-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
}

.stat-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 0;
  font-size: 12px;

  span:first-child {
    color: var(--text-code-muted);
  }

  span:last-child {
    color: var(--text-code);
    font-weight: 500;
  }

  .coord {
    font-family: var(--font-mono);
    font-size: 11px;
  }
}
</style>
