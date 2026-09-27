<template>
  <div class="process-settings">
    <!-- 日志管理 -->
    <LogManagement
      :log-settings="store.settings.logSettings"
      :exporting-logs="exportingLogs!"
      :export-progress="exportProgress!"
      :export-result="exportResult!"
      @update:log-settings="store.settings.logSettings = $event"
      @export="exportSystemLogs(store.settings.logSettings.exportDays)"
      @save="store.saveSettings()"
      @close-export-result="exportResult = null"
    />

    <!-- 审计日志 -->
    <AuditLogTable
      :audit-logs="auditLogs"
      :audit-log-statistics="auditLogStatistics"
      :loading-logs="loadingLogs"
      :exporting="exporting"
      :clearing="clearing"
      :log-search-keyword="logSearchKeyword"
      :log-filters="logFilters"
      :log-pagination="logPagination"
      @update:log-search-keyword="logSearchKeyword = $event"
      @update:log-filters="Object.assign(logFilters, $event)"
      @update:page="logPagination.page = $event"
      @update:page-size="logPagination.pageSize = $event"
      @load="loadAuditLogs"
      @search="searchLogs"
      @export="exportLogs"
      @clear="clearLogs"
      @view-detail="viewLogDetail"
    />

    <!-- 日志详情弹窗 -->
    <LogDetailDialog
      :visible="logDetailVisible"
      :log="selectedLog"
      @update:visible="logDetailVisible = $event"
    />
  </div>
</template>

<script setup lang="ts">
import { useSettingsStore } from '@/stores/settings'
import { useAuditLog } from '@/composables/useAuditLog'
import { useSettings } from '@/composables/useSettings'
import LogManagement from './process/LogManagement.vue'
import AuditLogTable from './process/AuditLogTable.vue'
import LogDetailDialog from './process/LogDetailDialog.vue'

const store = useSettingsStore()

const {
  auditLogs,
  auditLogStatistics,
  loadingLogs,
  exporting,
  clearing,
  logSearchKeyword,
  logDetailVisible,
  selectedLog,
  logFilters,
  logPagination,
  loadAuditLogs,
  searchLogs,
  exportLogs,
  clearLogs,
  viewLogDetail,
} = useAuditLog()

const {
  exportingLogs,
  exportProgress,
  exportResult,
  exportSystemLogs,
} = useSettings()
</script>

<style scoped>
/* 样式已迁移至子组件中 */
</style>