<template>
  <el-dialog
    :model-value="visible"
    :title="t('equipmentMonitor.dialogSettingsTitle')"
    width="480px"
    :close-on-click-modal="false"
    @update:model-value="emit('update:visible', $event)"
  >
    <el-form label-width="110px" @submit.prevent>
      <el-form-item :label="t('equipmentMonitor.colDeviceName')">
        <el-input :model-value="localForm.name" disabled />
      </el-form-item>
      <el-form-item :label="t('equipmentMonitor.fieldStatus')">
        <el-select v-model="localForm.status" style="width: 100%">
          <el-option :label="t('equipmentMonitor.labelStatusRunning')" :value="t('equipmentMonitor.labelStatusRunning')" />
          <el-option :label="t('equipmentMonitor.labelStatusStandby')" :value="t('equipmentMonitor.labelStatusStandby')" />
          <el-option :label="t('equipmentMonitor.labelStatusMaintenance')" :value="t('equipmentMonitor.labelStatusMaintenance')" />
          <el-option :label="t('equipmentMonitor.labelStatusFault')" :value="t('equipmentMonitor.labelStatusFault')" />
        </el-select>
      </el-form-item>
      <el-form-item :label="t('equipmentMonitor.fieldTemperature')">
        <el-input-number v-model="localForm.temperature" :min="0" :max="500" :precision="1" style="width: 100%" />
      </el-form-item>
      <el-form-item :label="t('equipmentMonitor.fieldVibration')">
        <el-input-number v-model="localForm.vibration" :min="0" :max="50" :precision="2" style="width: 100%" />
      </el-form-item>
      <el-form-item :label="t('equipmentMonitor.fieldRpm')">
        <el-input-number v-model="localForm.rpm" :min="0" :max="50000" style="width: 100%" />
      </el-form-item>
      <el-form-item :label="t('equipmentMonitor.fieldPower')">
        <el-input-number v-model="localForm.power" :min="0" :max="1000" :precision="1" style="width: 100%" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="emit('update:visible', false)">
        {{ t('equipmentMonitor.btnCancel') }}
      </el-button>
      <el-button type="primary" :loading="submitting" @click="handleSave">
        {{ t('equipmentMonitor.btnSubmit') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, watch, toRaw } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

export interface SettingsForm {
  id: number
  name: string
  status: string
  temperature: number | null
  vibration: number | null
  rpm: number | null
  power: number | null
}

const props = defineProps<{
  visible: boolean
  form: SettingsForm
  submitting: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', val: boolean): void
  (e: 'save', form: SettingsForm): void
  (e: 'update:form', form: SettingsForm): void
}>()

// 本地副本：编辑不直接变异 prop（修复 vue/no-mutating-props）
// toRaw：props 是 reactive proxy，structuredClone 直接克隆会抛 DataCloneError
const localForm = ref<SettingsForm>(structuredClone(toRaw(props.form)))

watch(
  () => props.form,
  (val) => {
    if (JSON.stringify(toRaw(val)) !== JSON.stringify(localForm.value)) {
      localForm.value = structuredClone(toRaw(val))
    }
  },
  { deep: true },
)

watch(
  localForm,
  (val) => {
    if (JSON.stringify(toRaw(val)) !== JSON.stringify(toRaw(props.form))) {
      emit('update:form', structuredClone(toRaw(val)))
    }
  },
  { deep: true },
)

function handleSave() {
  emit('save', structuredClone(toRaw(localForm.value)))
}
</script>