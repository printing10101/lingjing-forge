<template>
  <el-card class="rl-act-card">
    <template #header>
      <div class="rl-act-card__header">
        <span>{{ t('rlAgent.actionDecision') }}</span>
        <el-tag v-if="store.lastAction" size="small" type="success">
          {{ t('rlAgent.decisionReady') }}
        </el-tag>
      </div>
    </template>

    <el-form label-width="140px" label-position="left">
      <el-form-item :label="t('rlAgent.fields.modelUri')">
        <el-input v-model="actForm.model_uri" placeholder="model://rl_agent/1.0.0" />
      </el-form-item>
      <el-form-item :label="t('rlAgent.optimizationTarget')">
        <el-select v-model="actForm.optimization_target" class="rl-form-select">
          <el-option
            v-for="target in OPTIMIZATION_TARGET_VALUES"
            :key="target"
            :label="OPTIMIZATION_TARGET_LABELS[target]"
            :value="target"
          />
        </el-select>
      </el-form-item>

      <!-- 当前状态 -->
      <el-divider content-position="left">{{ t('rlAgent.currentState') }}</el-divider>
      <div class="rl-state-grid">
        <el-form-item
          v-for="field in STATE_FIELD_VALUES"
          :key="field"
          :label="STATE_FIELD_LABELS[field]"
          label-width="120px"
        >
          <el-input-number
            v-model="actForm.current_state[field]"
            :step="0.01"
            controls-position="right"
            class="rl-state-input"
          />
        </el-form-item>
      </div>

      <!-- 候选动作集 -->
      <el-divider content-position="left">{{ t('rlAgent.candidateActions') }}</el-divider>
      <div v-for="(action, idx) in actForm.candidate_actions" :key="`action-${idx}`" class="rl-action-row">
        <div class="rl-action-row__header">
          <span>{{ t('rlAgent.actionIndex', { n: idx + 1 }) }}</span>
          <el-button
            v-if="actForm.candidate_actions.length > 1"
            link
            type="danger"
            :icon="Delete"
            @click="handleRemoveAction(idx)"
          />
        </div>
        <div class="rl-action-row__grid">
          <el-form-item
            v-for="field in ACTION_FIELD_VALUES"
            :key="field"
            :label="ACTION_FIELD_LABELS[field]"
            label-width="110px"
          >
            <el-input-number
              v-model="action[field]"
              :step="0.05"
              :min="-1"
              :max="1"
              controls-position="right"
              class="rl-state-input"
            />
          </el-form-item>
        </div>
      </div>
      <el-button :icon="Plus" plain @click="handleAddAction">
        {{ t('rlAgent.addAction') }}
      </el-button>

      <el-form-item class="rl-act-buttons">
        <el-button
          type="primary"
          :loading="store.acting"
          :icon="Promotion"
          @click="handleAct"
        >
          {{ t('rlAgent.runDecision') }}
        </el-button>
        <el-button @click="handleResetActForm">{{ t('common.reset') }}</el-button>
      </el-form-item>
    </el-form>

    <!-- 决策结果 -->
    <div v-if="store.lastAction" class="rl-act-result">
      <el-divider content-position="left">{{ t('rlAgent.decisionResult') }}</el-divider>

      <el-alert
        :title="t('rlAgent.recommendedAction')"
        type="success"
        :closable="false"
        show-icon
        class="rl-recommend-alert"
      >
        <template #default>
          <div class="rl-recommend-body">
            <div class="rl-recommend-action">
              <el-tag
                v-for="(val, key) in store.lastAction.recommended_action.action"
                :key="key"
                class="rl-action-tag"
                :type="Math.abs(val) > 0.5 ? 'warning' : 'info'"
              >
                {{ key }}: {{ val.toFixed(3) }}
              </el-tag>
            </div>
            <div class="rl-recommend-reasoning">
              {{ store.lastAction.recommended_action.reasoning }}
            </div>
          </div>
        </template>
      </el-alert>

      <el-descriptions :column="2" border size="small" class="rl-policy-info">
        <el-descriptions-item :label="t('rlAgent.policy.algorithm')">
          <el-tag size="small" :type="POLICY_ALGORITHM_TAG_TYPE[store.lastAction.policy_info.algorithm]">
            {{ POLICY_ALGORITHM_LABELS[store.lastAction.policy_info.algorithm] }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('rlAgent.policy.version')">
          {{ store.lastAction.policy_info.policy_version }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('rlAgent.policy.episodes')">
          {{ store.lastAction.policy_info.training_episodes }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('rlAgent.policy.epsilon')">
          {{ store.lastAction.policy_info.exploration_rate.toFixed(4) }}
        </el-descriptions-item>
      </el-descriptions>

      <div class="rl-eval-section">
        <div class="rl-eval-section__title">{{ t('rlAgent.actionEvaluations') }}</div>
        <el-table :data="store.lastAction.action_evaluation" size="small" max-height="280" border>
          <el-table-column type="index" label="#" width="50" />
          <el-table-column label="expected_return" width="140">
            <template #default="{ row }">
              {{ row.expected_return.toFixed(4) }}
            </template>
          </el-table-column>
          <el-table-column label="chatter_prob" width="120">
            <template #default="{ row }">
              <span :class="{ 'rl-metric--warn': row.predicted_chatter_prob > 0.3 }">
                {{ row.predicted_chatter_prob.toFixed(4) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="tool_wear" width="120">
            <template #default="{ row }">
              {{ row.predicted_tool_wear.toFixed(4) }}
            </template>
          </el-table-column>
          <el-table-column label="q_value" width="120">
            <template #default="{ row }">
              {{ row.q_value.toFixed(4) }}
            </template>
          </el-table-column>
          <el-table-column label="safety" width="100">
            <template #default="{ row }">
              <el-tag :type="row.safety_violation ? 'danger' : 'success'" size="small">
                {{ row.safety_violation ? t('rlAgent.violation') : t('rlAgent.safe') }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <el-alert
        :title="t('rlAgent.safetyNotice')"
        type="warning"
        :closable="false"
        show-icon
        class="rl-safety-alert"
      />
    </div>
  </el-card>
</template>

<script setup lang="ts">
import { reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Promotion, Plus, Delete } from '@element-plus/icons-vue'
import { useRlAgentStore } from '@/stores/rlAgent'
import {
  STATE_FIELD,
  STATE_FIELD_VALUES,
  STATE_FIELD_LABELS,
  ACTION_FIELD,
  ACTION_FIELD_VALUES,
  ACTION_FIELD_LABELS,
  OPTIMIZATION_TARGET_VALUES,
  OPTIMIZATION_TARGET_LABELS,
  POLICY_ALGORITHM_LABELS,
  POLICY_ALGORITHM_TAG_TYPE,
  DEFAULT_RL_AGENT_URI,
  DEFAULT_OPTIMIZATION_TARGET,
  type OptimizationTarget,
  type RLActRequest,
} from '@/contracts/rl_agent'

const { t } = useI18n()
const store = useRlAgentStore()

defineEmits<{ acted: [] }>()

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

const defaultAction = (): Record<string, number> => ({
  [ACTION_FIELD.SPINDLE_SPEED_DELTA]: 0.0,
  [ACTION_FIELD.FEED_RATE_DELTA]: 0.0,
  [ACTION_FIELD.DEPTH_OF_CUT_DELTA]: 0.0,
  [ACTION_FIELD.WIDTH_OF_CUT_DELTA]: 0.0,
})

const actForm = reactive({
  model_uri: DEFAULT_RL_AGENT_URI,
  optimization_target: DEFAULT_OPTIMIZATION_TARGET as OptimizationTarget,
  current_state: { ...defaultStateValue },
  candidate_actions: [defaultAction(), { ...defaultAction() }] as Array<Record<string, number>>,
})

function handleAddAction(): void {
  actForm.candidate_actions.push(defaultAction())
}

function handleRemoveAction(idx: number): void {
  if (actForm.candidate_actions.length > 1) {
    actForm.candidate_actions.splice(idx, 1)
  }
}

async function handleAct(): Promise<void> {
  const request: RLActRequest = {
    model_uri: actForm.model_uri,
    optimization_target: actForm.optimization_target,
    current_state: { ...actForm.current_state },
    candidate_actions: actForm.candidate_actions.map((a) => ({ ...a })),
    safety_constraints: null,
  }
  const result = await store.act(request)
  if (!result) {
    ElMessage.error(store.error || t('rlAgent.actFailed'))
  } else {
    ElMessage.success(t('rlAgent.actSuccess'))
  }
}

function handleResetActForm(): void {
  actForm.model_uri = DEFAULT_RL_AGENT_URI
  actForm.optimization_target = DEFAULT_OPTIMIZATION_TARGET
  actForm.current_state = { ...defaultStateValue }
  actForm.candidate_actions = [defaultAction(), { ...defaultAction() }]
  store.clearLastAction()
}
</script>

<style scoped>
.rl-act-card__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.rl-form-select {
  width: 100%;
}

.rl-state-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0 16px;
}

.rl-state-input {
  width: 100%;
}

.rl-action-row {
  padding: 12px;
  margin-bottom: 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: var(--radius-sm);
  background: var(--el-fill-color-blank);
}

.rl-action-row__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.rl-action-row__grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0 16px;
}

.rl-act-buttons {
  margin-top: 12px;
}

.rl-act-result {
  margin-top: 8px;
}

.rl-recommend-alert {
  margin-bottom: 12px;
}

.rl-recommend-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rl-recommend-action {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.rl-action-tag {
  font-family: var(--font-mono);
}

.rl-recommend-reasoning {
  font-size: 13px;
  color: var(--el-text-color-regular);
  line-height: 1.5;
}

.rl-policy-info {
  margin: 12px 0;
}

.rl-eval-section {
  margin-top: 12px;
}

.rl-eval-section__title {
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--el-text-color-primary);
}

.rl-metric--warn {
  color: var(--state-error);
  font-weight: 600;
}

.rl-safety-alert {
  margin-top: 16px;
}
</style>
