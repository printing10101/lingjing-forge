<template>
  <div class="error-banner">
    <el-icon :size="16">
      <WarningFilled />
    </el-icon>
    <span>{{ t('taskBoard.msgDataLoadFailed') }}</span>
    <el-button
      text
      type="primary"
      size="small"
      @click="$emit('retry')"
    >
      {{ t('taskBoard.btnRetry') }}
    </el-button>
  </div>
</template>

<script setup lang="ts">
import { WarningFilled } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

defineEmits<{
  retry: []
}>()
</script>

<style scoped>
.error-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  margin-bottom: 16px;
  background: var(--error-bg);
  border: 1px solid var(--error-border);
  border-radius: var(--radius-md);
  color: var(--error);
  font-size: 13px;
}

.error-banner .el-button {
  margin-left: auto;
}
</style>