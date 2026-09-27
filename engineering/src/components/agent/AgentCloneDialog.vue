<template>
  <el-dialog :model-value="visible" :title="t('agentDetail.dialogCloneTitle')" width="400px" @update:model-value="$emit('update:visible', $event)">
    <el-form label-position="top">
      <el-form-item :label="t('agentDetail.labelTargetAgentId')">
        <el-input v-model="targetId" :placeholder="t('agentDetail.placeholderCloneTargetId')" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="$emit('update:visible', false)">
        {{ t('agentDetail.btnCancel') }}
      </el-button>
      <el-button type="primary" @click="handleClone">
        {{ t('agentDetail.btnCloneConfirm') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  (e: 'update:visible', value: boolean): void
  (e: 'clone', targetId: string): void
}>()

const targetId = ref('')

function handleClone() {
  emit('clone', targetId.value)
}
</script>