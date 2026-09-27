<template>
  <div v-loading="deploymentsLoading" class="tab-content">
    <div class="filter-bar">
      <el-input
        v-model="filterModelName"
        size="small"
        :placeholder="t('flywheel.filterModelName')"
        clearable
        style="width: 240px"
        @change="handleFilterDeployments"
      />
      <el-select
        v-model="filterStatus"
        size="small"
        :placeholder="t('flywheel.filterStatus')"
        clearable
        style="width: 160px"
        @change="handleFilterDeployments"
      >
        <el-option value="deploying" :label="t('flywheel.statusDeploying')" />
        <el-option value="observing" :label="t('flywheel.statusObserving')" />
        <el-option value="promoted" :label="t('flywheel.statusPromoted')" />
        <el-option value="rolled_back" :label="t('flywheel.statusRolledBack')" />
        <el-option value="failed" :label="t('flywheel.statusFailed')" />
      </el-select>
      <el-button
        size="small"
        type="primary"
        @click="handleFilterDeployments"
      >
        {{ t('flywheel.btnSearch') }}
      </el-button>
      <el-button
        size="small"
        @click="handleResetDeploymentFilters"
      >
        {{ t('flywheel.btnReset') }}
      </el-button>
    </div>

    <el-card class="section-card" shadow="never">
      <template #header>
        <span class="section-title">
          {{ t('flywheel.activeDeploymentsTitle') }}
          <el-tag type="warning" size="small" class="count-tag">
            {{ activeDeployments.length }}
          </el-tag>
        </span>
      </template>
      <el-empty
        v-if="activeDeployments.length === 0"
        :description="t('flywheel.emptyNoActiveDeployments')"
        :image-size="60"
      />
      <el-table
        v-else
        :data="activeDeployments"
        size="small"
        stripe
      >
        <el-table-column
          prop="deployment_id"
          :label="t('flywheel.colDeploymentId')"
          width="180"
        />
        <el-table-column
          prop="model_name"
          :label="t('flywheel.colModelName')"
          width="160"
        />
        <el-table-column
          prop="new_model_uri"
          :label="t('flywheel.colNewModelUri')"
          show-overflow-tooltip
        />
        <el-table-column
          prop="status"
          :label="t('flywheel.colStatus')"
          width="120"
        >
          <template #default="{ row }">
            <el-tag :type="deploymentStatusTagType(row.status)">
              {{ deploymentStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="canary_ratio"
          :label="t('flywheel.colCanaryRatio')"
          width="110"
        >
          <template #default="{ row }">
            {{ store.formatPercent((row.canary_ratio ?? 0) * 100, 0) }}
          </template>
        </el-table-column>
        <el-table-column
          prop="created_at"
          :label="t('flywheel.colCreatedAt')"
          width="180"
        >
          <template #default="{ row }">
            {{ store.formatTime(row.created_at) }}
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card class="section-card" shadow="never">
      <template #header>
        <span class="section-title">
          {{ t('flywheel.allDeploymentsTitle') }}
          <el-tag size="small" class="count-tag">
            {{ deployments.length }}
          </el-tag>
        </span>
      </template>
      <el-empty
        v-if="deployments.length === 0"
        :description="t('flywheel.emptyNoDeployments')"
        :image-size="60"
      />
      <el-table
        v-else
        :data="deployments"
        size="small"
        stripe
      >
        <el-table-column
          prop="deployment_id"
          :label="t('flywheel.colDeploymentId')"
          width="180"
        />
        <el-table-column
          prop="model_name"
          :label="t('flywheel.colModelName')"
          width="160"
        />
        <el-table-column
          prop="new_model_uri"
          :label="t('flywheel.colNewModelUri')"
          show-overflow-tooltip
        />
        <el-table-column
          prop="status"
          :label="t('flywheel.colStatus')"
          width="120"
        >
          <template #default="{ row }">
            <el-tag :type="deploymentStatusTagType(row.status)">
              {{ deploymentStatusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="decision"
          :label="t('flywheel.colDecision')"
          width="120"
        >
          <template #default="{ row }">
            {{ row.decision || '-' }}
          </template>
        </el-table-column>
        <el-table-column
          prop="created_at"
          :label="t('flywheel.colCreatedAt')"
          width="180"
        >
          <template #default="{ row }">
            {{ store.formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column
          prop="updated_at"
          :label="t('flywheel.colUpdatedAt')"
          width="180"
        >
          <template #default="{ row }">
            {{ store.formatTime(row.updated_at) }}
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useFlywheelStore } from '@/stores/flywheel'
import type { DeploymentRecord, DeploymentStatus } from '@/stores/flywheel'

const { t } = useI18n()
const store = useFlywheelStore()

defineProps<{
  deploymentsLoading: boolean
  activeDeployments: DeploymentRecord[]
  deployments: DeploymentRecord[]
}>()

const emit = defineEmits<{
  search: [modelName: string | undefined, status: DeploymentStatus | undefined]
  reset: []
}>()

const filterModelName = ref<string>('')
const filterStatus = ref<DeploymentStatus | ''>('')

function deploymentStatusTagType(
  status: DeploymentStatus,
): 'success' | 'warning' | 'danger' | 'info' {
  const map: Record<DeploymentStatus, 'success' | 'warning' | 'danger' | 'info'> = {
    deploying: 'info',
    observing: 'warning',
    promoted: 'success',
    rolled_back: 'danger',
    failed: 'danger',
  }
  return map[status] ?? 'info'
}

function deploymentStatusLabel(status: DeploymentStatus): string {
  const map: Record<DeploymentStatus, string> = {
    deploying: t('flywheel.statusDeploying'),
    observing: t('flywheel.statusObserving'),
    promoted: t('flywheel.statusPromoted'),
    rolled_back: t('flywheel.statusRolledBack'),
    failed: t('flywheel.statusFailed'),
  }
  return map[status] ?? status
}

function handleFilterDeployments(): void {
  emit('search', filterModelName.value || undefined, (filterStatus.value || undefined) as DeploymentStatus | undefined)
}

function handleResetDeploymentFilters(): void {
  filterModelName.value = ''
  filterStatus.value = ''
  emit('reset')
}
</script>

<style scoped>
.tab-content {
  padding: 8px 4px;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.section-card {
  margin-bottom: 16px;
}

.section-card :deep(.el-card__header) {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.count-tag {
  margin-left: 8px;
}
</style>