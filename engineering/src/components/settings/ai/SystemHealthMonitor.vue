<template>
  <div class="content-card">
    <div class="content-card__header">
      <span class="content-card__title">
        <el-icon style="margin-right: 6px;"><Odometer /></el-icon>
        {{ $t('settings.systemHealth') }}
      </span>
      <div style="display: flex; align-items: center; gap: 8px;">
        <el-tag
          :type="healthStatus?.backendOnline ? 'success' : 'danger'"
          size="small"
        >
          {{ healthStatus?.backendOnline ? $t('common.online') : $t('common.offline') }}
        </el-tag>
        <el-button
          size="small"
          :loading="healthLoading"
          circle
          @click="refreshHealth"
        >
          <el-icon><Refresh /></el-icon>
        </el-button>
      </div>
    </div>
    <div
      v-if="healthStatus"
      class="content-card__body"
    >
      <!-- 关键指标 -->
      <div class="health-stats">
        <div class="health-stat">
          <div class="health-stat__icon health-stat__icon--blue">
            <el-icon :size="18">
              <Timer />
            </el-icon>
          </div>
          <div class="health-stat__content">
            <span class="health-stat__label">{{ $t('settings.uptime') }}</span>
            <span class="health-stat__value">{{ healthStatus.uptimeStr }}</span>
          </div>
        </div>
        <div class="health-stat">
          <div class="health-stat__icon health-stat__icon--green">
            <el-icon :size="18">
              <DataLine />
            </el-icon>
          </div>
          <div class="health-stat__content">
            <span class="health-stat__label">{{ $t('settings.totalRequests') }}</span>
            <span class="health-stat__value">{{ healthStatus.totalRequests.toLocaleString() }}</span>
          </div>
        </div>
        <div class="health-stat">
          <div class="health-stat__icon health-stat__icon--orange">
            <el-icon :size="18">
              <Lightning />
            </el-icon>
          </div>
          <div class="health-stat__content">
            <span class="health-stat__label">{{ $t('settings.avgResponse') }}</span>
            <span class="health-stat__value">{{ healthStatus.avgResponseMs }}ms</span>
          </div>
        </div>
        <div class="health-stat">
          <div class="health-stat__icon health-stat__icon--purple">
            <el-icon :size="18">
              <Box />
            </el-icon>
          </div>
          <div class="health-stat__content">
            <span class="health-stat__label">{{ $t('settings.activeModels') }}</span>
            <span class="health-stat__value">{{ healthStatus.activeModels }}</span>
          </div>
        </div>
      </div>

      <!-- 资源使用 -->
      <div class="resource-section">
        <div class="resource-bar">
          <div class="resource-bar__header">
            <span class="resource-bar__label">{{ $t('settings.memoryUsage') }}</span>
            <span class="resource-bar__value">{{ healthStatus.memoryUsedMb }} / {{ healthStatus.memoryTotalMb }} MB</span>
          </div>
          <el-progress
            :percentage="healthStatus.memoryPercent"
            :status="healthStatus.memoryPercent > 80 ? 'exception' : healthStatus.memoryPercent > 60 ? 'warning' : ''"
            :stroke-width="8"
            :show-text="true"
          />
        </div>
        <div class="resource-bar">
          <div class="resource-bar__header">
            <span class="resource-bar__label">{{ $t('settings.cpuUsage') }}</span>
            <span class="resource-bar__value">{{ healthStatus.cpuPercent }}%</span>
          </div>
          <el-progress
            :percentage="healthStatus.cpuPercent"
            :status="healthStatus.cpuPercent > 80 ? 'exception' : healthStatus.cpuPercent > 60 ? 'warning' : ''"
            :stroke-width="8"
            :show-text="true"
          />
        </div>
        <div class="resource-bar">
          <div class="resource-bar__header">
            <span class="resource-bar__label">{{ $t('settings.trainingTasks') }}</span>
            <el-tag
              :type="healthStatus.activeTrainingTasks > 0 ? 'warning' : 'info'"
              size="small"
            >
              {{ healthStatus.activeTrainingTasks }} {{ $t('settings.activeSuffix') }}
            </el-tag>
          </div>
        </div>
      </div>

      <!-- 推理趋势 -->
      <div class="trend-section">
        <div class="trend-section__header">
          <span class="trend-section__title">{{ $t('settings.lnnTrend') }}</span>
          <span class="trend-section__stats">P50: {{ healthStatus.p50Ms }}ms | P95: {{ healthStatus.p95Ms }}ms | Max: {{ healthStatus.maxRecentDuration }}ms</span>
        </div>
        <div class="trend-chart">
          <div
            v-for="(item, idx) in healthStatus.recentInferences"
            :key="`inf-${idx}`"
            class="trend-bar-wrapper"
          >
            <div
              class="trend-bar"
              :style="{
                height: Math.max(4, (item.duration_ms / Math.max(healthStatus.maxRecentDuration, 1)) * 48) + 'px',
                backgroundColor: item.duration_ms > 500 ? 'var(--state-error)' : item.duration_ms > 200 ? 'var(--state-warning)' : 'var(--state-success)'
              }"
              :title="`${item.model}: ${item.duration_ms}ms`"
            />
            <span class="trend-bar-label">{{ item.model ? item.model.substring(0, 6) : '-' }}</span>
          </div>
        </div>
      </div>

      <!-- 服务状态 -->
      <div class="services-bar">
        <span class="services-bar__label">{{ $t('settings.serviceStatus') }}</span>
        <div class="services-bar__tags">
          <el-tag
            :type="healthStatus.dbHealthy ? 'success' : 'danger'"
            size="small"
            effect="plain"
          >
            DB
          </el-tag>
          <el-tag
            :type="healthStatus.redisHealthy ? 'success' : 'danger'"
            size="small"
            effect="plain"
          >
            Redis
          </el-tag>
          <el-tag
            :type="healthStatus.prometheusHealthy ? 'success' : 'danger'"
            size="small"
            effect="plain"
          >
            Prometheus
          </el-tag>
        </div>
        <span class="services-bar__interval">{{ $t('settings.autoRefresh') }}: {{ healthStatus.pollInterval }}s</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Odometer, Timer, DataLine, Lightning, Box, Refresh } from '@element-plus/icons-vue'
