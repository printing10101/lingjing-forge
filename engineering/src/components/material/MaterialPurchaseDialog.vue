<template>
  <el-dialog
    :model-value="visible"
    :title="t('materialManagement.dialogPurchaseTitle')"
    width="460px"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="$emit('update:visible', $event)"
  >
    <el-form label-width="90px" @submit.prevent>
      <el-form-item :label="t('materialManagement.fieldMaterial')" required>
        <el-input :model-value="materialLabel" disabled />
      </el-form-item>
      <el-form-item :label="t('materialManagement.fieldQuantity')" required>
        <el-input-number
          v-model="form.quantity"
          :min="1"
          :max="100000"
          style="width: 100%"
        />
      </el-form-item>
      <el-form-item :label="t('materialManagement.fieldSupplier')">
        <el-input
          v-model="form.supplier"
          :placeholder="t('materialManagement.placeholderSupplier')"
          maxlength="128"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="$emit('update:visible', false)">
        {{ t('materialManagement.btnCancel') }}
      </el-button>
      <el-button type="primary" :loading="submitting" @click="$emit('submit', form)">
        {{ t('materialManagement.btnSubmit') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

interface PurchaseFormData {
  material_id: number | ''
  quantity: number
  supplier: string
}

const props = defineProps<{
  visible: boolean
  materialLabel: string
  submitting: boolean
  initialMaterialId: number | ''
  initialSupplier: string
}>()

defineEmits<{
  'update:visible': [value: boolean]
  submit: [formData: PurchaseFormData]
}>()

const form = reactive<PurchaseFormData>({
  material_id: props.initialMaterialId,
  quantity: 1,
  supplier: props.initialSupplier,
})

// 每次打开弹窗时，根据传入的初始值重置表单
watch(
  () => props.visible,
  (newVal) => {
    if (newVal) {
      form.material_id = props.initialMaterialId
      form.quantity = 1
      form.supplier = props.initialSupplier
    }
  },
)
</script>