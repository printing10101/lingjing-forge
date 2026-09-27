<template>
  <div
    class="prc-card"
    @click="emit('select', route)"
  >
    <div class="prc-header">
      <span class="prc-title">{{ route.name }}</span>
      <el-tag
        :type="statusTagType(route.status)"
        size="small"
        effect="light"
        class="prc-status-tag"
      >
        {{ route.status }}
      </el-tag>
    </div>
    <p class="prc-description">
      {{ route.description }}
    </p>
    <div class="prc-meta">
      <span class="prc-meta-item">
        <el-icon :size="14"><Operation /></el-icon>
        {{ route.steps?.length || 0 }}{{ t('processPlanning.routePage.stepCountSuffix') }}
      </span>
      <span class="prc-meta-item">
        <el-icon :size="14"><Document /></el-icon>
        {{ route.version }}
      </span>
      <span class="prc-meta-item">
        <el-icon :size="14"><Clock /></el-icon>
        {{ route.updated_at?.split('T')[0] || '-' }}
      </span>
    </div>
    <div
      class="prc-actions"
      @click.stop
    >
      <el-button
        text
        type="primary"
        size="small"
        @click.stop="emit('view', route)"
      >
        {{ t('processPlanning.routePage.btnView') }}
      </el-button>
      <el-button
        text
        type="primary"
        size="small"
        @click.stop="emit('edit', route)"
      >
        {{ t('processPlanning.routePage.btnEdit') }}
      </el-button>
      <el-button
        text
        type="primary"
        size="small"
        @click.stop="emit('copy', route)"
      >
        {{ t('processPlanning.routePage.btnCopy') }}
      </el-button>
      <el-button
        text
        type="danger"
        size="small"
        @click.stop="emit('delete', route)"
      >
        {{ t('processPlanning.routePage.btnDelete') }}
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 工艺路线卡片（ProcessPlanning 拆分子组件）
 *
 * 纯展示：渲染单条工艺路线卡片（标题/状态/描述/元信息/操作按钮），
 * 通过 emits 上抛交互事件，消除主组件中的卡片内联模板。
 */
import { useI18n } from 'vue-i18n'
import { Operation, Document, Clock } from '@element-plus/icons-vue'

/** 工艺步骤（与主组件 ProcessStep 对齐）。 */
interface ProcessStep {
  name: string
  description: string
  duration: string
  tool_id?: number
  parameters?: Record<string, unknown>
}

/** 工艺路线（与主组件 ProcessRoute 对齐）。 */
interface ProcessRouteCardItem {
  id: number
  name: string
  part_type: string
  description: string
  status: string
  version: string
  material_type: string
  steps: ProcessStep[]
  created_at: string
  updated_at: string
}

defineProps<{
  /** 单条工艺路线。 */
  route: ProcessRouteCardItem
}>()

const emit = defineEmits<{
  /** 点击卡片（查看详情）。 */
  (e: 'select', route: ProcessRouteCardItem): void
  /** 查看。 */
  (e: 'view', route: ProcessRouteCardItem): void
  /** 编辑。 */
  (e: 'edit', route: ProcessRouteCardItem): void
  /** 复制。 */
  (e: 'copy', route: ProcessRouteCardItem): void
  /** 删除。 */
  (e: 'delete', route: ProcessRouteCardItem): void
}>()

const { t } = useI18n()

/** 状态 → 标签类型。 */
function statusTagType(status: string): 'success' | 'warning' | 'info' {
  if (status.includes('发布') || status === 'published') return 'success'
  if (status.includes('归档') || status === 'archived') return 'info'
  return 'warning'
}
</script>

<style scoped>
.prc-card {
  background: var(--bg-0);
  border: 1px solid var(--bg-200, var(--el-border-color-light));
  border-radius: var(--radius-lg);
  padding: 16px;
  cursor: pointer;
  transition: box-shadow 0.2s ease, transform 0.2s ease;
}

.prc-card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
  transform: translateY(-2px);
}

.prc-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.prc-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.prc-description {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 12px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.prc-meta {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 12px;
}

.prc-meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.prc-actions {
  display: flex;
  gap: 4px;
  justify-content: flex-end;
  border-top: 1px solid var(--bg-100, var(--el-border-color-lighter));
  padding-top: 10px;
}
</style>
