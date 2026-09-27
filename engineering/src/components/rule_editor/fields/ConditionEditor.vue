<template>
  <div class="condition-editor">
    <el-form-item :label="t('ruleEditDialog.labelLogicOperator')">
      <el-radio-group
        :model-value="logicOperator"
        @change="onLogicOperatorChange"
      >
        <el-radio-button value="AND">
          {{ t("ruleEditDialog.radioAnd") }}
        </el-radio-button>
        <el-radio-button value="OR">
          {{ t("ruleEditDialog.radioOr") }}
        </el-radio-button>
      </el-radio-group>
    </el-form-item>

    <el-form-item :label="t('ruleEditDialog.labelConditions')">
      <div class="conditions-container">
        <el-table :data="localConditions" border size="small">
          <el-table-column
            :label="t('ruleEditDialog.labelParameter')"
            width="180"
          >
            <template #default="{ $index }">
              <el-select
                :model-value="localConditions[$index].parameter"
                :placeholder="t('ruleEditDialog.placeholderParameter')"
                @change="
                  (val) => updateConditionField($index, 'parameter', val)
                "
              >
                <el-option-group :label="t('ruleEditDialog.groupMaterial')">
                  <el-option
                    :label="t('ruleEditDialog.paramMaterial')"
                    value="材料"
                  />
                  <el-option
                    :label="t('ruleEditDialog.paramMaterialHardness')"
                    value="材料硬度"
                  />
                </el-option-group>
                <el-option-group :label="t('ruleEditDialog.groupProcess')">
                  <el-option
                    :label="t('ruleEditDialog.paramProcess')"
                    value="工序"
                  />
                  <el-option
                    :label="t('ruleEditDialog.paramMachiningPrecision')"
                    value="加工精度"
                  />
                  <el-option
                    :label="t('ruleEditDialog.paramSurfaceRoughness')"
                    value="表面粗糙度"
                  />
                </el-option-group>
                <el-option-group :label="t('ruleEditDialog.groupTool')">
                  <el-option
                    :label="t('ruleEditDialog.paramToolType')"
                    value="刀具类型"
                  />
                  <el-option
                    :label="t('ruleEditDialog.paramToolDiameter')"
                    value="刀具直径"
                  />
                </el-option-group>
                <el-option-group :label="t('ruleEditDialog.groupCutting')">
                  <el-option
                    :label="t('ruleEditDialog.paramCuttingSpeed')"
                    value="切削速度"
                  />
                  <el-option
                    :label="t('ruleEditDialog.paramFeedRate')"
                    value="进给量"
                  />
                  <el-option
                    :label="t('ruleEditDialog.paramCutDepth')"
                    value="切深"
                  />
                  <el-option
                    :label="t('ruleEditDialog.paramCutWidth')"
                    value="切宽"
                  />
                  <el-option
                    :label="t('ruleEditDialog.paramSpindleSpeed')"
                    value="主轴转速"
                  />
                </el-option-group>
              </el-select>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('ruleEditDialog.labelOperator')"
            width="110"
          >
            <template #default="{ $index }">
              <el-select
                :model-value="localConditions[$index].operator"
                @change="(val) => updateConditionField($index, 'operator', val)"
              >
                <el-option label="=" value="=" />
                <el-option label="<" value="<" />
                <el-option label=">" value=">" />
                <el-option label="<=" value="<=" />
                <el-option label=">=" value=">=" />
                <el-option label="!=" value="!=" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column :label="t('ruleEditDialog.labelValue')" width="140">
            <template #default="{ $index }">
              <el-input
                :model-value="localConditions[$index].value"
                :placeholder="t('ruleEditDialog.placeholderValue')"
                @input="(val) => updateConditionField($index, 'value', val)"
              />
            </template>
          </el-table-column>
          <el-table-column :label="t('ruleEditDialog.labelUnit')" width="100">
            <template #default="{ $index }">
              <el-select
                :model-value="localConditions[$index].unit"
                :placeholder="t('ruleEditDialog.placeholderNone')"
                clearable
                @change="(val) => updateConditionField($index, 'unit', val)"
              >
                <el-option label="mm" value="mm" />
                <el-option label="m/min" value="m/min" />
                <el-option label="mm/rev" value="mm/rev" />
                <el-option label="rpm" value="rpm" />
                <el-option label="HB" value="HB" />
                <el-option label="μm" value="μm" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column :label="t('ruleEditDialog.labelActions')" width="80">
            <template #default="{ $index }">
              <el-button
                size="small"
                type="danger"
                link
                :disabled="localConditions.length <= 1"
                @click="removeCondition($index)"
              >
                {{ t("ruleEditDialog.btnDelete") }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-button
          size="small"
          type="primary"
          style="margin-top: 8px"
          @click="addCondition"
        >
          <el-icon><Plus /></el-icon>
          {{ t("ruleEditDialog.btnAddCondition") }}
        </el-button>
      </div>
    </el-form-item>
  </div>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import { Plus } from "@element-plus/icons-vue";
import type { RuleCondition } from "@/types";

const { t } = useI18n();

const props = defineProps<{
  conditions: RuleCondition[];
  logicOperator: "AND" | "OR";
}>();

const emit = defineEmits<{
  "update:conditions": [conditions: RuleCondition[]];
  "update:logic-operator": [value: "AND" | "OR"];
  "add-condition": [];
  "remove-condition": [index: number];
}>();

const localConditions = ref<RuleCondition[]>([]);

watch(
  () => props.conditions,
  (val) => {
    localConditions.value = val.map((c) => ({ ...c }));
  },
  { immediate: true, deep: true },
);

function updateConditionField(
  index: number,
  field: keyof RuleCondition,
  value: string | undefined,
) {
  const updated = localConditions.value.map((c, i) =>
    i === index ? { ...c, [field]: value } : { ...c },
  );
  localConditions.value = updated;
  emit(
    "update:conditions",
    updated.map((c) => ({ ...c })),
  );
}

function onLogicOperatorChange(val: string | number | boolean | undefined) {
  emit("update:logic-operator", val as "AND" | "OR");
}

function addCondition() {
  emit("add-condition");
}

function removeCondition(index: number) {
  emit("remove-condition", index);
}
</script>

<style scoped>
.conditions-container {
  width: 100%;
}
</style>
