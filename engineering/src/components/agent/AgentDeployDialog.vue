<template>
  <el-dialog
    :model-value="visible"
    :title="t('agentDashboard.dialogDeployTitle')"
    width="520px"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="$emit('update:visible', $event)"
  >
    <el-form
      ref="deployFormRef"
      :model="deployForm"
      :rules="deployRules"
      label-width="80px"
      label-position="left"
    >
      <el-form-item
        :label="t('agentDashboard.labelName')"
        prop="name"
      >
        <el-input
          v-model="deployForm.name"
          :placeholder="t('agentDashboard.placeholderName')"
        />
      </el-form-item>
      <el-form-item
        :label="t('agentDashboard.labelType')"
        prop="type"
      >
        <el-select
          v-model="deployForm.type"
          :placeholder="t('agentDashboard.placeholderType')"
          size="small"
          style="width: 100%"
        >
          <el-option
            :label="t('agentDashboard.typeMachining')"
            value="machining"
          />
          <el-option
            :label="t('agentDashboard.typeInspection')"
            value="inspection"
          />
          <el-option
            :label="t('agentDashboard.typeScheduling')"
            value="scheduling"
          />
          <el-option
            :label="t('agentDashboard.typeInventory')"
            value="inventory"
          />
          <el-option
            :label="t('agentDashboard.typeMaintenance')"
            value="maintenance"
          />
          <el-option
            :label="t('agentDashboard.typeOptimization')"
            value="optimization"
          />
        </el-select>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="handleCancel">
        {{ t('agentDashboard.btnCancel') }}
      </el-button>
      <el-button
        type="primary"
        :loading="loading"
        @click="handleSubmit"
      >
        {{ t('agentDashboard.btnDeploy') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import type { FormInstance, FormRules } from 'element-plus'
import { useI18n } from 'vue-i18n'

defineProps<{
  visible: boolean
  loading?: boolean
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  submit: [payload: { name: string; type: string }]
}>()

const { t } = useI18n()

const deployFormRef = ref<FormInstance>()
const deployForm = reactive({
  name: '',
  type: '',
})

const deployRules: FormRules = {
  name: [{ required: true, message: t('agentDashboard.msgNameRequired'), trigger: 'blur' }],
  type: [{ required: true, message: t('agentDashboard.msgTypeRequired'), trigger: 'change' }],
}

function handleCancel() {
  emit('update:visible', false)
  deployForm.name = ''
  deployForm.type = ''
}

async function handleSubmit() {
  const valid = await deployFormRef.value?.validate().catch(() => false)
  if (!valid) return

  emit('submit', { name: deployForm.name.trim(), type: deployForm.type })
}
</script>