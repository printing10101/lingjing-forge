<template>
  <div class="result-section">
    <el-alert
      :title="warnings.length > 0 ? $t('stepImport.importSuccessWithWarning') : $t('stepImport.importSuccess')"
      :type="warnings.length > 0 ? 'warning' : 'success'"
      :closable="false"
      show-icon
    />

    <!-- 模型概览 -->
    <div class="model-overview">
      <h4>{{ $t('stepImport.modelOverview') }}</h4>
      <el-descriptions
        :column="2"
        border
        size="small"
      >
        <el-descriptions-item :label="$t('stepImport.fileName')">
          {{ currentResult.file_name }}
        </el-descriptions-item>
        <el-descriptions-item :label="$t('stepImport.fileSize')">
          {{ formatFileSize(currentResult.file_size) }}
        </el-descriptions-item>
        <el-descriptions-item :label="$t('stepImport.parseTime')">
          {{ currentResult.parse_time_ms.toFixed(0) }} ms
        </el-descriptions-item>
        <el-descriptions-item :label="$t('stepImport.conversionTime')">
          {{ currentResult.conversion_time_ms.toFixed(0) }} ms
        </el-descriptions-item>
        <el-descriptions-item :label="$t('stepImport.entityCount')">
          {{ modelInfo?.entity_count ?? 0 }}
        </el-descriptions-item>
        <el-descriptions-item :label="$t('stepImport.faceCount')">
          {{ (modelInfo?.face_count ?? 0).toLocaleString() }}
        </el-descriptions-item>
        <el-descriptions-item :label="$t('stepImport.vertexCount')">
          {{ (modelInfo?.vertex_count ?? 0).toLocaleString() }}
        </el-descriptions-item>
        <el-descriptions-item :label="$t('stepImport.assembly')">
          {{ currentResult.is_assembly ? $t('common.yes') : $t('common.no') }}
        </el-descriptions-item>
      </el-descriptions>
    </div>

    <!-- 包围盒尺寸 -->
    <div
      v-if="modelInfo?.bounding_box"
      class="model-dimensions"
    >
      <h4>{{ $t('stepImport.boundingBox') }}</h4>
      <div class="dimension-cards">
        <div class="dim-card">
          <span class="dim-label">{{ $t('stepImport.lengthX') }}</span>
          <span class="dim-value">{{ modelInfo.bounding_box.length.toFixed(2) }} mm</span>
        </div>
        <div class="dim-card">
          <span class="dim-label">{{ $t('stepImport.widthY') }}</span>
          <span class="dim-value">{{ modelInfo.bounding_box.width.toFixed(2) }} mm</span>
        </div>
        <div class="dim-card">
          <span class="dim-label">{{ $t('stepImport.heightZ') }}</span>
          <span class="dim-value">{{ modelInfo.bounding_box.height.toFixed(2) }} mm</span>
        </div>
      </div>
      <div
        v-if="modelInfo.volume > 0"
        class="dim-extra"
      >
        {{ $t('stepImport.volume') }}: {{ (modelInfo.volume / 1000).toFixed(2) }} {{ $t('stepImport.cubicCm') }} |
        {{ $t('stepImport.surfaceArea') }}: {{ (modelInfo.surface_area / 100).toFixed(2) }} {{ $t('stepImport.squareCm') }}
      </div>
    </div>

    <!-- 警告信息 -->
    <div
      v-if="warnings.length > 0"
      class="warning-section"
    >
      <el-alert
        v-for="(w, i) in warnings"
        :key="`warn-${i}`"
        :title="w"
        type="warning"
        :closable="false"
        show-icon
        style="margin-bottom: 4px;"
      />
    </div>

    <!-- 多实体选择 -->
    <div
      v-if="hasStlFiles && activeStlFiles.length > 1"
      class="entity-selector"
    >
      <h4>{{ $t('stepImport.entitySelection', { count: activeStlFiles.length }) }}</h4>
      <el-radio-group
        :model-value="entityIndex"
        size="small"
        @change="onEntityChange"
      >
        <el-radio-button
          v-for="(f, i) in activeStlFiles"
          :key="`entity-${i}`"
          :value="i"
        >
          {{ f.entity_name }}
        </el-radio-button>
      </el-radio-group>
    </div>

    <!-- 转换错误 -->
    <div
      v-if="errors.length > 0"
      class="error-detail"
    >
      <el-alert
        v-for="(e, i) in errors"
        :key="`err-${i}`"
        :title="e"
        type="error"
        :closable="false"
        show-icon
        style="margin-bottom: 4px;"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import type { StepImportResult, ModelInfo, StlFileInfo } from '@/types'
import { formatFileSize } from '@/utils/formatters'

defineProps<{
  currentResult: StepImportResult
  modelInfo: ModelInfo | null
  warnings: string[]
  hasStlFiles: boolean
  activeStlFiles: StlFileInfo[]
  entityIndex: number
  errors: string[]
}>()

const emit = defineEmits<{
  'update:entityIndex': [value: number]
}>()

function onEntityChange(index: string | number | boolean | undefined) {
  const idx = typeof index === 'number' ? index : parseInt(String(index), 10)
  if (!isNaN(idx)) {
    emit('update:entityIndex', idx)
  }
}
</script>

<style scoped>
.result-section { max-height: 50vh; overflow-y: auto; }
.model-overview { margin-top: 16px; }
.model-overview h4,
.model-dimensions h4,
.entity-selector h4 { margin: 12px 0 8px; font-size: 14px; font-weight: 600; color: var(--text-primary); }
.dimension-cards { display: flex; gap: 12px; }
.dim-card { flex: 1; padding: 12px; background: var(--bg-tertiary); border-radius: var(--radius-sm); text-align: center; }
.dim-label { display: block; font-size: 12px; color: var(--text-tertiary); margin-bottom: 4px; }
.dim-value { font-size: 16px; font-weight: 600; color: var(--text-primary); }
.dim-extra { margin-top: 8px; font-size: 12px; color: var(--text-secondary); text-align: center; }
.warning-section { margin-top: 12px; }
.entity-selector { margin-top: 16px; }
.error-detail { margin-top: 12px; }
</style>