import { useHealthMonitor } from '@/composables/useHealthMonitor'

const {
  healthStatus,
  healthLoading,
  refreshHealth,
} = useHealthMonitor()
</script>

<style scoped>
/* 健康监控指标网格 */
.health-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 24px;
}

.health-stat {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px;
  background: var(--bg-50);
  border-radius: var(--radius-md);
  border: 1px solid var(--bg-100);
}

.health-stat__icon {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  flex-shrink: 0;
}

.health-stat__icon--blue { background: var(--info-bg); color: var(--accent-primary); }
.health-stat__icon--green { background: var(--success-bg); color: var(--success); }
.health-stat__icon--orange { background: var(--warning-bg); color: var(--warning); }
.health-stat__icon--purple { background: var(--purple-bg); color: var(--purple); }

.health-stat__content {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.health-stat__label {
  font-size: 12px;
  color: var(--text-tertiary);
}

.health-stat__value {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
}

/* 资源使用进度条 */
.resource-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
  margin-bottom: 24px;
  padding: 16px;
  background: var(--bg-50);
  border-radius: var(--radius-md);
  border: 1px solid var(--bg-100);
}

.resource-bar__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}

.resource-bar__label {
  font-size: 13px;
  color: var(--text-secondary);
}

.resource-bar__value {
  font-size: 13px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

/* 推理趋势 */
.trend-section {
  padding: 16px;
  background: var(--bg-50);
  border-radius: var(--radius-md);
  border: 1px solid var(--bg-100);
  margin-bottom: 16px;
}

.trend-section__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.trend-section__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
}

.trend-section__stats {
  font-size: 11px;
  color: var(--text-tertiary);
  font-family: var(--font-mono);
}

.trend-chart {
  display: flex;
  align-items: flex-end;
  gap: 4px;
  height: 56px;
  padding: 4px 0;
}

.trend-bar-wrapper {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex: 1;
  min-width: 0;
}

.trend-bar {
  width: 100%;
  max-width: 28px;
  border-radius: var(--radius-2xs) var(--radius-2xs) 0 0;
  min-height: 4px;
  transition: height 0.3s ease;
}

.trend-bar-label {
  font-size: 9px;
  color: var(--text-tertiary);
  margin-top: 3px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

/* 服务状态栏 */
.services-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  background: var(--bg-50);
  border-radius: var(--radius-md);
  border: 1px solid var(--bg-100);
}

.services-bar__label {
  font-size: 13px;
  color: var(--text-secondary);
  font-weight: 500;
}

.services-bar__tags {
  display: flex;
  gap: 6px;
}

.services-bar__interval {
  margin-left: auto;
  font-size: 11px;
  color: var(--text-tertiary);
}
</style>