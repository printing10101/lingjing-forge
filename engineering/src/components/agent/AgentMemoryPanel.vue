<template>
  <el-card shadow="hover" class="memory-card">
    <template #header>
      <div class="card-header-flex">
        <span>{{ t('agentDetail.sectionAgentMemory', { count: memory.length || 0 }) }}</span>
        <el-button size="small" @click="$emit('update:viewMode', viewMode === 'list' ? 'chart' : 'list')">
          {{ viewMode === 'list' ? t('agentDetail.btnVisualize') : t('agentDetail.btnList') }}
        </el-button>
      </div>
    </template>
    <template v-if="viewMode === 'list'">
      <el-table
        v-if="memory.length > 0"
        :data="sortedMemory"
        stripe
        max-height="300"
      >
        <el-table-column prop="memory_type" :label="t('agentDetail.colType')" width="100">
          <template #default="{ row }">
            <el-tag size="small">{{ row.memory_type }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="content" :label="t('agentDetail.colContent')" min-width="200">
          <template #default="{ row }">
            <el-text truncated>{{ row.content }}</el-text>
          </template>
        </el-table-column>
        <el-table-column prop="importance" :label="t('agentDetail.colImportance')" width="120" sortable>
          <template #default="{ row }">
            <el-progress
              :percentage="Math.round(row.importance * 100)"
              :stroke-width="8"
              :color="importanceColor(row.importance)"
            />
          </template>
        </el-table-column>
        <el-table-column prop="tags" :label="t('agentDetail.colTags')" width="160">
          <template #default="{ row }">
            <el-tag v-for="tag in (row.tags || [])" :key="tag" size="small" style="margin: 1px">
              {{ tag }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('agentDetail.colAccessCount')" width="80" sortable prop="access_count" />
      </el-table>
      <el-empty v-else :description="t('agentDetail.emptyNoMemory')" :image-size="50" />
    </template>
    <template v-else>
      <div class="memory-chart-container">
        <div v-for="entry in sortedMemory.slice(0, 20)" :key="entry.memory_id" class="memory-bar-item">
          <div class="memory-bar-label">
            <el-tag
              size="small"
              :type="entry.memory_type === 'observation' ? 'info' : entry.memory_type === 'decision' ? 'warning' : 'success'"
            >
              {{ entry.memory_type }}
            </el-tag>
            <el-text truncated class="memory-bar-text">{{ entry.content.substring(0, 60) }}</el-text>
          </div>
          <div class="memory-bar-track">
            <div
              class="memory-bar-fill"
              :style="{
                width: Math.round(entry.importance * 100) + '%',
                backgroundColor: importanceColor(entry.importance)
              }"
            />
          </div>
          <span class="memory-bar-value">{{ Math.round(entry.importance * 100) }}%</span>
        </div>
        <el-empty v-if="sortedMemory.length === 0" :description="t('agentDetail.emptyNoMemory')" :image-size="50" />
      </div>
    </template>
  </el-card>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import type { MemoryEntryInfo } from '@/stores/agents'

const { t } = useI18n()

const props = defineProps<{
  memory: MemoryEntryInfo[]
  viewMode: 'list' | 'chart'
}>()

defineEmits<{
  (e: 'update:viewMode', value: 'list' | 'chart'): void
}>()

const sortedMemory = computed(() => {
  const mem = [...props.memory]
  return mem.sort((a, b) => b.importance - a.importance)
})

function importanceColor(imp: number): string {
  if (imp >= 0.8) return 'var(--error)'
  if (imp >= 0.5) return 'var(--warning)'
  if (imp >= 0.3) return 'var(--accent-primary)'
  return 'var(--text-tertiary)'
}
</script>

<style scoped>
.memory-card {
  margin-bottom: 16px;
}

.card-header-flex {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.memory-chart-container {
  max-height: 400px;
  overflow-y: auto;
}

.memory-bar-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
}

.memory-bar-label {
  width: 260px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 4px;
}

.memory-bar-text {
  font-size: 0.82rem;
}

.memory-bar-track {
  flex: 1;
  height: 18px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.memory-bar-fill {
  height: 100%;
  border-radius: var(--radius-md);
  transition: width 0.3s;
}

.memory-bar-value {
  width: 40px;
  font-size: 0.75rem;
  color: var(--text-tertiary);
  text-align: right;
}
</style>