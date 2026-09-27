<template>
  <section class="ex-list-panel">
    <div class="ex-list-panel__header">
      <span class="ex-list-panel__title">{{ t('explainability.explanationList') }}</span>
      <el-select
        :model-value="filterType"
        size="small"
        class="ex-list-panel__filter"
        @change="$emit('filterChange', $event)"
      >
        <el-option :label="t('explainability.allTypes')" value="" />
        <el-option
          v-for="et in EXPLANATION_TYPE_VALUES"
          :key="et"
          :label="EXPLANATION_TYPE_LABELS[et]"
          :value="et"
        />
      </el-select>
    </div>
    <div v-loading="store.explanationsLoading" class="ex-list-panel__body">
      <el-empty
        v-if="!store.explanationsLoading && !store.hasExplanations"
        :description="t('explainability.emptyExplanations')"
      />
      <div
        v-for="exp in store.explanations"
        :key="exp.id"
        class="ex-card"
        :class="{ 'ex-card--active': store.currentExplanation?.id === exp.id }"
        @click="$emit('selectExplanation', exp.id)"
      >
        <div class="ex-card__header">
          <el-tag :type="EXPLANATION_TYPE_TAG_TYPE[exp.explanation_type]" size="small">
            {{ EXPLANATION_TYPE_LABELS[exp.explanation_type] }}
          </el-tag>
          <span class="ex-card__id">{{ exp.id.slice(0, 12) }}\u2026</span>
        </div>
        <div class="ex-card__meta">
          <span>{{ exp.model_uri }}</span>
        </div>
        <div class="ex-card__time">{{ formatDateTime(exp.created_at) }}</div>
      </div>
    </div>
    <el-pagination
      v-if="store.totalPages > 1"
      :model-value="currentPage"
      small
      layout="prev, pager, next"
      :page-size="store.explanationPagination?.limit ?? 50"
      :total="store.explanationPagination?.total ?? 0"
      class="ex-list-panel__pager"
      @current-change="$emit('pageChange', $event)"
    />
  </section>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useExplainabilityStore } from '@/stores/explainability'
import {
  EXPLANATION_TYPE_VALUES,
  EXPLANATION_TYPE_LABELS,
  EXPLANATION_TYPE_TAG_TYPE,
} from '@/contracts/explainability'
import { formatDateTime } from '@/utils/dateTime'

defineProps<{ filterType: string; currentPage: number }>()
defineEmits<{
  filterChange: [value: string]
  selectExplanation: [id: string]
  pageChange: [page: number]
}>()

const { t } = useI18n()
const store = useExplainabilityStore()
</script>

<style scoped>
.ex-list-panel {
  display: flex; flex-direction: column; gap: 8px;
  background: var(--el-bg-color); border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-md); padding: 12px; max-height: calc(100vh - 140px);
}
.ex-list-panel__header {
  display: flex; justify-content: space-between; align-items: center;
  padding-bottom: 8px; border-bottom: 1px solid var(--el-border-color-lighter); gap: 8px;
}
.ex-list-panel__title { font-weight: 600; color: var(--el-text-color-primary); white-space: nowrap; }
.ex-list-panel__filter { width: 140px; }
.ex-list-panel__body { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.ex-list-panel__pager { margin-top: 8px; justify-content: center; }

.ex-card { padding: 10px 12px; border: 1px solid var(--el-border-color-lighter); border-radius: var(--radius-sm); cursor: pointer; transition: all 0.2s; }
.ex-card:hover { border-color: var(--accent-primary); background: var(--accent-light); }
.ex-card--active { border-color: var(--accent-primary); background: var(--accent-light); box-shadow: var(--shadow-ring); }
.ex-card__header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.ex-card__id { font-family: var(--font-mono); font-size: 11px; color: var(--el-text-color-placeholder); }
.ex-card__meta { font-size: 12px; color: var(--el-text-color-secondary); margin-bottom: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ex-card__time { font-size: 11px; color: var(--el-text-color-placeholder); }
</style>
