<template>
  <el-dialog
    :model-value="visible"
    :title="t('materialManagement.dialogStockInTitle')"
    width="460px"
    :close-on-click-modal="false"
    destroy-on-close
    @update:model-value="$emit('update:visible', $event)"
  >
    <el-form label-width="90px" @submit.prevent>
      <el-form-item :label="t('materialManagement.fieldMaterial')" required>
        <el-select
          v-model="form.material_id"
          :placeholder="t('materialManagement.placeholderSelectMaterial')"
          style="width: 100%"
          filterable
        >
          <el-option
            v-for="m in materials"
            :key="m.id"
            :label="`${m.code} - ${m.name}`"
            :value="m.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item :label="t('materialManagement.fieldQuantity')" required>
        <el-input-number
          v-model="form.quantity"
          :min="1"
          :max="100000"
          style="width: 100%"
        />
      </el-form-item>
      <el-form-item :label="t('materialManagement.fieldRemark')">
        <el-input
          v-model="form.remark"
          :placeholder="t('materialManagement.placeholderRemark')"
          maxlength="200"
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

interface Material {
  id: number
  code: string
  name: string
  spec: string
  category: string
  quantity: number
  safe_quantity: number
  status: string
  location: string
  unit: string
  supplier: string
  created_at: string
  updated_at: string
}

interface StockInFormData {
  material_id: number | ''
  quantity: number
  remark: string
}

const props = defineProps<{
  visible: boolean
  materials: Material[]
  submitting: boolean
  initialMaterialId: number | ''
}>()

defineEmits<{
  'update:visible': [value: boolean]
  submit: [formData: StockInFormData]
}>()

const form = reactive<StockInFormData>({
  material_id: props.initialMaterialId,
  quantity: 1,
  remark: '',
})

// 每次打开弹窗时，根据传入的 initialMaterialId 重置表单
watch(
  () => props.visible,
  (newVal) => {
    if (newVal) {
      form.material_id = props.initialMaterialId
      form.quantity = 1
      form.remark = ''
    }
  },
)
</script>