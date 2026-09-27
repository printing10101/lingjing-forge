<template>
  <div class="content-panel">
    <div class="panel-header">
      <h3>{{ t("workflowGuide.step4Header") }}</h3>
      <p class="hint">
        {{ t("workflowGuide.step4Hint") }}
      </p>
    </div>
    <div class="panel-body">
      <el-form :model="config" label-width="120px" class="process-form">
        <el-form-item :label="t('workflowGuide.paramMaterialType')">
          <el-select
            :model-value="config.material"
            :placeholder="t('workflowGuide.paramMaterialSelectPlaceholder')"
            @update:model-value="$emit('update:material', $event)"
          >
            <el-option
              :label="t('workflowGuide.materialAluminum6061')"
              value="aluminum_6061"
            />
            <el-option
              :label="t('workflowGuide.materialSteel45')"
              value="steel_45"
            />
            <el-option
              :label="t('workflowGuide.materialStainless304')"
              value="stainless_304"
            />
            <el-option
              :label="t('workflowGuide.materialCopper')"
              value="copper"
            />
            <el-option
              :label="t('workflowGuide.materialBrass')"
              value="brass"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('workflowGuide.paramMachineType')">
          <el-select
            :model-value="config.machine_type"
            :placeholder="t('workflowGuide.paramMachineSelectPlaceholder')"
            @update:model-value="$emit('update:machineType', $event)"
          >
            <el-option
              :label="t('workflowGuide.machineCncMill')"
              value="cnc_mill"
            />
            <el-option
              :label="t('workflowGuide.machineCncLathe')"
              value="cnc_lathe"
            />
            <el-option
              :label="t('workflowGuide.machineMachiningCenter')"
              value="machining_center"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('workflowGuide.paramPrecision')">
          <el-select
            :model-value="config.precision"
            :placeholder="t('workflowGuide.paramPrecisionSelectPlaceholder')"
            @update:model-value="$emit('update:precision', $event)"
          >
            <el-option
              :label="t('workflowGuide.precisionRough')"
              value="rough"
            />
            <el-option
              :label="t('workflowGuide.precisionSemiFinish')"
              value="semi-finish"
            />
            <el-option
              :label="t('workflowGuide.precisionFinish')"
              value="finish"
            />
            <el-option
              :label="t('workflowGuide.precisionSuperFinish')"
              value="super-finish"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <div class="panel-actions">
        <el-button @click="$emit('prev')">
          <el-icon><ArrowLeft /></el-icon>
          {{ t("workflowGuide.btnPrev") }}
        </el-button>
        <el-button type="primary" @click="$emit('generate')">
          <el-icon><SetUp /></el-icon>
          {{ t("workflowGuide.btnGenerateProcess") }}
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import { ArrowLeft, SetUp } from "@element-plus/icons-vue";
import type { ProcessConfig } from "@/types/nl2cad";

defineProps<{
  config: ProcessConfig;
}>();

defineEmits<{
  (e: "prev"): void;
  (e: "generate"): void;
  (e: "update:material", value: string): void;
  (e: "update:machineType", value: string): void;
  (e: "update:precision", value: string): void;
}>();

const { t } = useI18n();
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
.process-form {
  max-width: 500px;
}
.panel-actions {
  display: flex;
  justify-content: space-between;
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid var(--border-light);
}
</style>
