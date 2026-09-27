<template>
  <el-table :data="plugins" stripe>
    <el-table-column prop="id" :label="t('pluginManager.colId')" width="150" />
    <el-table-column prop="name" :label="t('pluginManager.colName')" width="150" />
    <el-table-column prop="version" :label="t('pluginManager.colVersion')" width="80" />
    <el-table-column v-if="showType" prop="plugin_type" :label="t('pluginManager.colType')" width="100">
      <template #default="{ row }">
        <el-tag size="small">{{ row.plugin_type }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column prop="status" :label="t('pluginManager.colStatus')" width="100">
      <template #default="{ row }">
        <el-tag :type="getStatusType(row.status)" size="small">{{ row.status }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column prop="description" :label="t('pluginManager.colDescription')" min-width="200" show-overflow-tooltip />
    <el-table-column :label="t('pluginManager.colActions')" width="200" fixed="right">
      <template #default="{ row }">
        <el-button size="small" @click="emit('detail', row.id)">
          {{ t('pluginManager.btnDetail') }}
        </el-button>
        <el-button
          v-if="row.status === 'enabled'"
          size="small"
          type="warning"
          @click="emit('disable', row.id)"
        >
          {{ t('pluginManager.btnDisable') }}
        </el-button>
        <el-button
          v-else
          size="small"
          type="success"
          @click="emit('enable', row.id)"
        >
          {{ t('pluginManager.btnEnable') }}
        </el-button>
        <el-button size="small" type="danger" @click="emit('uninstall', row.id)">
          {{ t('pluginManager.btnUninstall') }}
        </el-button>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { Plugin } from '../../stores/plugin'

const { t } = useI18n()

defineProps<{
  plugins: Plugin[]
  showType?: boolean
}>()

const emit = defineEmits<{
  detail: [pluginId: string]
  enable: [pluginId: string]
  disable: [pluginId: string]
  uninstall: [pluginId: string]
}>()

const getStatusType = (status: string): "success" | "warning" | "danger" | "info" => {
  switch (status) {
    case 'enabled':
      return 'success'
    case 'disabled':
      return 'warning'
    case 'error':
      return 'danger'
    default:
      return 'info'
  }
}
</script>