<template>
  <div class="content-panel">
    <div class="panel-header">
      <h3>{{ t("workflowGuide.step3Header") }}</h3>
      <p class="hint">
        {{ t("workflowGuide.step3Hint") }}
      </p>
    </div>
    <div class="panel-body">
      <div class="model-preview">
        <div v-if="!modelGenerated" class="preview-placeholder">
          <el-icon :size="64" class="loading-icon">
            <Loading />
          </el-icon>
          <p>{{ t("workflowGuide.step3Loading") }}</p>
        </div>
        <div v-else class="preview-container">
          <div class="preview-viewport">
            <slot name="3d-viewer" />
          </div>
          <div class="preview-info">
            <div class="info-item">
              <span class="label">{{ t("workflowGuide.infoShape") }}</span>
              <span class="value">{{ getShapeLabel(params.shape_type) }}</span>
            </div>
            <div class="info-item">
              <span class="label">{{ t("workflowGuide.infoDimensions") }}</span>
              <span class="value">{{
                formatDimensions(params.dimensions)
              }}</span>
            </div>
            <div v-if="params.material" class="info-item">
              <span class="label">{{ t("workflowGuide.infoMaterial") }}</span>
              <span class="value">{{ params.material }}</span>
            </div>
          </div>
        </div>
      </div>
      <div class="panel-actions">
        <el-button @click="$emit('prev')">
          <el-icon><ArrowLeft /></el-icon>
          {{ t("workflowGuide.btnModifyParams") }}
        </el-button>
        <el-button
          type="primary"
          :disabled="!modelGenerated"
          @click="$emit('next')"
        >
          <el-icon><ArrowRight /></el-icon>
          {{ t("workflowGuide.btnProcessPlanning") }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { ArrowLeft, ArrowRight, Loading } from "@element-plus/icons-vue";
import type { CADParams, CADDimensions } from "@/types/nl2cad";

defineProps<{
  modelGenerated: boolean;
  params: CADParams;
}>();

defineEmits<{
  (e: "prev"): void;
  (e: "next"): void;
}>();

const { t } = useI18n();

function getShapeLabel(shapeType: string): string {
  const labels: Record<string, string> = {
    box: t("workflowGuide.shapeBox"),
    cylinder: t("workflowGuide.shapeCylinder"),
    sphere: t("workflowGuide.shapeSphere"),
    cone: t("workflowGuide.shapeCone"),
  };
  return labels[shapeType] || shapeType;
}

function formatDimensions(dimensions: CADDimensions | undefined): string {
  if (!dimensions) return "-";
  const parts: string[] = [];
  if (dimensions.length)
    parts.push(`${t("workflowGuide.dimLength")}${dimensions.length}mm`);
  if (dimensions.width)
    parts.push(`${t("workflowGuide.dimWidth")}${dimensions.width}mm`);
  if (dimensions.height)
    parts.push(`${t("workflowGuide.dimHeight")}${dimensions.height}mm`);
  if (dimensions.radius)
    parts.push(`${t("workflowGuide.dimRadius")}${dimensions.radius}mm`);
  return parts.join(" × ") || "-";
}
</script>

<style scoped>
.content-panel {
  max-width: 900px;
  margin: 0 auto;
}
.panel-header {
  margin-bottom: 24px;
}
.panel-header h3 {
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
  margin: 0 0 8px;
}
.panel-header .hint {
  font-size: 14px;
  color: var(--text-tertiary);
  margin: 0;
}
.panel-body {
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-lg);
  padding: 24px;
}
.panel-actions {
  display: flex;
  justify-content: space-between;
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--border-light);
}
.model-preview {
  min-height: 400px;
}
.preview-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 400px;
  color: var(--text-tertiary);
}
.loading-icon {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
.preview-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.preview-viewport {
  height: 400px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.preview-info {
  display: flex;
  gap: 24px;
  padding: 16px;
  background: var(--bg-secondary);
  border-radius: var(--radius-md);
}
.info-item {
  display: flex;
  gap: 8px;
  font-size: 13px;
}
.info-item .label {
  color: var(--text-tertiary);
}
.info-item .value {
  color: var(--text-primary);
  font-weight: 500;
}
</style>
