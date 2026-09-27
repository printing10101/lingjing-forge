<template>
  <div class="content-card">
    <div class="content-card__header">
      <span class="content-card__title">{{ t('costDashboard.alertListTitle') }}</span>
      <div>
        <el-select
          :model-value="alertFilter"
          size="small"
          style="width:100px"
          @update:model-value="$emit('update:alertFilter', $event)"
        >
          <el-option
            :label="t('costDashboard.filterAll')"
            value=""
          />
          <el-option
            :label="t('costDashboard.filterWarning')"
            value="warning"
          />
          <el-option
            :label="t('costDashboard.filterExceeded')"
            value="exceeded"
          />
        </el-select>
        <el-button
          size="small"
          :disabled="!hasUnread"
          style="margin-left:4px"
          @click="$emit('mark-all-read')"
        >
          {{ t('costDashboard.btnMarkAllRead') }}
        </el-button>
        <el-button
          size="small"
          :loading="loading"
          circle
          :aria-label="t('costDashboard.refreshBudgetAlertsAriaLabel')"
          :title="t('costDashboard.refreshBudgetAlertsTitle')"
          style="margin-left:4px"
          @click="$emit('refresh')"
        >
          <el-icon :size="16">
            <Refresh />
          </el-icon>
        </el-button>
      </div>
    </div>

    <el-table
      v-loading="loading"
      :data="alerts"
      style="width: 100%"
      :empty-text="t('costDashboard.emptyAlerts')"
      stripe
    >
      <el-table-column
        :label="t('costDashboard.colUrgency')"
        width="80"
      >
        <template #default="{ row }">
          <el-tag
            :type="row.status === 'exceeded' ? 'danger' : 'warning'"
            size="small"
          >
            {{ row.status === 'exceeded' ? t('costDashboard.statusExceeded') : t('costDashboard.statusWarning') }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        prop="created_at"
        :label="t('costDashboard.colTime')"
        width="180"
      >
        <template #default="{ row }">
          {{ formatSecondsTimestamp(row.created_at) }}
        </template>
      </el-table-column>
      <el-table-column
        prop="level"
        :label="t('costDashboard.colLevel')"
        width="80"
      >
        <template #default="{ row }">
          <el-tag
            size="small"
            type="info"
          >
            {{ budgetLevelLabel(row.level) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        prop="scope_id"
        :label="t('costDashboard.colScope')"
        width="140"
      />
      <el-table-column
        prop="resource_type"
        :label="t('costDashboard.colResourceType')"
        width="120"
      />
      <el-table-column
        :label="t('costDashboard.colUsageRatio')"
        width="120"
      >
        <template #default="{ row }">
          <el-progress
            :percentage="Math.round((row.usage_ratio || 0) * 100)"
            :status="row.status === 'exceeded' ? 'exception' : 'warning'"
            :stroke-width="6"
          />
        </template>
      </el-table-column>
      <el-table-column
        prop="message"
        :label="t('costDashboard.colMessage')"
        min-width="300"
        show-overflow-tooltip
      />
      <el-table-column
        :label="t('costDashboard.colStatus')"
        width="80"
      >
        <template #default="{ row }">
          <el-tag
            size="small"
            :type="row.is_read ? 'info' : 'warning'"
          >
            {{ row.is_read ? t('costDashboard.statusRead') : t('costDashboard.statusUnread') }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        :label="t('costDashboard.colActions')"
        width="140"
      >
        <template #default="{ row }">
          <el-button
            size="small"
            :disabled="row.is_read"
            @click="$emit('mark-read', row.id)"
          >
            {{ t('costDashboard.btnMarkRead') }}
          </el-button>
          <el-button
            size="small"
            type="danger"
            text
            @click="$emit('delete', row.id)"
          >
            {{ t('costDashboard.btnDelete') }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script lang="ts" setup>
import { Refresh } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import { formatSecondsTimestamp } from '@/utils/formatters'

const { t } = useI18n()

defineProps({
  alerts: {
    type: Array as PropType<any[]>,
    required: true,
    default: () => [],
  },
  loading: {
    type: Boolean,
    default: false,
  },
  hasUnread: {
    type: Boolean,
    default: false,
  },
  alertFilter: {
    type: String,
    default: '',
  },
})

defineEmits<{
  'update:alertFilter': [value: string]
  'mark-read': [id: number]
  'mark-all-read': []
  'delete': [id: number]
  'refresh': []
}>()

function budgetLevelLabel(level: string): string {
  const map: Record<string, string> = {
    global: t('costDashboard.levelGlobal'),
    project: t('costDashboard.levelProject'),
    agent: t('costDashboard.levelAgent'),
    task: t('costDashboard.levelTask'),
  }
  return map[level] || level
}
</script>

<style scoped>
.content-card {
  margin-bottom: 16px;
  border-radius: 8px;
  background: var(--bg-card);
  padding: 16px;
}

.content-card__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.content-card__title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}
</style>