<template>
  <el-table
    v-loading="loading"
    :data="records"
    style="width: 100%"
    stripe
    :empty-text="loadError ? t('qualityInspection.emptyLoadFailed') : t('qualityInspection.emptyRecords')"
  >
    <el-table-column
      prop="inspection_no"
      :label="t('qualityInspection.colId')"
      width="180"
    />
    <el-table-column
      prop="inspection_type"
      :label="t('qualityInspection.colProductName')"
      min-width="140"
    />
    <el-table-column
      prop="batch_no"
      :label="t('qualityInspection.colBatch')"
      width="150"
    />
    <el-table-column
      :label="t('qualityInspection.colResult')"
      width="100"
    >
      <template #default="{ row }">
        <el-tag
          :type="resultTagType(row.result)"
          size="small"
          effect="light"
        >
          {{ row.result }}
        </el-tag>
      </template>
    </el-table-column>
    <el-table-column
      prop="inspector"
      :label="t('qualityInspection.colInspector')"
      width="100"
    />
    <el-table-column
      prop="created_at"
      :label="t('qualityInspection.colTime')"
      width="170"
    />
    <el-table-column
      :label="t('qualityInspection.colAction')"
      width="100"
      fixed="right"
    >
      <template #default="{ row }">
        <el-button
          type="primary"
          text
          size="small"
          @click="emit('view', row as QualityRecord)"
        >
          {{ t('qualityInspection.btnView') }}
        </el-button>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
/**
 * 质检记录表格（QualityInspection 拆分子组件）
 *
 * 纯展示：渲染检测记录表格（编号/类型/批次/结果/检验员/时间/操作），
 * 结果标签类型局部化，点击查看通过 emit 上抛。
 */
import { useI18n } from 'vue-i18n'

/** 检测记录（与主组件 InspectionRecord 对齐）。 */
interface QualityRecord {
  id: string | number
  inspection_no: string
  batch_no: string
  inspection_type: string
  result: string
  inspector: string
  notes?: string | null
  created_at: string
  updated_at?: string
}

defineProps<{
  /** 检测记录列表。 */
  records: QualityRecord[]
  /** 加载中。 */
  loading: boolean
  /** 加载失败（空状态文案切换）。 */
  loadError: boolean
}>()

const emit = defineEmits<{
  /** 查看详情。 */
  (e: 'view', row: QualityRecord): void
}>()

const { t } = useI18n()

/** 结果 → 标签类型。 */
function resultTagType(result: string): 'success' | 'danger' | 'warning' {
  if (result.includes('合格')) return 'success'
  if (result.includes('不合格')) return 'danger'
  return 'warning'
}
</script>
