<template>
  <header class="page-header">
    <div class="page-header__title-block">
      <h1 class="page-header__title">{{ t('rlAgent.title') }}</h1>
      <p class="page-header__subtitle">{{ t('rlAgent.subtitle') }}</p>
    </div>
    <div class="page-header__actions">
      <el-button :icon="Refresh" :loading="loading" @click="$emit('refresh')">
        {{ t('common.refresh') }}
      </el-button>
    </div>
  </header>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { Refresh } from '@element-plus/icons-vue'

defineProps<{ loading: boolean }>()
defineEmits<{ refresh: [] }>()
const { t } = useI18n()
</script>

<style scoped>
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
}

.page-header__title {
  margin: 0;
  font-size: 22px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.page-header__subtitle {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>
