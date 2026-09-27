<template>
  <el-row
    :gutter="16"
    class="stats-cards"
  >
    <el-col
      :xs="12"
      :sm="8"
      :md="4"
    >
      <el-card
        shadow="hover"
        class="stat-card"
      >
        <div class="stat-value">
          {{ stats?.total_rules || 0 }}
        </div>
        <div class="stat-label">
          {{ $t('ruleEditor.totalRules') }}
        </div>
      </el-card>
    </el-col>
    <el-col
      :xs="12"
      :sm="8"
      :md="4"
    >
      <el-card
        shadow="hover"
        class="stat-card active"
      >
        <div class="stat-value">
          {{ stats?.active_rules || 0 }}
        </div>
        <div class="stat-label">
          {{ $t('ruleEditor.activeRules') }}
        </div>
      </el-card>
    </el-col>
    <el-col
      :xs="12"
      :sm="8"
      :md="4"
    >
      <el-card
        shadow="hover"
        class="stat-card draft"
      >
        <div class="stat-value">
          {{ stats?.draft_rules || 0 }}
        </div>
        <div class="stat-label">
          {{ $t('ruleEditor.draftRules') }}
        </div>
      </el-card>
    </el-col>
    <el-col
      :xs="12"
      :sm="8"
      :md="4"
    >
      <el-card
        shadow="hover"
        class="stat-card groups"
      >
        <div class="stat-value">
          {{ stats?.total_groups || 0 }}
        </div>
        <div class="stat-label">
          {{ $t('ruleEditor.ruleGroups') }}
        </div>
      </el-card>
    </el-col>
  </el-row>
</template>

<script setup lang="ts">
import type { RuleStats } from '@/types'

defineProps<{
  stats: RuleStats | null
}>()
</script>

<style scoped>
.stats-cards {
  margin-bottom: 16px;
}

.stat-card {
  text-align: center;
  padding: 8px 0;
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  transition: all var(--transition-fast);
}

.stat-card:hover {
  box-shadow: var(--shadow-md);
}

.stat-value {
  font-size: 28px;
  font-weight: 600;
  color: var(--accent-primary);
}

.stat-card.active .stat-value {
  color: var(--success);
}

.stat-card.draft .stat-value {
  color: var(--warning);
}

.stat-card.groups .stat-value {
  color: var(--text-tertiary);
}

.stat-label {
  font-size: 13px;
  color: var(--text-secondary);
  margin-top: 4px;
}
</style>