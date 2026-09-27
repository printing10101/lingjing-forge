<template>
  <div class="content-card">
    <div class="content-card__header">
      <span class="content-card__title">{{ t('productionReport.workOrdersTitle') }}</span>
    </div>
    <div class="content-card__body">
      <el-table
        v-loading="loading"
        :data="workOrders"
        style="width: 100%"
        :empty-text="t('productionReport.emptyWorkOrders')"
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
          :label="t('productionReport.colQtyShort')"
          width="90"
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
          prop="priority"
          :label="t('productionReport.colPriority')"
          width="80"
        />
        <el-table-column
          prop="deadline"
          :label="t('productionReport.colDeadline')"
          width="120"
        />
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

interface WorkOrder {
  id: number
  order_no: string
  product_name: string
  quantity: number
  status: string
  priority: string
  deadline: string
  created_at: string
}

defineProps<{
  workOrders: WorkOrder[]
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