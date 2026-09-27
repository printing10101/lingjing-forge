<template>
  <div class="stats-row">
    <div class="stat-card stat-card--info">
      <div class="stat-card__icon">
        <el-icon :size="24">
          <Monitor />
        </el-icon>
      </div>
      <div class="stat-card__content">
        <span class="stat-card__value">{{ totalCount }}</span>
        <span class="stat-card__label">{{ t('simulationPage.statTotalSim') }}</span>
      </div>
    </div>
    <div class="stat-card stat-card--success">
      <div class="stat-card__icon">
        <el-icon :size="24">
          <CircleCheck />
        </el-icon>
      </div>
      <div class="stat-card__content">
        <span class="stat-card__value">{{ passCount }}</span>
        <span class="stat-card__label">{{ t('simulationPage.statPassCount') }}</span>
      </div>
    </div>
    <div class="stat-card stat-card--danger">
      <div class="stat-card__icon">
        <el-icon :size="24">
          <Warning />
        </el-icon>
      </div>
      <div class="stat-card__content">
        <span class="stat-card__value">{{ failCount }}</span>
        <span class="stat-card__label">{{ t('simulationPage.statCollisionCount') }}</span>
      </div>
    </div>
    <div class="stat-card stat-card--accent">
      <div class="stat-card__icon">
        <el-icon :size="24">
          <Timer />
        </el-icon>
      </div>
      <div class="stat-card__content">
        <span class="stat-card__value">{{ avgDuration }}</span>
        <span class="stat-card__label">{{ t('simulationPage.statAvgDuration') }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Monitor, CircleCheck, Warning, Timer } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

interface Props {
  totalCount: number
  passCount: number
  failCount: number
  avgDuration: string
}

defineProps<Props>()
</script>

<style scoped>
.stats-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 16px 20px;
  background: var(--bg-100);
  border-radius: var(--radius-lg);
  border: 1px solid var(--bg-200);
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.stat-card--info .stat-card__icon {
  color: var(--accent-primary);
}

.stat-card--success .stat-card__icon {
  color: var(--state-success);
}

.stat-card--danger .stat-card__icon {
  color: var(--state-error);
}

.stat-card--accent .stat-card__icon {
  color: var(--accent-secondary);
}

.stat-card__content {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-card__value {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}

.stat-card__label {
  font-size: 12px;
  color: var(--text-tertiary);
}
</style>