<template>
  <el-dialog
    :model-value="visible"
    :title="
      isEditing
        ? t('ruleEditDialog.dialogTitleEdit')
        : t('ruleEditDialog.dialogTitleNew')
    "
    width="900px"
    @update:model-value="$emit('update:visible', $event)"
    @close="handleClose"
  >
    <el-form ref="formRef" :model="form" :rules="formRules" label-width="100px">
      <el-row :gutter="16">
        <el-col :span="16">
          <el-form-item :label="t('ruleEditDialog.labelRuleName')" prop="name">
            <el-input
              v-model="form.name"
              :placeholder="t('ruleEditDialog.placeholderRuleName')"
            />
          </el-form-item>
        </el-col>
        <el-col :span="8">
          <el-form-item :label="t('ruleEditDialog.labelGroup')">
            <el-select
              v-model="form.group_id"
              :placeholder="t('ruleEditDialog.placeholderGroup')"
              clearable
              style="width: 100%"
            >
              <el-option
                v-for="g in ruleStore.groups"
                :key="g.id"
                :label="g.name"
                :value="g.id"
              />
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>

      <el-form-item :label="t('ruleEditDialog.labelRuleDesc')">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="2"
          :placeholder="t('ruleEditDialog.placeholderRuleDesc')"
        />
      </el-form-item>

      <ConditionEditor
        :conditions="form.conditions"
        :logic-operator="form.logic_operator"
        @update:conditions="onConditionsUpdate"
        @update:logic-operator="onLogicOperatorUpdate"
        @add-condition="addCondition"
        @remove-condition="removeCondition"
      />

      <ResultEditor :result="form.result" @update:result="onResultUpdate" />

      <el-form-item :label="t('ruleEditDialog.labelStatus')">
        <el-select v-model="form.status" style="width: 120px">
          <el-option :label="t('ruleEditDialog.statusActive')" value="active" />
          <el-option
            :label="t('ruleEditDialog.statusInactive')"
            value="inactive"
          />
          <el-option :label="t('ruleEditDialog.statusDraft')" value="draft" />
        </el-select>
      </el-form-item>

      <el-form-item :label="t('ruleEditDialog.labelPriority')">
        <el-input-number v-model="form.priority" :min="0" :max="100" />
      </el-form-item>

      <el-form-item :label="t('ruleEditDialog.labelRulePreview')">
        <el-alert
          :title="previewText || t('ruleEditDialog.previewEmpty')"
          type="info"
          :closable="false"
          :class="{ 'preview-empty': !previewText }"
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="$emit('update:visible', false)">
        {{ t("ruleEditDialog.btnCancel") }}
      </el-button>
      <el-button type="primary" :loading="submitting" @click="handleSubmit">
        {{
          isEditing
            ? t("ruleEditDialog.btnSave")
            : t("ruleEditDialog.btnCreate")
        }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage } from "element-plus";
import type {
  ProcessRule,
  RuleCondition,
  RuleResult,
  RuleCreateRequest,
  RuleUpdateRequest,
} from "@/types";
import { useRuleStore } from "@/stores/rules";
import type { FormInstance, FormRules } from "element-plus";
import ConditionEditor from "@/components/rule_editor/fields/ConditionEditor.vue";
import ResultEditor from "@/components/rule_editor/fields/ResultEditor.vue";

const { t } = useI18n();

const props = defineProps<{
  visible: boolean;
  rule: ProcessRule | null;
}>();

const emit = defineEmits<{
  "update:visible": [value: boolean];
  saved: [];
}>();

const ruleStore = useRuleStore();
const formRef = ref<FormInstance>();
const submitting = ref(false);
const previewText = ref("");

const defaultCondition = (): RuleCondition => ({
  parameter: "",
  operator: "=",
  value: "",
  unit: undefined,
});

const defaultResult = (): RuleResult => ({
  parameter: "",
  operator: "<=",
  value: "",
  unit: undefined,
});

const form = ref<{
  name: string;
  description: string;
  group_id?: number;
  conditions: RuleCondition[];
  logic_operator: "AND" | "OR";
  result: RuleResult;
  status: "active" | "inactive" | "draft";
  priority: number;
}>({
  name: "",
  description: "",
  group_id: undefined,
  conditions: [defaultCondition()],
  logic_operator: "AND",
  result: defaultResult(),
  status: "active",
  priority: 0,
});

