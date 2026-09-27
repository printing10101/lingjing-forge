<template>
  <div class="content-panel">
    <div class="panel-header">
      <h3>{{ t("workflowGuide.step2Header") }}</h3>
      <p class="hint">
        {{ t("workflowGuide.step2Hint") }}
      </p>
    </div>
    <div class="panel-body">
      <div class="params-preview">
        <el-form :model="params" label-width="120px" class="params-form">
          <el-form-item :label="t('workflowGuide.paramShapeType')">
            <el-select
              :model-value="params.shape_type"
              :placeholder="t('workflowGuide.paramShapePlaceholder')"
              @update:model-value="$emit('update:shapeType', $event)"
            >
              <el-option :label="t('workflowGuide.shapeBox')" value="box" />
              <el-option
                :label="t('workflowGuide.shapeCylinder')"
                value="cylinder"
              />
              <el-option
                :label="t('workflowGuide.shapeSphere')"
                value="sphere"
              />
              <el-option :label="t('workflowGuide.shapeCone')" value="cone" />
            </el-select>
          </el-form-item>

          <template v-if="params.dimensions">
            <el-form-item
              v-if="params.dimensions.length !== undefined"
              :label="t('workflowGuide.paramLength')"
            >
              <el-input-number
                :model-value="params.dimensions.length"
                :min="0.1"
                :step="1"
                controls-position="right"
                @update:model-value="
                  $emit('update:dimension', 'length', $event!)
                "
              />
              <span class="unit">mm</span>
            </el-form-item>

            <el-form-item
              v-if="params.dimensions.width !== undefined"
              :label="t('workflowGuide.paramWidth')"
            >
              <el-input-number
                :model-value="params.dimensions.width"
                :min="0.1"
                :step="1"
                controls-position="right"
                @update:model-value="
                  $emit('update:dimension', 'width', $event!)
                "
              />
              <span class="unit">mm</span>
            </el-form-item>

            <el-form-item
              v-if="params.dimensions.height !== undefined"
              :label="t('workflowGuide.paramHeight')"
            >
              <el-input-number
                :model-value="params.dimensions.height"
                :min="0.1"
                :step="1"
                controls-position="right"
                @update:model-value="
                  $emit('update:dimension', 'height', $event!)
                "
              />
              <span class="unit">mm</span>
            </el-form-item>

            <el-form-item
              v-if="params.dimensions.radius !== undefined"
              :label="t('workflowGuide.paramRadius')"
            >
              <el-input-number
                :model-value="params.dimensions.radius"
                :min="0.1"
                :step="1"
                controls-position="right"
                @update:model-value="
                  $emit('update:dimension', 'radius', $event!)
                "
              />
              <span class="unit">mm</span>
            </el-form-item>
          </template>

          <el-form-item
            v-if="params.material"
            :label="t('workflowGuide.paramMaterial')"
          >
            <el-input
              :model-value="params.material"
              :placeholder="t('workflowGuide.paramMaterialInputPlaceholder')"
              @update:model-value="$emit('update:material', $event)"
            />
          </el-form-item>

          <el-form-item :label="t('workflowGuide.paramConfidence')">
            <el-progress
              :percentage="Math.round((params.confidence || 0.8) * 100)"
              :color="getConfidenceColor(params.confidence)"
            />
          </el-form-item>
        </el-form>
      </div>
      <div class="panel-actions">
        <el-button @click="$emit('prev')">
          <el-icon><ArrowLeft /></el-icon>
          {{ t("workflowGuide.btnPrev") }}
        </el-button>
        <el-button type="primary" @click="$emit('generate')">
          <el-icon><Box /></el-icon>
          {{ t("workflowGuide.btnGenerateModel") }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { ArrowLeft, Box } from "@element-plus/icons-vue";
import type { CADParams, ShapeType } from "@/types/nl2cad";

defineProps<{
  params: CADParams;
}>();

defineEmits<{
  (e: "prev"): void;
  (e: "generate"): void;
  (e: "update:shapeType", value: ShapeType): void;
  (e: "update:dimension", key: string, value: number): void;
  (e: "update:material", value: string): void;
}>();

const { t } = useI18n();

function getConfidenceColor(confidence: number | undefined): string {
  const c = confidence ?? 0.8;
  if (c >= 0.8) return "var(--success)";
  if (c >= 0.6) return "var(--warning)";
  return "var(--error)";
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
.params-form {
  max-width: 500px;
}
.params-form .el-form-item {
  margin-bottom: 20px;
}
.unit {
  margin-left: 8px;
  color: var(--text-tertiary);
  font-size: 13px;
}
.panel-actions {
  display: flex;
  justify-content: space-between;
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--border-light);
}
</style>
