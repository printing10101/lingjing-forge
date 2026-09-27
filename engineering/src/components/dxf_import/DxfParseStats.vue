<template>
  <div class="stats-section">
    <h4>{{ $t('dxfImportDialog.statistics') }}</h4>
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-label">
          {{ $t('dxfImportDialog.linesCount') }}
        </div>
        <div class="stat-value">
          {{ parseResult.lines_count.toLocaleString() }}
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-label">
          {{ $t('dxfImportDialog.arcsCount') }}
        </div>
        <div class="stat-value">
          {{ parseResult.arcs_count.toLocaleString() }}
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-label">
          {{ $t('dxfImportDialog.circlesCount') }}
        </div>
        <div class="stat-value">
          {{ parseResult.circles_count.toLocaleString() }}
        </div>
      </div>
      <div class="stat-card highlight">
        <div class="stat-label">
          {{ $t('dxfImportDialog.featuresCount') }}
        </div>
        <div class="stat-value">
          {{ featuresCount.toLocaleString() }}
        </div>
      </div>
    </div>

    <el-descriptions
      :column="2"
      border
      size="small"
      class="meta-descriptions"
    >
      <el-descriptions-item :label="$t('dxfImportDialog.fileName')">
        {{ parseResult.file_name }}
      </el-descriptions-item>
      <el-descriptions-item :label="$t('dxfImportDialog.fileSize')">
        {{ formatFileSize(parseResult.file_size) }}
      </el-descriptions-item>
      <el-descriptions-item :label="$t('dxfImportDialog.dxfVersion')">
        {{ parseResult.dxf_version || '-' }}
      </el-descriptions-item>
      <el-descriptions-item :label="$t('dxfImportDialog.parseTime')">
        {{ parseResult.parse_time_ms.toFixed(0) }} ms
      </el-descriptions-item>
      <el-descriptions-item
        :label="$t('dxfImportDialog.totalEntities')"
        :span="2"
      >
        {{ parseResult.total_entities.toLocaleString() }}
      </el-descriptions-item>
    </el-descriptions>
  </div>
</template>

<script setup lang="ts">
import { formatFileSize } from '@/utils/formatters'
import type { DxfParseResponse } from '@/types'

defineProps<{
  parseResult: DxfParseResponse
  featuresCount: number
}>()
</script>

<style scoped>
.stats-section h4 {
  margin: 8px 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-bottom: 12px;
}

.stat-card {
  padding: 12px;
  background: var(--bg-tertiary);
  border-radius: var(--radius-sm);
  text-align: center;
  transition: transform 0.15s ease;
}

.stat-card:hover {
  transform: translateY(-2px);
}

.stat-card.highlight {
  background: var(--gradient-purple);
  color: var(--text-white);
}

.stat-label {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-bottom: 4px;
}

.stat-card.highlight .stat-label {
  color: var(--text-white-90);
}

.stat-value {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
}

.stat-card.highlight .stat-value {
  color: white;
}

.meta-descriptions {
  margin-top: 8px;
}
</style>