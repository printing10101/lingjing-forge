<template>
  <div class="page-header">
    <div class="page-header__title">
      <h1>{{ t('taskBoard.pageTitle') }}</h1>
    </div>
    <div class="page-header__actions">
      <el-button-group>
        <el-button
          :type="viewMode === 'kanban' ? 'primary' : 'default'"
          size="small"
          @click="$emit('update:viewMode', 'kanban')"
        >
          {{ t('taskBoard.btnKanban') }}
        </el-button>
        <el-button
          :type="viewMode === 'list' ? 'primary' : 'default'"
          size="small"
          @click="$emit('update:viewMode', 'list')"
        >
          {{ t('taskBoard.btnList') }}
        </el-button>
      </el-button-group>
      <el-button
        size="small"
        :icon="Filter"
        @click="$emit('toggleFilter')"
      >
        {{ t('taskBoard.btnFilter') }}
      </el-button>
      <el-button
        type="primary"
        size="small"
        :icon="Plus"
        @click="$emit('create')"
      >
        {{ t('taskBoard.btnCreateTask') }}
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Filter, Plus } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()

defineProps<{
  viewMode: 'kanban' | 'list'
}>()

defineEmits<{
  'update:viewMode': [value: 'kanban' | 'list']
  toggleFilter: []
  create: []
}>()
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
  flex-wrap: wrap;
  gap: 12px;
}

.page-header__title h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.3;
}

.page-header__actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }
}
</style>