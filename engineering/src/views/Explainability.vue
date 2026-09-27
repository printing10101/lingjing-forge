<template>
  <div class="explainability-page">
    <header class="page-header">
      <div class="page-header__title-block">
        <h1 class="page-header__title">{{ t('explainability.title') }}</h1>
        <p class="page-header__subtitle">{{ t('explainability.subtitle') }}</p>
      </div>
      <div class="page-header__actions">
        <el-dropdown trigger="click" @command="handleOpenGenerate">
          <el-button type="primary" :icon="MagicStick">
            {{ t('explainability.generateExplanation') }}
            <el-icon class="el-icon--right"><ArrowDown /></el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item :command="EXPLANATION_TYPE.HIDDEN_STATE">
                <el-tag :type="EXPLANATION_TYPE_TAG_TYPE[EXPLANATION_TYPE.HIDDEN_STATE]" size="small">
                  {{ EXPLANATION_TYPE_LABELS[EXPLANATION_TYPE.HIDDEN_STATE] }}
                </el-tag>
              </el-dropdown-item>
              <el-dropdown-item :command="EXPLANATION_TYPE.GATE_DYNAMICS">
                <el-tag :type="EXPLANATION_TYPE_TAG_TYPE[EXPLANATION_TYPE.GATE_DYNAMICS]" size="small">
                  {{ EXPLANATION_TYPE_LABELS[EXPLANATION_TYPE.GATE_DYNAMICS] }}
                </el-tag>
              </el-dropdown-item>
              <el-dropdown-item :command="EXPLANATION_TYPE.COUNTERFACTUAL">
                <el-tag :type="EXPLANATION_TYPE_TAG_TYPE[EXPLANATION_TYPE.COUNTERFACTUAL]" size="small">
                  {{ EXPLANATION_TYPE_LABELS[EXPLANATION_TYPE.COUNTERFACTUAL] }}
                </el-tag>
              </el-dropdown-item>
              <el-dropdown-item :command="EXPLANATION_TYPE.CONFIDENCE">
                <el-tag :type="EXPLANATION_TYPE_TAG_TYPE[EXPLANATION_TYPE.CONFIDENCE]" size="small">
                  {{ EXPLANATION_TYPE_LABELS[EXPLANATION_TYPE.CONFIDENCE] }}
                </el-tag>
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button :icon="Refresh" :loading="store.anyLoading" @click="handleRefresh">
          {{ t('common.refresh') }}
        </el-button>
      </div>
    </header>

    <div class="ex-main">
      <ExplainabilityExplanationsList
        :filter-type="filterType"
        :current-page="currentPage"
        @filter-change="handleFilterChange"
        @select-explanation="handleSelectExplanation"
        @page-change="handlePageChange"
      />

      <section class="ex-detail-panel">
        <ExplainabilityDetailCard
          @add-to-compare="handleAddToCompare"
          @deleted="loadExplanations"
        />
        <ExplainabilityCompareCard
          :compare-selection="compareSelection"
          @remove-from-compare="handleRemoveFromCompare"
          @clear-compare="handleClearCompare"
          @compared="loadExplanations"
        />
      </section>
    </div>

    <ExplainabilityDialogs ref="dialogsRef" @generated="loadExplanations" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Refresh, MagicStick, ArrowDown } from '@element-plus/icons-vue'
import { useExplainabilityStore } from '@/stores/explainability'
import {
  EXPLANATION_TYPE, EXPLANATION_TYPE_LABELS, EXPLANATION_TYPE_TAG_TYPE,
  type ExplanationType,
} from '@/contracts/explainability'
import ExplainabilityExplanationsList from '@/components/explainability/ExplainabilityExplanationsList.vue'
import ExplainabilityDetailCard from '@/components/explainability/ExplainabilityDetailCard.vue'
import ExplainabilityCompareCard from '@/components/explainability/ExplainabilityCompareCard.vue'
import ExplainabilityDialogs from '@/components/explainability/ExplainabilityDialogs.vue'

const { t } = useI18n()
const store = useExplainabilityStore()

const currentPage = ref(1)
const filterType = ref<ExplanationType | ''>('')
const compareSelection = ref<string[]>([])
const dialogsRef = ref<InstanceType<typeof ExplainabilityDialogs> | null>(null)

async function loadExplanations(): Promise<void> {
  const result = await store.fetchExplanations({
    limit: 50, offset: (currentPage.value - 1) * 50,
    explanation_type: filterType.value || undefined,
  })
  if (!result) ElMessage.error(store.error || t('explainability.loadFailed'))
}

async function handleSelectExplanation(id: string): Promise<void> {
  const result = await store.fetchExplanation(id)
  if (!result) ElMessage.error(store.error || t('explainability.loadFailed'))
}

async function handlePageChange(page: number): Promise<void> {
  currentPage.value = page
  await loadExplanations()
}

async function handleFilterChange(): Promise<void> {
  currentPage.value = 1
  await loadExplanations()
}

async function handleRefresh(): Promise<void> {
  currentPage.value = 1
  await loadExplanations()
}

function handleOpenGenerate(type: ExplanationType): void {
  dialogsRef.value?.open(type)
}

function handleAddToCompare(): void {
  const id = store.currentExplanation?.id
  if (!id) return
  if (compareSelection.value.includes(id)) { ElMessage.warning(t('explainability.alreadyInCompare')); return }
  if (compareSelection.value.length >= 2) { ElMessage.warning(t('explainability.compareFull')); return }
  compareSelection.value.push(id)
  ElMessage.success(t('explainability.addedToCompare'))
}

function handleRemoveFromCompare(id: string): void {
  const idx = compareSelection.value.indexOf(id)
  if (idx >= 0) compareSelection.value.splice(idx, 1)
}

function handleClearCompare(): void {
  compareSelection.value = []
  store.clearLastResults()
}

onMounted(() => { void loadExplanations() })
</script>

<style scoped>
.explainability-page { padding: 20px; display: flex; flex-direction: column; gap: 16px; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; }
.page-header__title { margin: 0; font-size: 22px; font-weight: 600; color: var(--el-text-color-primary); }
.page-header__subtitle { margin: 4px 0 0; font-size: 13px; color: var(--el-text-color-secondary); }
.page-header__actions { display: flex; gap: 8px; }
.ex-main { display: grid; grid-template-columns: 360px 1fr; gap: 16px; align-items: start; }
.ex-detail-panel { display: flex; flex-direction: column; gap: 16px; }
</style>
