<template>
  <div class="content-card">
    <div class="content-card__header">
      <span class="content-card__title">
        <el-icon style="margin-right: 6px;"><Document /></el-icon>
        {{ $t('settings.auditLog') }}
      </span>
      <div style="display: flex; gap: 8px;">
        <el-button
          size="small"
          :loading="exporting"
          @click="$emit('export')"
        >
          <el-icon style="margin-right: 4px;">
            <Download />
          </el-icon>
          {{ $t('settings.exportLogs') }}
        </el-button>
        <el-button
          size="small"
          type="danger"
          :loading="clearing"
          @click="$emit('clear')"
        >
          <el-icon style="margin-right: 4px;">
            <Delete />
          </el-icon>
          {{ $t('settings.clearLogs') }}
        </el-button>
      </div>
    </div>
    <div class="content-card__body">
      <!-- 筛选栏 -->
      <div class="filter-bar">
        <el-select
          :model-value="logFilters.ai_module"
          :placeholder="$t('settings.allModules')"
          clearable
          size="small"
          style="width: 140px;"
          @update:model-value="onFilterChange('ai_module', $event)"
        >
          <el-option
            :label="$t('settings.lnnPredict')"
            value="lnn_predict"
          />
          <el-option
            :label="$t('settings.lnnTrain')"
            value="lnn_train"
          />
          <el-option
            :label="$t('settings.processOptimize')"
            value="process_optimize"
          />
          <el-option
            :label="$t('settings.toolWearAnalyze')"
            value="tool_wear_analyze"
          />
          <el-option
            :label="$t('settings.cadGenerate')"
            value="cad_generate"
          />
        </el-select>
        <el-select
          :model-value="logFilters.user_decision"
          :placeholder="$t('settings.allModules')"
          clearable
          size="small"
          style="width: 120px;"
          @update:model-value="onFilterChange('user_decision', $event)"
        >
          <el-option
            :label="$t('settings.accept')"
            value="accept"
          />
          <el-option
            :label="$t('settings.modify')"
            value="modify"
          />
          <el-option
            :label="$t('settings.reject')"
            value="reject"
          />
          <el-option
            :label="$t('settings.autoExecuted')"
            value="auto_executed"
          />
        </el-select>
        <el-date-picker
          :model-value="logFilters.dateRange"
          type="daterange"
          :range-separator="$t('settings.to')"
          :start-placeholder="$t('settings.startDate')"
          :end-placeholder="$t('settings.endDate')"
          size="small"
          @update:model-value="onDateRangeChange"
        />
        <el-input
          :model-value="logSearchKeyword"
          :placeholder="$t('settings.keyword')"
          clearable
          size="small"
          style="width: 180px;"
          @update:model-value="$emit('update:logSearchKeyword', $event)"
          @keyup.enter="$emit('search')"
        />
        <el-button
          type="primary"
          size="small"
          @click="$emit('search')"
        >
          {{ $t('common.search') }}
        </el-button>
      </div>

      <!-- 统计摘要 -->
      <div
        v-if="auditLogStatistics"
        class="audit-stats"
      >
        <div class="audit-stat-item">
          <span class="audit-stat-item__value">{{ auditLogStatistics.total_entries }}</span>
          <span class="audit-stat-item__label">{{ $t('settings.totalEntries') }}</span>
        </div>
        <div class="audit-stat-item">
          <span class="audit-stat-item__value">{{ ((auditLogStatistics.avg_confidence ?? 0) * 100).toFixed(1) }}%</span>
          <span class="audit-stat-item__label">{{ $t('settings.avgConfidence') }}</span>
        </div>
        <div class="audit-stat-item">
          <span class="audit-stat-item__value">{{ auditLogStatistics.recent_24h }}</span>
          <span class="audit-stat-item__label">{{ $t('settings.recent24h') }}</span>
        </div>
      </div>

      <!-- 表格 -->
      <el-table
        v-loading="loadingLogs"
        :data="auditLogs"
        style="width: 100%;"
        stripe
      >
        <el-table-column
          prop="timestamp_ms"
          :label="$t('common.time')"
          width="180"
        >
          <template #default="{ row }">
            {{ formatTimestamp(row.timestamp_ms) }}
          </template>
        </el-table-column>
        <el-table-column
          prop="ai_module"
          :label="$t('settings.aiModuleCol')"
          width="140"
        >
          <template #default="{ row }">
            <el-tag size="small">
              {{ getModuleName(row.ai_module) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="user_decision"
          :label="$t('settings.userDecisionCol')"
          width="100"
        >
          <template #default="{ row }">
            <el-tag
              :type="getDecisionType(row.user_decision as string)"
              size="small"
            >
              {{ getDecisionName(row.user_decision) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="operation_status"
          :label="$t('settings.opStatus')"
          width="100"
        >
          <template #default="{ row }">
            <el-tag
              :type="getStatusType(row.operation_status as string)"
              size="small"
            >
              {{ getStatusName(row.operation_status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="confidence"
          :label="$t('settings.confidence')"
          width="100"
        >
          <template #default="{ row }">
            <span v-if="row.confidence !== null">{{ (row.confidence * 100).toFixed(0) }}%</span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="reasoning"
          :label="$t('settings.reasoningDesc')"
          min-width="200"
        >
          <template #default="{ row }">
            <el-tooltip
              :content="row.reasoning"
              placement="top"
            >
              <span class="reasoning-text">{{ row.reasoning }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column
          :label="$t('common.operation')"
          width="80"
        >
          <template #default="{ row }">
            <el-button
              type="primary"
              text
              size="small"
              @click="$emit('viewDetail', row)"
            >
              {{ $t('common.detail') }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        :current-page="logPagination.page"
        :page-size="logPagination.pageSize"
        :total="logPagination.total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next"
        style="margin-top: 16px; justify-content: flex-end;"
        @update:current-page="onPageChange"
        @update:page-size="onPageSizeChange"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { Download, Delete, Document } from '@element-plus/icons-vue'
import { formatTimestamp } from '@/utils/formatters'
import {
  getAuditModuleName as getModuleName,
  getAuditDecisionLabel as getDecisionName,
  getAuditDecisionTagType as getDecisionType,
  getGenericStatusLabel as getStatusName,
  getGenericStatusTagType as getStatusType,
} from '@/utils/statusHelpers'
import type { AuditLogEntry, AuditLogStatistics, AuditLogFilters, AuditLogPagination } from '@/composables/useAuditLog'

const props = defineProps<{
  auditLogs: AuditLogEntry[]
  auditLogStatistics: AuditLogStatistics | null
  loadingLogs: boolean
  exporting: boolean
  clearing: boolean
  logSearchKeyword: string
  logFilters: AuditLogFilters
  logPagination: AuditLogPagination
}>()

const emit = defineEmits<{
  'update:logSearchKeyword': [value: string]
  'update:logFilters': [value: AuditLogFilters]
  'update:page': [value: number]
  'update:pageSize': [value: number]
  load: []
  search: []
  export: []
  clear: []
  viewDetail: [row: AuditLogEntry]
}>()

function onFilterChange(field: keyof AuditLogFilters, value: unknown) {
  const newFilters = { ...props.logFilters, [field]: value }
  emit('update:logFilters', newFilters)
  emit('load')
}

function onDateRangeChange(value: [Date, Date] | null) {
  const newFilters = { ...props.logFilters, dateRange: value }
  emit('update:logFilters', newFilters)
  emit('load')
}

function onPageChange(page: number) {
  emit('update:page', page)
  emit('load')
}

function onPageSizeChange(size: number) {
  emit('update:pageSize', size)
  emit('load')
}

</script>

<style scoped>
.audit-stats {
  display: flex;
  gap: 24px;
  padding: 14px 16px;
  background: var(--bg-50);
  border-radius: var(--radius-md);
  border: 1px solid var(--bg-100);
  margin-bottom: 16px;
}

.audit-stat-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.audit-stat-item__value {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
}

.audit-stat-item__label {
  font-size: 12px;
  color: var(--text-tertiary);
}

.reasoning-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 300px;
  display: inline-block;
}
</style>