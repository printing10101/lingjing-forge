<template>
  <div class="tree-node">
    <div class="node-content">
      <el-icon
        v-if="node.dependencies?.length"
        class="expand-icon"
        @click="expanded = !expanded"
      >
        <ArrowRight v-if="!expanded" />
        <ArrowDown v-else />
      </el-icon>
      <span class="node-name">{{ node.name }}</span>
      <el-tag
        size="small"
        style="margin-left: 5px"
      >
        {{ node.version }}
      </el-tag>
      <el-tag
        v-if="node.status === 'missing'"
        type="danger"
        size="small"
        style="margin-left: 5px"
      >
        {{ $t('dependencyTree.statusMissing') }}
      </el-tag>
    </div>
    <div
      v-if="expanded && node.dependencies?.length"
      class="children"
    >
      <TreeNode
        v-for="child in node.dependencies"
        :key="child.id"
        :node="child"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ArrowRight, ArrowDown } from '@element-plus/icons-vue'
import type { DependencyNode } from '../../stores/plugin'

defineProps<{
  node: DependencyNode
}>()

const expanded = ref(false)
</script>

<style scoped>
.tree-node {
  margin-left: 20px;
}
.node-content {
  display: flex;
  align-items: center;
  padding: 5px 0;
}
.expand-icon {
  cursor: pointer;
  margin-right: 5px;
}
.children {
  margin-left: 10px;
}
</style>
