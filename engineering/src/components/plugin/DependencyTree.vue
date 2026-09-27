<template>
  <div class="dependency-tree">
    <TreeNode
      v-if="tree"
      :node="tree"
    />
  </div>
</template>

<script setup lang="ts">
import TreeNode from './DependencyTreeNode.vue'
import type { DependencyNode } from '../../stores/plugin'

defineProps<{
  tree: DependencyNode
}>()
</script>

<style scoped>
.dependency-tree {
  padding: 10px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-xs);
}
</style>
