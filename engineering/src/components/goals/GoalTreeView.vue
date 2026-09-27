<template>
  <div class="goal-tree-view">
    <div class="tree-header">
      <h3>{{ t('goals.treeView.title') }}</h3>
      <el-button
        size="small"
        :loading="loading"
        @click="$emit('refresh')"
      >
        <el-icon><Refresh /></el-icon>
        {{ t('goals.treeView.refresh') }}
      </el-button>
    </div>
    <el-tree
      v-loading="loading"
      :data="treeData"
      :props="treeProps"
      node-key="id"
      default-expand-all
      highlight-current
      @node-click="handleNodeClick"
    >
      <template #default="{ data }">
        <span class="tree-node">
          <el-tag
            size="small"
            :type="getGoalLevelTagType(data.level)"
          >
            {{ getGoalLevelLabel(data.level) }}
          </el-tag>
          <span class="node-name">{{ data.name }}</span>
          <el-tag
            size="small"
            :type="getGoalStatusTagType(data.status)"
          >
            {{ getGoalStatusLabel(data.status) }}
          </el-tag>
        </span>
      </template>
    </el-tree>
  </div>
</template>

<script setup lang="ts">
import { Refresh } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import { getGoalLevelLabel, getGoalLevelTagType, getGoalStatusLabel, getGoalStatusTagType } from '@/utils/statusHelpers'

const { t } = useI18n()

interface GoalTreeNode {
  id: string
  name: string
  level: string
  status: string
  children?: GoalTreeNode[]
}

defineProps<{
  treeData: GoalTreeNode[]
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'select', goalId: string): void
  (e: 'refresh'): void
}>()

const treeProps = {
  children: 'children',
  label: 'name',
}

const handleNodeClick = (data: GoalTreeNode) => {
  emit('select', data.id)
}
</script>

<style scoped>
.goal-tree-view {
  padding: 8px;
}

.tree-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.tree-header h3 {
  margin: 0;
  font-size: 16px;
}

.tree-node {
  display: flex;
  align-items: center;
  gap: 8px;
}

.node-name {
  font-weight: 500;
}
</style>
