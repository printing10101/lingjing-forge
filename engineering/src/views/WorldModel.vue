<!--
  世界模型视图（ADR-017 阶段 8 p8-7）

  对应后端 `python/app/api/v1/world_model.py`。
  详见 `docs/adr/ADR-017-世界模型与RL模块.md`。

  功能：
    1. 世界模型版本列表（左侧）+ 版本详情（右侧顶部）
    2. 轨迹预测面板：输入 current_state + candidate_action + horizon，
       调用 store.predict()，展示预测轨迹与汇总指标
    3. 工程约束：v1 仅离线预测，结果供 RL agent 训练参考，
       不直接接 CNC 控制器（物理执行需持证操作员 + 导师签字 + 保险）
-->
<template>
  <div class="world-model-page">
    <!-- ===== 页面头部 ===== -->
    <header class="page-header">
      <div class="page-header__title-block">
        <h1 class="page-header__title">{{ t('worldModel.title') }}</h1>
        <p class="page-header__subtitle">{{ t('worldModel.subtitle') }}</p>
      </div>
      <div class="page-header__actions">
        <el-button :icon="Refresh" @click="handleRefresh" :loading="store.anyLoading">
          {{ t('common.refresh') }}
        </el-button>
      </div>
    </header>

    <!-- ===== 主体：左列表 + 右详情/预测 ===== -->
    <div class="wm-main">
      <!-- 左侧：版本列表 -->
      <WorldModelVersionList
        :versions="store.versions"
        :current-version="store.currentVersion"
        :versions-loading="store.versionsLoading"
        :total-pages="store.totalPages"
        :version-pagination="store.versionPagination"
        :current-page="currentPage"
        @select-version="handleSelectVersion"
        @update:current-page="handlePageChange"
      />

      <!-- 右侧：版本详情 + 预测面板 -->
      <section class="wm-detail-panel">
        <!-- 版本详情 -->
        <WorldModelVersionDetail
          :current-version="store.currentVersion"
          :version-loading="store.versionLoading"
          @use-active-version="handleUseActiveVersion"
        />

        <!-- 预测面板 -->
        <WorldModelPredictPanel
          :model-uri="predictForm.model_uri"
          :horizon="predictForm.horizon"
          :current-state="predictForm.current_state"
          :candidate-action="predictForm.candidate_action"
          :predicting="store.predicting"
          :last-prediction="store.lastPrediction"
          :last-prediction-max-chatter="store.lastPredictionMaxChatter"
          :last-prediction-step-count="store.lastPredictionStepCount"
          @update:model-uri="predictForm.model_uri = $event"
          @update:horizon="predictForm.horizon = $event"
          @update-state="handleUpdateState"
          @update-action="handleUpdateAction"
          @predict="handlePredict"
          @reset-form="handleResetForm"
        />
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { useWorldModelStore } from '@/stores/worldModel'
import {
  STATE_FIELD,
  ACTION_FIELD,
  DEFAULT_HORIZON,
  DEFAULT_WORLD_MODEL_URI,
  type WorldModelPredictRequest,
} from '@/contracts/world_model'
import WorldModelVersionList from '@/components/world_model/WorldModelVersionList.vue'
import WorldModelVersionDetail from '@/components/world_model/WorldModelVersionDetail.vue'
import WorldModelPredictPanel from '@/components/world_model/WorldModelPredictPanel.vue'

const { t } = useI18n()
const store = useWorldModelStore()

const currentPage = ref(1)

/** 默认状态值（典型 6061-T6 粗加工场景） */
const defaultStateValue: Record<string, number> = {
  [STATE_FIELD.SPINDLE_SPEED]: 8000,
  [STATE_FIELD.FEED_RATE]: 1200,
  [STATE_FIELD.DEPTH_OF_CUT]: 1.5,
  [STATE_FIELD.WIDTH_OF_CUT]: 6.0,
  [STATE_FIELD.TOOL_WEAR]: 0.05,
  [STATE_FIELD.VIBRATION_RMS]: 0.8,
  [STATE_FIELD.TEMPERATURE]: 45.0,
  [STATE_FIELD.CHATTER_PROBABILITY]: 0.1,
}

const defaultActionValue: Record<string, number> = {
  [ACTION_FIELD.SPINDLE_SPEED_DELTA]: 0.0,
  [ACTION_FIELD.FEED_RATE_DELTA]: 0.0,
  [ACTION_FIELD.DEPTH_OF_CUT_DELTA]: 0.0,
  [ACTION_FIELD.WIDTH_OF_CUT_DELTA]: 0.0,
}

const predictForm = reactive<{
  model_uri: string
  horizon: number
  current_state: Record<string, number>
  candidate_action: Record<string, number>
}>({
  model_uri: DEFAULT_WORLD_MODEL_URI,
  horizon: DEFAULT_HORIZON,
  current_state: { ...defaultStateValue },
  candidate_action: { ...defaultActionValue },
})

/** 处理状态字段更新 */
function handleUpdateState(field: string, value: number): void {
  predictForm.current_state[field] = value
}

/** 处理动作字段更新 */
function handleUpdateAction(field: string, value: number): void {
  predictForm.candidate_action[field] = value
}

/** 加载版本列表 */
async function loadVersions(): Promise<void> {
  const result = await store.fetchVersions({ limit: 50, offset: (currentPage.value - 1) * 50 })
  if (!result) {
    ElMessage.error(store.error || t('worldModel.loadFailed'))
  }
}

/** 选择版本查看详情 */
async function handleSelectVersion(version: string): Promise<void> {
  const result = await store.fetchVersion(version)
  if (!result) {
    ElMessage.error(store.error || t('worldModel.loadFailed'))
  }
}

/** 使用当前版本填充预测表单 model_uri */
function handleUseActiveVersion(): void {
  if (store.currentVersion) {
    predictForm.model_uri = store.currentVersion.model_uri
    ElMessage.success(t('worldModel.modelUriFilled'))
  }
}

/** 翻页 */
function handlePageChange(page: number): void {
  currentPage.value = page
  void loadVersions()
}

/** 刷新 */
async function handleRefresh(): Promise<void> {
  currentPage.value = 1
  await loadVersions()
}

/** 执行预测 */
async function handlePredict(): Promise<void> {
  const request: WorldModelPredictRequest = {
    model_uri: predictForm.model_uri,
    horizon: predictForm.horizon,
    current_state: { ...predictForm.current_state },
    candidate_action: { ...predictForm.candidate_action },
  }
  const result = await store.predict(request)
  if (!result) {
    ElMessage.error(store.error || t('worldModel.predictFailed'))
  } else {
    ElMessage.success(t('worldModel.predictSuccess'))
  }
}

/** 重置表单 */
function handleResetForm(): void {
  predictForm.model_uri = DEFAULT_WORLD_MODEL_URI
  predictForm.horizon = DEFAULT_HORIZON
  predictForm.current_state = { ...defaultStateValue }
  predictForm.candidate_action = { ...defaultActionValue }
  store.clearLastPrediction()
}

onMounted(() => {
  void loadVersions()
})
</script>

<style scoped>
.world-model-page {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ===== 页面头部 ===== */
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

/* ===== 主体布局 ===== */
.wm-main {
  display: grid;
  grid-template-columns: 360px 1fr;
  gap: 16px;
  align-items: start;
}

/* ===== 右侧详情 ===== */
.wm-detail-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
</style>