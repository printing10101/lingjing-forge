<template>
  <div
    v-if="visible"
    class="right-click-menu"
    :style="{ left: position.x + 'px', top: position.y + 'px' }"
  >
    <div
      class="menu-item"
      @click="handleDelete"
    >
      <el-icon><Delete /></el-icon>
      <span>{{ $t('rightClickMenu.delete') }}</span>
    </div>
    <div
      class="menu-item"
      @click="handleAdjustFeed"
    >
      <el-icon><Edit /></el-icon>
      <span>{{ $t('rightClickMenu.adjustFeed') }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Delete, Edit } from '@element-plus/icons-vue'

const props = defineProps<{
  visible: boolean
  position: { x: number; y: number }
  segmentId: string
}>()

const emit = defineEmits<{
  'delete': [segmentId: string]
  'adjust-feed': [segmentId: string]
}>()

function handleDelete() {
  emit('delete', props.segmentId)
}

function handleAdjustFeed() {
  emit('adjust-feed', props.segmentId)
}
</script>

<style lang="scss" scoped>
.right-click-menu {
  position: fixed;
  z-index: 1000;
  background: var(--bg-code-elevated);
  border: 1px solid var(--border-code);
  border-radius: var(--radius-md);
  padding: 4px 0;
  min-width: 200px;
  box-shadow: var(--shadow-xl);

  .menu-item {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 16px;
    cursor: pointer;
    color: var(--text-code);
    font-size: 13px;
    transition: background 0.15s;

    &:hover {
      background: var(--accent-amber-bg);
      color: var(--accent-amber);
    }
  }
}
</style>
