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

        <el-card>
          <template #header>{{ t('worldModel.inputContext.title') }}</template>
          <el-button :loading="inputs.loading.value" @click="inputs.inspectModel()">
            {{ t('worldModel.inputContext.inspect') }}
          </el-button>
          <el-alert v-if="inputs.error.value" :title="inputs.error.value" type="error" :closable="false" show-icon />
          <template v-if="inputs.requirements.value">
            <p>{{ inputs.requirements.value.use_fusion ? t('worldModel.inputContext.fusion') : t('worldModel.inputContext.legacy') }}</p>
            <p>{{ inputs.requirements.value.training_history_steps?.length
              ? t('worldModel.inputContext.trainingWindow', { steps: inputs.requirements.value.training_history_steps.join(' / '), provided: historyInput.enabled.value ? historyInput.history.value?.samples.length || 0 : 1 })
              : t('worldModel.inputContext.unknownWindow') }}</p>
            <p v-if="inputs.requirements.value.training_horizon">
              {{ t('worldModel.inputContext.trainingHorizon', { steps: inputs.requirements.value.training_horizon }) }}
            </p>
            <el-alert v-if="inputs.requirements.value.training_horizon && predictForm.horizon > inputs.requirements.value.training_horizon"
              :title="t('worldModel.inputContext.extrapolationHint')" type="warning" :closable="false" show-icon />
            <template v-if="inputs.requirements.value.use_fusion">
              <el-select v-model="inputs.taskId.value" :placeholder="t('worldModel.inputContext.selectPart')" style="width:100%">
                <el-option v-for="source in inputs.requirements.value.geometry_sources" :key="source.task_id"
                  :value="source.task_id" :label="`${source.name} · ${new Date(source.updated_at * 1000).toLocaleString()}`" />
              </el-select>
              <p v-if="!inputs.requirements.value.geometry_sources.length">{{ t('worldModel.inputContext.noParts') }}</p>
              <template v-if="inputs.source.value">
                <p v-if="inputs.source.value.calibrated">{{ t('worldModel.inputContext.calibrated') }}</p>
                <el-form v-else label-position="top">
                  <el-form-item :label="t('worldModel.inputContext.scale')">
                    <el-input-number v-model="inputs.scale.value" :min="0.000001" :precision="6" />
                  </el-form-item>
                  <p>{{ t('worldModel.inputContext.scaleHint') }}</p>
                </el-form>
                <el-button :loading="inputs.preparing.value" @click="inputs.readGeometry()">{{ t('worldModel.inputContext.readGeometry') }}</el-button>
              </template>
              <el-descriptions v-if="inputs.geometry.value" :column="1" border>
                <el-descriptions-item :label="t('worldModel.inputContext.dimensions')">{{ inputs.geometry.value.geometry.bbox_dimensions.map(v => v.toFixed(3)).join(' × ') }} mm</el-descriptions-item>
                <el-descriptions-item :label="t('worldModel.inputContext.featureCount')">{{ inputs.geometry.value.source.feature_count }}</el-descriptions-item>
                <el-descriptions-item :label="t('worldModel.inputContext.scaleSource')">{{ inputs.geometry.value.source.scale_source === 'upstream_calibration' ? t('worldModel.inputContext.calibrated') : t('worldModel.inputContext.userScale') }}</el-descriptions-item>
              </el-descriptions>
              <el-switch v-model="historyInput.enabled.value" :active-text="t('worldModel.inputContext.historyMode')" />
              <template v-if="historyInput.enabled.value">
                <p>{{ t('worldModel.inputContext.historyFileHint') }}</p>
                <p><a :href="historyExampleUrl" download>{{ t('worldModel.inputContext.historyExample') }}</a></p>
                <input :key="historyInput.fileKey.value" type="file" accept=".json,application/json"
                  :aria-label="t('worldModel.inputContext.historyMode')" @change="handleHistoryFile" />
                <p v-if="historyInput.loading.value">{{ t('worldModel.inputContext.historyLoading') }}</p>
                <el-alert v-if="historyInput.error.value" :title="historyInput.error.value" type="error" :closable="false" />
                <el-descriptions v-if="historyInput.history.value" :column="1" border>
                  <el-descriptions-item :label="t('worldModel.inputContext.historySource')">{{ historyInput.history.value.source }}</el-descriptions-item>
                  <el-descriptions-item :label="t('worldModel.inputContext.historyLength')">{{ historyInput.history.value.samples.length }}</el-descriptions-item>
                  <el-descriptions-item :label="t('worldModel.inputContext.historyPeriod')">{{ historyInput.history.value.sample_period_seconds }} s</el-descriptions-item>
                </el-descriptions>
                <p>{{ t('worldModel.inputContext.historyCaveat') }}</p>
              </template>
              <p v-else>{{ t('worldModel.inputContext.snapshotHint') }}</p>
            </template>
            <p v-if="inputs.requirements.value.state_feedback">{{ t('worldModel.inputContext.feedbackHint') }}</p>
          </template>
          <p>{{ inputValidation }}</p>
        </el-card>

        <!-- 预测面板 -->
        <WorldModelPredictPanel
          :model-uri="predictForm.model_uri"
          :horizon="predictForm.horizon"
          :current-state="displayState"
          :state-read-only="historyInput.enabled.value"
          :state-fields="visibleStateFields"
          :field-labels="FEEDBACK_LABELS"
          :prediction-disabled="Boolean(inputValidation) || inputs.loading.value || inputs.preparing.value"
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
import { computed, onMounted, reactive, ref, toRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { useWorldModelStore } from '@/stores/worldModel'
import {
  STATE_FIELD,
  STATE_FIELD_VALUES,
  ACTION_FIELD,
  DEFAULT_HORIZON,
  DEFAULT_WORLD_MODEL_URI,
  type WorldModelPredictRequest,
} from '@/contracts/world_model'
import WorldModelVersionList from '@/components/world_model/WorldModelVersionList.vue'
import WorldModelVersionDetail from '@/components/world_model/WorldModelVersionDetail.vue'
import WorldModelPredictPanel from '@/components/world_model/WorldModelPredictPanel.vue'
import { buildWorldModelRequest, FEEDBACK_LABELS, useWorldModelInputs } from '@/composables/useWorldModelInputs'
import { useWorldModelHistory } from '@/composables/useWorldModelHistory'

const { t } = useI18n()
const historyExampleUrl = `${import.meta.env.BASE_URL}examples/world-model-history.synthetic.json`
const store = useWorldModelStore()

const currentPage = ref(1)

/** 状态由用户填写，空值不能伪装成已采集加工状态。 */
const defaultStateValue: Record<string, number | undefined> = Object.fromEntries(STATE_FIELD_VALUES.map(field => [field, undefined]))

const defaultActionValue: Record<string, number> = {
  [ACTION_FIELD.SPINDLE_SPEED_DELTA]: 0.0,
  [ACTION_FIELD.FEED_RATE_DELTA]: 0.0,
  [ACTION_FIELD.DEPTH_OF_CUT_DELTA]: 0.0,
  [ACTION_FIELD.WIDTH_OF_CUT_DELTA]: 0.0,
}

const predictForm = reactive<{
  model_uri: string
  horizon: number
  current_state: Record<string, number | undefined>
  candidate_action: Record<string, number>
}>({
  model_uri: DEFAULT_WORLD_MODEL_URI,
  horizon: DEFAULT_HORIZON,
  current_state: { ...defaultStateValue },
  candidate_action: { ...defaultActionValue },
})

const inputs = useWorldModelInputs(toRef(predictForm, 'model_uri'))
const historyContext = computed(() => JSON.stringify([
  predictForm.model_uri, inputs.taskId.value, inputs.scale.value, inputs.geometry.value?.source.updated_at,
]))
const historyInput = useWorldModelHistory(historyContext)
const displayState = computed(() => {
  const samples = historyInput.history.value?.samples
  return historyInput.enabled.value
    ? samples?.[samples.length - 1]?.current_state || {}
    : predictForm.current_state
})
const visibleStateFields = computed(() => {
  if (!inputs.requirements.value?.use_fusion) return STATE_FIELD_VALUES
  return [...new Set([
    STATE_FIELD.SPINDLE_SPEED, STATE_FIELD.FEED_RATE, STATE_FIELD.DEPTH_OF_CUT,
    STATE_FIELD.TOOL_WEAR, STATE_FIELD.VIBRATION_RMS, STATE_FIELD.TEMPERATURE,
    ...(inputs.requirements.value.state_feedback ? [STATE_FIELD.CHATTER_PROBABILITY] : []),
    ...inputs.feedbackFields.value,
  ])]
})
const inputValidation = computed(() => {
  try {
    buildWorldModelRequest(predictForm.model_uri, inputs.requirements.value, inputs.geometry.value,
      predictForm.current_state, predictForm.candidate_action, predictForm.horizon,
      historyInput.enabled.value ? historyInput.history.value : undefined)
    return ''
  } catch (err) { return err instanceof Error ? err.message : t('worldModel.predictFailed') }
})
watch(() => [predictForm.model_uri, inputs.taskId.value, inputs.scale.value,
  { ...predictForm.current_state }, { ...predictForm.candidate_action }, predictForm.horizon,
  historyInput.enabled.value, historyInput.history.value],
() => store.clearLastPrediction(), { deep: true })

/** 处理状态字段更新 */
function handleUpdateState(field: string, value: number | undefined): void {
  predictForm.current_state[field] = value
}

/** 处理动作字段更新 */
function handleUpdateAction(field: string, value: number): void {
  predictForm.candidate_action[field] = value
}

function handleHistoryFile(event: Event): void {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (file) void historyInput.load(file)
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
  let request: WorldModelPredictRequest
  try {
    request = buildWorldModelRequest(predictForm.model_uri, inputs.requirements.value, inputs.geometry.value,
      predictForm.current_state, predictForm.candidate_action, predictForm.horizon,
      historyInput.enabled.value ? historyInput.history.value : undefined)
  } catch (err) { ElMessage.error(err instanceof Error ? err.message : t('worldModel.predictFailed')); return }
  const result = await store.predict(request)
  if (!result) {
    if (store.error) ElMessage.error(store.error)
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
  inputs.taskId.value = ''
  inputs.scale.value = undefined
  historyInput.enabled.value = false
  historyInput.clear()
}

onMounted(() => {
  void loadVersions()
  void inputs.inspectModel()
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
  grid-template-columns: minmax(220px, 300px) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

/* ===== 右侧详情 ===== */
.wm-detail-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

@media (max-width: 1100px) {
  .wm-main { grid-template-columns: minmax(0, 1fr); }
}
</style>
