<template>
  <el-table
    v-loading="loading"
    :data="materials"
    style="width: 100%"
    stripe
    :empty-text="loadError ? t('materialManagement.emptyTextError') : t('materialManagement.emptyText')"
  >
    <el-table-column
      prop="code"
      :label="t('materialManagement.colMaterialCode')"
      width="120"
    />
    <el-table-column
      prop="name"
      :label="t('materialManagement.colMaterialName')"
      min-width="180"
    />
    <el-table-column
      :label="t('materialManagement.colCategory')"
      width="100"
    >
      <template #default="{ row }">
        <span class="mlt-category">{{ row.category }}</span>
      </template>
    </el-table-column>
    <el-table-column
      prop="quantity"
      :label="t('materialManagement.colQuantity')"
      width="110"
    >
      <template #default="{ row }">
        <span
          :class="{
            'mlt-danger': row.status === t('materialManagement.labelStatusOut'),
            'mlt-warning': row.status === t('materialManagement.labelStatusLow'),
          }"
        >
          {{ row.quantity }}
        </span>
      </template>
    </el-table-column>
    <el-table-column
      prop="safe_quantity"
      :label="t('materialManagement.colSafeQuantity')"
      width="110"
    />
    <el-table-column
      :label="t('materialManagement.colStatus')"
      width="100"
    >
      <template #default="{ row }">
        <el-tag
          :type="stockStatusTagType(row.status)"
          size="small"
          effect="light"
        >
          {{ row.status }}
        </el-tag>
      </template>
    </el-table-column>
    <el-table-column
      :label="t('materialManagement.colActions')"
      width="180"
      fixed="right"
    >
      <template #default="{ row }">
        <el-button
          type="primary"
          text
          size="small"
          @click="emit('view', row as MaterialListItem)"
        >
          {{ t('materialManagement.btnDetail') }}
        </el-button>
        <el-button
          type="primary"
          text
          size="small"
          @click="emit('stock-in', row as MaterialListItem)"
        >
          {{ t('materialManagement.btnStockInRow') }}
        </el-button>
        <el-button
          v-if="row.status === t('materialManagement.labelStatusOut')"
          type="warning"
          text
          size="small"
          @click="emit('purchase', row as MaterialListItem)"
        >
          {{ t('materialManagement.btnPurchase') }}
        </el-button>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
/**
 * 物料列表表格（MaterialManagement 拆分子组件）
 *
 * 纯展示：渲染物料表格（编码/名称/分类/数量/安全库存/状态/操作），
 * 库存状态标签类型局部化，详情/入库/采购通过 emit 上抛。
 */
import { useI18n } from 'vue-i18n'

/** 物料（与主组件 Material 对齐）。 */
interface MaterialListItem {
  id: number
  code: string
  name: string
  spec: string
  category: string
  quantity: number
  safe_quantity: number
  status: string
  location: string
  unit: string
  supplier: string
  created_at: string
  updated_at: string
}

defineProps<{
  /** 物料列表。 */
  materials: MaterialListItem[]
  /** 加载中。 */
  loading: boolean
  /** 加载失败（空状态文案切换）。 */
  loadError: boolean
}>()

const emit = defineEmits<{
  /** 查看详情。 */
  (e: 'view', row: MaterialListItem): void
  /** 入库登记。 */
  (e: 'stock-in', row: MaterialListItem): void
  /** 采购。 */
  (e: 'purchase', row: MaterialListItem): void
}>()

const { t } = useI18n()

/** 库存状态 → 标签类型。 */
function stockStatusTagType(status: string): 'success' | 'warning' | 'danger' {
  if (status === t('materialManagement.labelStatusOut')) return 'danger'
  if (status === t('materialManagement.labelStatusLow')) return 'warning'
  return 'success'
}
</script>

<style scoped>
.mlt-category {
  font-size: 13px;
}

.mlt-danger {
  color: var(--el-color-danger);
  font-weight: 600;
}

.mlt-warning {
  color: var(--el-color-warning);
  font-weight: 600;
}
</style>
