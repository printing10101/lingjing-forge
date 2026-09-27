<template>
  <div class="content-card">
    <div class="content-card__header">
      <span class="content-card__title">{{
        t("simulationPage.paramsTitle")
      }}</span>
    </div>
    <div class="content-card__body">
      <el-form label-position="left" label-width="80px" size="small">
        <div class="params-grid">
          <el-form-item :label="t('simulationPage.paramVoxelSize')">
            <el-input-number
              :model-value="simParams.voxelSize"
              :min="0.1"
              :max="10"
              :step="0.1"
              :controls="false"
              style="width: 100%"
              @update:model-value="updateSimParam('voxelSize', $event)"
            />
          </el-form-item>
          <el-form-item :label="t('simulationPage.paramToolType')">
            <el-select
              :model-value="simParams.toolType"
              style="width: 100%"
              @update:model-value="updateSimParam('toolType', $event)"
            >
              <el-option :label="t('simulationPage.toolFlat')" value="flat" />
              <el-option :label="t('simulationPage.toolBall')" value="ball" />
              <el-option :label="t('simulationPage.toolDrill')" value="drill" />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('simulationPage.paramToolDiameter')">
            <el-input-number
              :model-value="simParams.toolDiameter"
              :min="0.5"
              :max="300"
              :step="0.5"
              :controls="false"
              style="width: 100%"
              @update:model-value="updateSimParam('toolDiameter', $event)"
            />
          </el-form-item>
          <el-form-item :label="t('simulationPage.paramToolLength')">
            <el-input-number
              :model-value="simParams.toolLength"
              :min="1"
              :max="500"
              :step="1"
              :controls="false"
              style="width: 100%"
              @update:model-value="updateSimParam('toolLength', $event)"
            />
          </el-form-item>
          <el-form-item :label="t('simulationPage.paramSafeZ')">
            <el-input-number
              :model-value="simParams.safeZ"
              :min="0"
              :max="200"
              :step="1"
              :controls="false"
              style="width: 100%"
              @update:model-value="updateSimParam('safeZ', $event)"
            />
          </el-form-item>
          <el-form-item :label="t('simulationPage.paramCornerRadius')">
            <el-input-number
              :model-value="simParams.toolCornerRadius"
              :min="0"
              :max="150"
              :step="0.5"
              :controls="false"
              style="width: 100%"
              @update:model-value="updateSimParam('toolCornerRadius', $event)"
            />
          </el-form-item>
        </div>
        <el-form-item :label="t('simulationPage.paramStockStl')">
          <el-input
            :model-value="simParams.stockStlPath"
            :placeholder="t('simulationPage.stockStlPlaceholder')"
            clearable
            @input="updateSimParam('stockStlPath', $event)"
          />
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import type { SimParams } from "./types";

const { t } = useI18n();

const props = defineProps<{
  simParams: SimParams;
}>();

const emit = defineEmits<{
  "update:simParams": [value: SimParams];
}>();

function updateSimParam<K extends keyof SimParams>(
  key: K,
  value: SimParams[K] | undefined,
) {
  if (value === undefined) return;
  emit("update:simParams", { ...props.simParams, [key]: value });
}
</script>

<style scoped>
.content-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-bottom: 1px solid var(--bg-200);
}

.content-card__title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.content-card__body {
  padding: 16px 20px;
}

.params-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
}
</style>
