<template>
  <el-dialog
    :model-value="visible"
    :title="t('materialManagement.dialogDetailTitle')"
    width="520px"
    @update:model-value="$emit('update:visible', $event)"
  >
    <div v-loading="loading" style="min-height: 200px">
      <template v-if="data">
        <el-descriptions :column="1" border>
          <el-descriptions-item :label="t('materialManagement.colMaterialCode')">
            {{ data.code }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('materialManagement.colMaterialName')">
            {{ data.name }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('materialManagement.fieldSpec')">
            {{ data.spec || '—' }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('materialManagement.colCategory')">
            {{ data.category }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('materialManagement.colQuantity')">
            {{ data.quantity }} {{ data.unit || '' }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('materialManagement.fieldSafeQuantity')">
            {{ data.safe_quantity }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('materialManagement.colStatus')">
            <el-tag :type="stockStatusTagType(data.status)" size="small" effect="light">
              {{ data.status }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item :label="t('materialManagement.fieldLocation')">
            {{ data.location || '—' }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('materialManagement.fieldSupplier')">
            {{ data.supplier || '—' }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('materialManagement.fieldUpdatedAt')">
            {{ data.updated_at }}
          </el-descriptions-item>
        </el-descriptions>
      </template>
    </div>
    <template #footer>
      <el-button @click="$emit('update:visible', false)">
        {{ t('materialManagement.btnCancel') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

interface MaterialDetail {
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
  visible: boolean
  loading: boolean
  data: MaterialDetail | null
}>()

defineEmits<{
  'update:visible': [value: boolean]
}>()

function stockStatusTagType(status: string): 'success' | 'warning' | 'danger' | 'info' {
  const map: Record<string, 'success' | 'warning' | 'danger' | 'info'> = {
    [t('materialManagement.labelStatusNormal')]: 'success',
    [t('materialManagement.labelStatusLow')]: 'warning',
    [t('materialManagement.labelStatusOut')]: 'danger',
  }
  return map[status] || 'info'
}
</script>