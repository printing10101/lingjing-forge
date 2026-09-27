<template>
  <div class="content-card">
    <div class="content-card__header">
      <span class="content-card__title">{{ t('productionReport.recordsTitle') }}</span>
    </div>
    <div class="content-card__body">
      <el-table
        v-loading="loading"
        :data="records"
        style="width: 100%"
        :empty-text="t('productionReport.emptyRecords')"
        stripe
      >
        <el-table-column
          prop="order_no"
          :label="t('productionReport.colOrderNo')"
          width="140"
        />
        <el-table-column
          prop="product_name"
          :label="t('productionReport.colProductName')"
          min-width="160"
        />
        <el-table-column
          prop="quantity"
          :label="t('productionReport.colQuantity')"
          width="100"
        />
        <el-table-column
          prop="qualified_quantity"
          :label="t('productionReport.colQualifiedQty')"
          width="100"
        />
        <el-table-column
          prop="operator"
          :label="t('productionReport.colOperator')"
          width="100"
        />
        <el-table-column
          prop="status"
          :label="t('productionReport.colStatus')"
          width="90"
        >
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="statusTagType(row.status)"
            >
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="created_at"
          :label="t('productionReport.colCreatedAt')"
          width="170"
        />
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

interface ProductionRecord {
  id: number
  order_no: string
  product_name: string
  quantity: number
  qualified_quantity: number
  operator: string
  status: string
  created_at: string
}

defineProps<{
  records: ProductionRecord[]
  loading: boolean
}>()

function statusTagType(status: string): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  const map: Record<string, 'success' | 'warning' | 'danger' | 'info' | 'primary'> = {
    completed: 'success',
    producing: 'primary',
    pending: 'warning',
    cancelled: 'danger',
    paused: 'info',
  }
  return map[status] || 'info'
}
</script>