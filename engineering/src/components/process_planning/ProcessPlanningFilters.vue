<template>
  <div class="filter-bar">
    <el-input
      v-model="model.searchKeyword"
      :placeholder="t('processPlanning.routePage.searchPlaceholder')"
      clearable
      size="small"
      class="filter-search"
      :prefix-icon="Search"
    />
    <el-select
      v-model="model.filterType"
      :placeholder="t('processPlanning.routePage.filterTypePlaceholder')"
      size="small"
      class="filter-select"
    >
      <el-option
        :label="t('processPlanning.routePage.typeAll')"
        value="all"
      />
      <el-option
        :label="t('processPlanning.routePage.typeMachining')"
        value="machining"
      />
      <el-option
        :label="t('processPlanning.routePage.typeAssembly')"
        value="assembly"
      />
      <el-option
        :label="t('processPlanning.routePage.typeWelding')"
        value="welding"
      />
      <el-option
        :label="t('processPlanning.routePage.typeHeatTreatment')"
        value="heattreatment"
      />
    </el-select>
    <el-select
      v-model="model.filterStatus"
      :placeholder="t('processPlanning.routePage.filterStatusPlaceholder')"
      size="small"
      class="filter-select"
    >
      <el-option
        :label="t('processPlanning.routePage.statusAll')"
        value="all"
      />
      <el-option
        :label="t('processPlanning.routePage.statusPublished')"
        value="published"
      />
      <el-option
        :label="t('processPlanning.routePage.statusDraft')"
        value="draft"
      />
      <el-option
        :label="t('processPlanning.routePage.statusArchived')"
        value="archived"
      />
    </el-select>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { Search } from '@element-plus/icons-vue'

const props = defineProps<{
  searchKeyword: string
  filterType: string
  filterStatus: string
}>()

const emit = defineEmits<{
  'update:search-keyword': [value: string]
  'update:filter-type': [value: string]
  'update:filter-status': [value: string]
}>()

const { t } = useI18n()

const model = {
  get searchKeyword() { return props.searchKeyword },
  set searchKeyword(v: string) { emit('update:search-keyword', v) },
  get filterType() { return props.filterType },
  set filterType(v: string) { emit('update:filter-type', v) },
  get filterStatus() { return props.filterStatus },
  set filterStatus(v: string) { emit('update:filter-status', v) },
}
</script>

<style scoped>
.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
  flex-wrap: wrap;
}

.filter-search {
  width: 280px;
}

.filter-select {
  width: 160px;
}

@media (max-width: 900px) {
  .filter-search {
    width: 100%;
  }

  .filter-select {
    flex: 1;
    min-width: 120px;
  }
}

@media (max-width: 600px) {
  .filter-bar {
    flex-direction: column;
    align-items: stretch;
  }

  .filter-search,
  .filter-select {
    width: 100%;
  }
}
</style>