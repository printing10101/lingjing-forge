<template>
  <el-dialog
    :model-value="visible"
    :title="t('taskBoard.dialogCreateTitle')"
    width="460px"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="$emit('update:visible', $event)"
  >
    <el-form label-width="90px" @submit.prevent>
      <el-form-item :label="t('taskBoard.fieldName')" required>
        <el-input
          v-model="createForm.name"
          :placeholder="t('taskBoard.placeholderName')"
          maxlength="128"
        />
      </el-form-item>
      <el-form-item :label="t('taskBoard.labelTaskType')" required>
        <el-select
          v-model="createForm.task_type"
          :placeholder="t('taskBoard.placeholderTaskType')"
          style="width: 100%"
        >
          <el-option
            v-for="opt in createTaskTypeOptions"
            :key="opt.value"
            :label="opt.label"
            :value="opt.value"
          />
        </el-select>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="$emit('update:visible', false)">
        {{ t('taskBoard.btnCancel') }}
      </el-button>
      <el-button
        type="primary"
        :loading="submitting"
        @click="handleSubmit"
      >
        {{ t('taskBoard.btnSubmit') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

const props = defineProps<{
  visible: boolean
  submitting: boolean
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  submit: [data: { name: string; task_type: string }]
}>()

const createForm = ref({ name: '', task_type: '' })

const createTaskTypeOptions = computed(() => [
  { value: 'lnn_training', label: t('taskBoard.typeLnnTraining') },
  { value: 'lnn_inference', label: t('taskBoard.typeLnnInference') },
  { value: 'lnn_batch_inference', label: t('taskBoard.typeBatchInference') },
  { value: 'data_processing', label: t('taskBoard.typeDataProcessing') },
  { value: 'model_export', label: t('taskBoard.typeModelExport') },
  { value: 'model_quantization', label: t('taskBoard.typeModelQuantization') },
])

watch(() => props.visible, (val) => {
  if (val) {
    createForm.value = { name: '', task_type: '' }
  }
})

function handleSubmit() {
  if (!createForm.value.name.trim() || !createForm.value.task_type) return
  emit('submit', { ...createForm.value })
}
</script>

<style scoped>
/* No additional styles needed — relies on Element Plus dialog styles */
</style>