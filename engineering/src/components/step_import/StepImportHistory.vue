<template>
  <div class="history-container">
    <div
      v-if="historyLoading"
      style="text-align:center;padding:20px;"
    >
      <el-icon class="is-loading">
        <Loading />
      </el-icon> {{ $t('common.loading') }}
    </div>
    <el-table
      v-else-if="importHistory.length > 0"
      :data="importHistory"
      height="350"
      stripe
      size="small"
    >
      <el-table-column
        prop="original_name"
        :label="$t('stepImport.historyFile')"
        min-width="180"
        show-overflow-tooltip
      />
      <el-table-column
        :label="$t('stepImport.historySize')"
        width="90"
      >
        <template #default="{ row }">
          {{ formatFileSize(row.file_size) }}
        </template>
      </el-table-column>
      <el-table-column
        :label="$t('stepImport.historyTime')"
        width="170"
      >
        <template #default="{ row }">
          {{ formatSecondsTimestamp(row.created_at) }}
        </template>
      </el-table-column>
      <el-table-column
        :label="$t('stepImport.historyActions')"
        width="160"
      >
        <template #default="{ row }">
          <el-button
            size="small"
            text
            type="primary"
            @click="$emit('view', row as ImportHistoryEntry)"
          >
            {{ $t('stepImport.historyView') }}
          </el-button>
          <el-button
            size="small"
            text
            type="danger"
            @click="$emit('delete', row as ImportHistoryEntry)"
          >
            {{ $t('stepImport.historyDelete') }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty
      v-else
      :description="$t('stepImport.noHistory')"
      :image-size="80"
    />
  </div>
</template>

<script setup lang="ts">
import { Loading } from '@element-plus/icons-vue'
import type { ImportHistoryEntry } from '@/types'
import { formatFileSize, formatSecondsTimestamp } from '@/utils/formatters'

defineProps<{
  historyLoading: boolean
  importHistory: ImportHistoryEntry[]
}>()

defineEmits<{
  'view': [row: ImportHistoryEntry]
  'delete': [row: ImportHistoryEntry]
}>()
</script>

<style scoped>
.history-container { min-height: 200px; }
</style>