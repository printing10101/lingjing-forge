<template>
  <div class="content-card">
    <div class="content-card__header">
      <span class="content-card__title">{{ t('home.cardProductionProgress') }}</span>
      <el-tag
        v-if="error"
        type="warning"
        size="small"
        effect="plain"
      >
        {{ t('home.msgDataLoadFailed') }}
      </el-tag>
    </div>
    <div class="content-card__body">
      <el-table
        :data="workOrders"
        style="width: 100%"
        stripe
      >
        <el-table-column
          prop="orderNo"
          :label="t('home.labelOrderNo')"
          width="130"
        />
        <el-table-column
          prop="productName"
          :label="t('home.labelProductName')"
          min-width="140"
        />
        <el-table-column
          prop="process"
          :label="t('home.labelProcess')"
          width="100"
        />
        <el-table-column
          :label="t('home.labelProgress')"
          width="180"
        >
          <template #default="{ row }">
            <el-progress
              :percentage="row.progress"
              :stroke-width="8"
              :color="row.progress === 100 ? 'var(--success)' : 'var(--accent-primary)'"
            />
          </template>
        </el-table-column>
        <el-table-column
          :label="t('home.labelStatus')"
          width="100"
          align="center"
        >
          <template #default="{ row }">
            <el-tag
              :type="statusTagType(row.status)"
              size="small"
              effect="light"
            >
              {{ row.statusLabel }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('home.labelAction')"
          width="80"
          align="center"
        >
          <template #default="{ row }">
            <el-button
              type="primary"
              text
              size="small"
              @click="$emit('view-detail', row as WorkOrder)"
            >
              {{ t('home.btnDetail') }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script lang="ts">
export interface WorkOrder {
  orderNo: string
  productName: string
  process: string
  progress: number
  status: string
  statusLabel: string
}
</script>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

defineProps<{
  workOrders: WorkOrder[]
  error: boolean
}>()

defineEmits<{
  'view-detail': [row: WorkOrder]
}>()

const { t } = useI18n()

function statusTagType(status: string): 'success' | 'warning' | 'info' | 'danger' | 'primary' {
  if (status === 'completed') return 'success'
  if (status === 'running' || status === 'queued') return 'warning'
  if (status === 'pending') return 'info'
  if (status === 'failed') return 'danger'
  if (status === 'cancelled') return 'info'
  return 'info'
}
</script>

<style scoped>
.content-card {
  background: var(--bg-0);
  border: 1px solid var(--bg-200);
  border-radius: var(--radius-lg);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.content-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  border-bottom: 1px solid var(--bg-100);
  flex-shrink: 0;
}

.content-card__title {
  font-size: 0.9375rem;
  font-weight: 600;
  color: var(--text-primary);
}

.content-card__body {
  padding: 0;
  flex: 1;
}
</style>