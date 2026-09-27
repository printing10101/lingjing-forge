<template>
  <el-card class="ex-compare-card">
    <template #header>
      <div class="ex-compare-card__header">
        <span>{{ t('explainability.comparison') }}</span>
        <el-tag v-if="compareSelection.length > 0" size="small">{{ compareSelection.length }} / 2</el-tag>
      </div>
    </template>

    <div class="ex-compare-selection">
      <div v-for="id in compareSelection" :key="id" class="ex-compare-item">
        <span class="ex-compare-item__id">{{ id.slice(0, 16) }}\u2026</span>
        <el-button link type="danger" :icon="Close" @click="$emit('removeFromCompare', id)" />
      </div>
      <el-empty v-if="compareSelection.length === 0" :description="t('explainability.compareEmptyHint')" :image-size="40" />
    </div>

    <el-form :inline="true" class="ex-compare-form">
      <el-form-item :label="t('explainability.comparisonType')">
        <el-select v-model="compareForm.comparison_type" class="ex-compare-select">
          <el-option v-for="ct in COMPARISON_TYPE_VALUES" :key="ct" :label="COMPARISON_TYPE_LABELS[ct]" :value="ct" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :icon="Connection" :loading="store.comparing" :disabled="compareSelection.length !== 2" @click="handleCompare">
          {{ t('explainability.runComparison') }}
        </el-button>
        <el-button @click="$emit('clearCompare')">{{ t('common.clear') }}</el-button>
      </el-form-item>
    </el-form>

    <div v-if="store.lastComparisonResult" class="ex-compare-result">
      <el-divider content-position="left">{{ t('explainability.comparisonResult') }}</el-divider>
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item :label="t('explainability.fields.id')">{{ store.lastComparisonResult.id }}</el-descriptions-item>
        <el-descriptions-item :label="t('explainability.comparisonType')">
          <el-tag :type="COMPARISON_TYPE_TAG_TYPE[store.lastComparisonResult.comparison_type]" size="small">
            {{ COMPARISON_TYPE_LABELS[store.lastComparisonResult.comparison_type] }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('explainability.fields.baseExplanation')">{{ store.lastComparisonResult.base_explanation_id }}</el-descriptions-item>
        <el-descriptions-item :label="t('explainability.fields.comparedExplanation')">{{ store.lastComparisonResult.compared_explanation_id }}</el-descriptions-item>
        <el-descriptions-item :label="t('explainability.fields.createdAt')">{{ formatDateTime(store.lastComparisonResult.created_at) }}</el-descriptions-item>
      </el-descriptions>
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Connection, Close } from '@element-plus/icons-vue'
import { useExplainabilityStore } from '@/stores/explainability'
import {
  COMPARISON_TYPE_VALUES, COMPARISON_TYPE_LABELS, COMPARISON_TYPE_TAG_TYPE,
  DEFAULT_COMPARISON_TYPE, type ComparisonType, type CompareExplanationsRequest,
} from '@/contracts/explainability'
import { formatDateTime } from '@/utils/dateTime'

const { t } = useI18n()
const store = useExplainabilityStore()

const props = defineProps<{ compareSelection: string[] }>()
const emit = defineEmits<{
  removeFromCompare: [id: string]
  clearCompare: []
  compared: []
}>()

const compareForm = reactive({ comparison_type: DEFAULT_COMPARISON_TYPE as ComparisonType })

async function handleCompare(): Promise<void> {
  if (props.compareSelection.length !== 2) return
  const request: CompareExplanationsRequest = {
    base_explanation_id: props.compareSelection[0],
    compared_explanation_id: props.compareSelection[1],
    comparison_type: compareForm.comparison_type,
  }
  const result = await store.compareExplanations(request)
  if (!result) {
    ElMessage.error(store.error || t('explainability.compareFailed'))
  } else {
    ElMessage.success(t('explainability.compareSuccess'))
    emit('compared')
  }
}
</script>

<style scoped>
.ex-compare-card__header { display: flex; justify-content: space-between; align-items: center; }
.ex-compare-selection { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.ex-compare-item { display: flex; justify-content: space-between; align-items: center; padding: 6px 10px; border: 1px solid var(--el-border-color-lighter); border-radius: var(--radius-xs); background: var(--el-fill-color-blank); }
.ex-compare-item__id { font-family: var(--font-mono); font-size: 12px; color: var(--el-text-color-regular); }
.ex-compare-form { margin-top: 8px; }
.ex-compare-select { width: 200px; }
.ex-compare-result { margin-top: 8px; }
</style>
