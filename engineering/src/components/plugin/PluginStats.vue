<template>
  <el-row :gutter="16" class="stats-row">
    <el-col :span="6">
      <el-card class="stat-card">
        <div class="stat-value">{{ total }}</div>
        <div class="stat-label">{{ t('pluginManager.statTotal') }}</div>
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card class="stat-card enabled">
        <div class="stat-value">{{ enabled }}</div>
        <div class="stat-label">{{ t('pluginManager.statEnabled') }}</div>
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card class="stat-card disabled">
        <div class="stat-value">{{ disabled }}</div>
        <div class="stat-label">{{ t('pluginManager.statDisabled') }}</div>
      </el-card>
    </el-col>
    <el-col :span="6">
      <el-card class="stat-card error">
        <div class="stat-value">{{ error }}</div>
        <div class="stat-label">{{ t('pluginManager.statError') }}</div>
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

defineProps<{
  total: number
  enabled: number
  disabled: number
  error: number
}>()
</script>

<style scoped>
.stats-row {
  margin-bottom: 20px;
}
.stat-card {
  text-align: center;
}
.stat-value {
  font-size: 32px;
  font-weight: bold;
  color: var(--accent-primary);
}
.stat-label {
  color: var(--text-tertiary);
  margin-top: 5px;
}
.stat-card.enabled .stat-value {
  color: var(--success);
}
.stat-card.disabled .stat-value {
  color: var(--warning);
}
.stat-card.error .stat-value {
  color: var(--error);
}
</style>