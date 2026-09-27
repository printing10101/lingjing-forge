<template>
  <el-dialog
    v-model="dialogVisible"
    :title="title"
    width="720px"
    :close-on-click-modal="false"
  >
    <el-form
      :model="localForm"
      label-width="100px"
    >
      <el-form-item :label="formTemplateLabel">
        <el-select
          v-model="localForm.templateName"
          :placeholder="formTemplatePlaceholder"
          clearable
          style="width: 100%"
          @change="handleTemplateSelect"
        >
          <el-option
            v-for="tpl in builtinTemplates"
            :key="tpl.name"
            :label="`${tpl.name} (v${tpl.version})`"
            :value="tpl.name"
          />
        </el-select>
      </el-form-item>
      <el-form-item :label="formSpecLabel">
        <el-input
          v-model="localForm.specYaml"
          type="textarea"
          :rows="14"
          :placeholder="formSpecPlaceholder"
          class="spec-editor"
        />
      </el-form-item>
      <el-form-item :label="formOwnerLabel">
        <el-input
          v-model="localForm.ownerId"
          :placeholder="formOwnerPlaceholder"
          clearable
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="handleCancel">
        {{ btnCancelText }}
      </el-button>
      <el-button
        :loading="validating"
        @click="emit('validate')"
      >
        {{ btnValidateText }}
      </el-button>
      <el-button
        type="primary"
        :loading="submitting"
        @click="emit('submit')"
      >
        {{ confirmButtonText }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, ref, watch, toRaw } from 'vue'
import type { WorkflowSpec } from '@/contracts/task'

const props = defineProps<{
  visible: boolean
  mode: 'submit' | 'resume'
  title: string
  confirmButtonText: string
  form: { templateName: string; specYaml: string; ownerId: string }
  builtinTemplates: Array<{ name: string; version: string; spec: WorkflowSpec }>
  validating: boolean
  submitting: boolean
  formTemplateLabel: string
  formTemplatePlaceholder: string
  formSpecLabel: string
  formSpecPlaceholder: string
  formOwnerLabel: string
  formOwnerPlaceholder: string
  btnCancelText: string
  btnValidateText: string
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  submit: []
  cancel: []
  validate: []
  'template-select': [name: string]
  'update:form': [form: { templateName: string; specYaml: string; ownerId: string }]
}>()

const dialogVisible = computed({
  get: () => props.visible,
  set: (val: boolean) => emit('update:visible', val),
})

// 本地副本：表单编辑不直接变异 prop（修复 vue/no-mutating-props）
// 双向同步：父组件外部变更（模板选择、重置）→ 覆盖本地副本；本地编辑 → emit 回写
// toRaw：props 是 reactive proxy，直接 spread 会携带 proxy 引用，统一用 toRaw 取原始值
const localForm = ref({ ...toRaw(props.form) })

watch(
  () => props.form,
  (val) => {
    if (JSON.stringify(toRaw(val)) !== JSON.stringify(localForm.value)) {
      localForm.value = { ...toRaw(val) }
    }
  },
  { deep: true },
)

watch(
  localForm,
  (val) => {
    if (JSON.stringify(toRaw(val)) !== JSON.stringify(toRaw(props.form))) {
      emit('update:form', { ...toRaw(val) })
    }
  },
  { deep: true },
)

function handleTemplateSelect(name: string) {
  emit('template-select', name)
}

function handleCancel() {
  emit('cancel')
  emit('update:visible', false)
}
</script>

<style scoped>
:deep(.spec-editor .el-textarea__inner) {
  font-family: var(--font-mono);
  font-size: 12px;
}
</style>