const formRules = computed<FormRules>(() => ({
  name: [
    {
      required: true,
      message: t("ruleEditDialog.msgRuleNameRequired"),
      trigger: "blur",
    },
  ],
  result: [
    {
      required: true,
      message: t("ruleEditDialog.msgResultRequired"),
      trigger: "change",
    },
  ],
}));

const isEditing = computed(() => !!props.rule);

watch(
  () => props.rule,
  (newRule) => {
    if (newRule) {
      form.value = {
        name: newRule.name,
        description: newRule.description,
        group_id: newRule.group_id,
        conditions:
          newRule.conditions.length > 0
            ? [...newRule.conditions]
            : [defaultCondition()],
        logic_operator: newRule.logic_operator,
        result: newRule.result ? { ...newRule.result } : defaultResult(),
        status: newRule.status,
        priority: newRule.priority,
      };
    } else {
      resetForm();
    }
    updatePreview();
  },
  { immediate: true },
);

function resetForm() {
  form.value = {
    name: "",
    description: "",
    group_id: undefined,
    conditions: [defaultCondition()],
    logic_operator: "AND",
    result: defaultResult(),
    status: "active",
    priority: 0,
  };
}

function addCondition() {
  form.value.conditions.push(defaultCondition());
  updatePreview();
}

function removeCondition(index: number) {
  if (form.value.conditions.length > 1) {
    form.value.conditions.splice(index, 1);
    updatePreview();
  }
}

function onConditionsUpdate(conditions: RuleCondition[]) {
  form.value.conditions = conditions;
  updatePreview();
}

function onLogicOperatorUpdate(val: "AND" | "OR") {
  form.value.logic_operator = val;
  updatePreview();
}

function onResultUpdate(result: RuleResult) {
  form.value.result = result;
  updatePreview();
}

function updatePreview() {
  const validConditions = form.value.conditions.filter(
    (c) => c.parameter && c.value,
  );
  if (validConditions.length === 0) {
    previewText.value = "";
    return;
  }

  const condTexts = validConditions.map((c) => {
    let text = `${c.parameter} ${c.operator} ${c.value}`;
    if (c.unit) text += c.unit;
    return text;
  });

  const condStr = condTexts.join(` ${form.value.logic_operator} `);
  let resultStr = "";
  if (form.value.result.parameter && form.value.result.value) {
    resultStr = `${form.value.result.parameter} ${form.value.result.operator} ${form.value.result.value}`;
    if (form.value.result.unit) resultStr += form.value.result.unit;
  }

  previewText.value = resultStr
    ? `IF ${condStr} THEN ${resultStr}`
    : `IF ${condStr}`;
}

async function handleSubmit() {
  if (!formRef.value) return;

  try {
    await formRef.value.validate();
  } catch {
    ElMessage.warning(t("ruleEditDialog.msgCheckForm"));
    return;
  }

  const validConditions = form.value.conditions.filter(
    (c) => c.parameter && c.value,
  );
  if (validConditions.length === 0) {
    ElMessage.warning(t("ruleEditDialog.msgAddCondition"));
    return;
  }

  if (!form.value.result.parameter || !form.value.result.value) {
    ElMessage.warning(t("ruleEditDialog.msgResultRequired"));
    return;
  }

  submitting.value = true;
  try {
    if (isEditing.value && props.rule?.id) {
      const updateData: RuleUpdateRequest = {
        name: form.value.name,
        description: form.value.description,
        group_id: form.value.group_id,
        conditions: validConditions,
        logic_operator: form.value.logic_operator,
        result: form.value.result,
        status: form.value.status,
        priority: form.value.priority,
      };
      await ruleStore.updateRule(props.rule.id, updateData);
    } else {
      const createData: RuleCreateRequest = {
        name: form.value.name,
        description: form.value.description,
        group_id: form.value.group_id,
        conditions: validConditions,
        logic_operator: form.value.logic_operator,
        result: form.value.result,
        status: form.value.status,
        priority: form.value.priority,
      };
      await ruleStore.createRule(createData);
    }
    emit("saved");
    emit("update:visible", false);
  } catch (e: unknown) {
    // createRule/updateRule 已在 store 内通过 ElMessage.error 提示用户，
    // 此处仅记录调用栈上下文便于排查，避免重复弹窗
    console.warn("[RuleEditDialog] handleSubmit failed:", e);
  } finally {
    submitting.value = false;
  }
}

function handleClose() {
  resetForm();
  previewText.value = "";
}
</script>

<style scoped>
.preview_empty :deep(.el-alert__title) {
  color: var(--text-tertiary);
}
</style>
