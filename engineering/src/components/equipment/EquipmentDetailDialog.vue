<template>
  <el-dialog
    :model-value="visible"
    :title="t('equipmentMonitor.dialogDetailTitle')"
    width="520px"
    @update:model-value="emit('update:visible', $event)"
  >
    <div v-loading="loading" style="min-height: 200px">
      <template v-if="device">
        <el-descriptions :column="1" border>
          <el-descriptions-item :label="t('equipmentMonitor.colDeviceId')">
            {{ device.id }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('equipmentMonitor.colDeviceName')">
            {{ device.name }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('equipmentMonitor.fieldModel')">
            {{ device.model || '—' }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('equipmentMonitor.fieldLocation')">
            {{ device.location || '—' }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('equipmentMonitor.colStatus')">
            <el-tag :type="statusTagType(device.status)" size="small" effect="light">
              {{ device.status }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item :label="t('equipmentMonitor.fieldTemperature')">
            {{ device.temperature ?? '—' }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('equipmentMonitor.fieldVibration')">
            {{ device.vibration ?? '—' }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('equipmentMonitor.fieldRpm')">
            {{ device.rpm ?? '—' }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('equipmentMonitor.fieldPower')">
            {{ device.power ?? '—' }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('equipmentMonitor.fieldUpdatedAt')">
            {{ device.updated_at }}
          </el-descriptions-item>
        </el-descriptions>
      </template>
    </div>
    <template #footer>
      <el-button @click="emit('update:visible', false)">
        {{ t('equipmentMonitor.btnCancel') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

export interface Device {
  id: number
  name: string
  model: string
  location: string
  status: string
  temperature: number | null
  vibration: number | null
  rpm: number | null
  power: number | null
  created_at: string
  updated_at: string
}

defineProps<{
  visible: boolean
  loading: boolean
  device: Device | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', val: boolean): void
}>()

// 状态映射
const STATUS_TO_TAG_TYPE: Record<string, 'success' | 'info' | 'danger' | 'warning'> = {
  [t('equipmentMonitor.labelStatusRunning')]: 'success',
  [t('equipmentMonitor.labelStatusStandby')]: 'info',
  [t('equipmentMonitor.labelStatusFault')]: 'danger',
  [t('equipmentMonitor.labelStatusMaintenance')]: 'warning',
}

function statusTagType(status: string): 'success' | 'info' | 'danger' | 'warning' {
  return STATUS_TO_TAG_TYPE[status] || 'info'
}
</script>
