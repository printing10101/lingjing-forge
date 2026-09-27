<template>
  <el-form-item :label="t('ruleEditDialog.labelResult')" prop="result">
    <el-row :gutter="8" style="width: 100%">
      <el-col :span="6">
        <el-select
          :model-value="result.parameter"
          :placeholder="t('ruleEditDialog.placeholderResultParameter')"
          @change="(val) => updateResultField('parameter', val)"
        >
          <el-option :label="t('ruleEditDialog.paramCutDepth')" value="切深" />
          <el-option :label="t('ruleEditDialog.paramCutWidth')" value="切宽" />
          <el-option
            :label="t('ruleEditDialog.paramCuttingSpeed')"
            value="切削速度"
          />
          <el-option
            :label="t('ruleEditDialog.paramFeedRate')"
            value="进给量"
          />
          <el-option
            :label="t('ruleEditDialog.paramSpindleSpeed')"
            value="主轴转速"
          />
        </el-select>
      </el-col>
      <el-col :span="4">
        <el-select
          :model-value="result.operator"
          @change="(val) => updateResultField('operator', val)"
        >
          <el-option label="=" value="=" />
          <el-option label="<=" value="<=" />
          <el-option label=">=" value=">=" />
          <el-option label="<" value="<" />
          <el-option label=">" value=">" />
          <el-option label="!=" value="!=" />
        </el-select>
      </el-col>
      <el-col :span="6">
        <el-input
          :model-value="result.value"
          :placeholder="t('ruleEditDialog.placeholderResultValue')"
          @input="(val) => updateResultField('value', val)"
        />
      </el-col>
      <el-col :span="4">
        <el-select
          :model-value="result.unit"
          :placeholder="t('ruleEditDialog.placeholderNone')"
          clearable
          @change="(val) => updateResultField('unit', val)"
        >
          <el-option label="mm" value="mm" />
          <el-option label="m/min" value="m/min" />
          <el-option label="mm/rev" value="mm/rev" />
          <el-option label="rpm" value="rpm" />
        </el-select>
      </el-col>
    </el-row>
  </el-form-item>
</template>

<script setup lang="ts">
import { useI18n } from "vue-i18n";
import type { RuleResult } from "@/types";

const { t } = useI18n();

const props = defineProps<{
  result: RuleResult;
}>();

const emit = defineEmits<{
  "update:result": [result: RuleResult];
}>();

function updateResultField(field: keyof RuleResult, value: string | undefined) {
  emit("update:result", { ...props.result, [field]: value });
}
</script>
