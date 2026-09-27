<template>
  <div class="dialog-footer">
    <el-button
      v-if="isIdle || isError"
      @click="emit('close')"
    >
      {{ $t('common.cancel') }}
    </el-button>

    <el-button
      v-if="isError"
      type="primary"
      @click="emit('retry')"
    >
      {{ $t('common.retry') }}
    </el-button>

    <el-button
      v-if="isActive"
      :disabled="true"
    >
      {{ $t('common.loading') }}
    </el-button>

    <template v-if="isSuccess">
      <el-button @click="emit('close')">
        {{ $t('common.cancel') }}
      </el-button>
      <el-button
        type="primary"
        :loading="importing"
        @click="emit('import-to-project')"
      >
        {{ $t('dxfImportDialog.importToProject') }}
      </el-button>
    </template>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  isIdle: boolean
  isError: boolean
  isActive: boolean
  isSuccess: boolean
  importing: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'retry'): void
  (e: 'import-to-project'): void
}>()
</script>

<style scoped>
.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>