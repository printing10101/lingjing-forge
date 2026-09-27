<template>
  <div class="accept-modify-reject">
    <div class="decision-header">
      <span class="title">{{ $t('acceptModifyReject.title') }}</span>
      <span
        v-if="showTimestamp"
        class="timestamp"
      >{{ formatTimestamp(timestamp) }}</span>
    </div>

    <div
      v-if="aiRecommendation"
      class="recommendation-card"
    >
      <div class="card-header">
        <el-icon><Promotion /></el-icon>
        <span class="card-title">{{ $t('acceptModifyReject.cardRecommendation') }}</span>
        <el-tag
          v-if="confidence !== null"
          :type="getConfidenceTagType(confidence)"
          size="small"
        >
          {{ $t('acceptModifyReject.confidence', { percent: (confidence * 100).toFixed(0) }) }}
        </el-tag>
      </div>
      <div class="card-content">
        <slot name="recommendation">
          <pre>{{ formatRecommendation(aiRecommendation) }}</pre>
        </slot>
      </div>
    </div>

    <div
      v-if="reasoning"
      class="reasoning-card"
    >
      <div class="card-header">
        <el-icon><ChatDotRound /></el-icon>
        <span class="card-title">{{ $t('acceptModifyReject.cardReasoning') }}</span>
      </div>
      <div class="card-content">
        <p>{{ reasoning }}</p>
      </div>
    </div>

    <div
      v-if="showAlternatives && alternatives && alternatives.length > 0"
      class="alternatives-section"
    >
      <div class="section-header">
        <el-icon><Grid /></el-icon>
        <span class="section-title">{{ $t('acceptModifyReject.sectionAlternatives') }}</span>
      </div>
      <el-radio-group
        v-model="selectedAlternative"
        class="alternatives-list"
      >
        <el-card
          v-for="alt in alternatives"
          :key="alt.plan_id"
          class="alternative-card"
          :class="{ 'is-selected': selectedAlternative === alt.plan_id }"
          @click="selectedAlternative = alt.plan_id"
        >
          <div class="alternative-header">
            <el-tag
              :type="getConfidenceTagType(alt.confidence)"
              size="small"
            >
              {{ (alt.confidence * 100).toFixed(0) }}%
            </el-tag>
            <span class="alternative-title">{{ alt.expected_outcome }}</span>
          </div>
          <div
            v-if="showReasoning"
            class="alternative-reasoning"
          >
            {{ alt.reasoning }}
          </div>
        </el-card>
      </el-radio-group>
    </div>

    <div class="action-buttons">
      <el-button
        type="success"
        size="large"
        :icon="Check"
        @click="handleAccept"
      >
        {{ $t('acceptModifyReject.accept') }}
      </el-button>
      <el-button
        v-if="allowModify"
        type="warning"
        size="large"
        :icon="Edit"
        @click="handleModify"
      >
        {{ $t('acceptModifyReject.modify') }}
      </el-button>
      <el-button
        type="danger"
        size="large"
        :icon="Close"
        @click="handleReject"
      >
        {{ $t('acceptModifyReject.reject') }}
      </el-button>
    </div>

    <el-drawer
      v-model="modifyDrawerVisible"
      :title="$t('acceptModifyReject.modifyDrawerTitle')"
      size="50%"
    >
      <div class="modify-content">
        <slot
          name="modify-form"
          :recommendation="aiRecommendation"
        >
          <el-alert
            :title="$t('acceptModifyReject.modifyDrawerHint')"
            type="info"
            :closable="false"
            show-icon
          />
          <el-form
            :model="modifiedParams"
            label-width="120px"
            style="margin-top: 16px;"
          >
            <el-form-item
              v-for="(value, key) in aiRecommendation"
              :key="key"
              :label="String(key)"
            >
              <el-input
                v-if="typeof value === 'string'"
                :model-value="getStringValue(key)"
                @update:model-value="setStringValue(key, $event)"
              />
              <el-input-number
                v-else-if="typeof value === 'number'"
                :model-value="getNumberValue(key)"
                :step="0.01"
                @update:model-value="setNumberValue(key, $event)"
              />
              <el-switch
                v-else-if="typeof value === 'boolean'"
                :model-value="getBoolValue(key)"
                @update:model-value="setBoolValue(key, $event)"
              />
              <pre v-else>{{ JSON.stringify(value, null, 2) }}</pre>
            </el-form-item>
          </el-form>
        </slot>
      </div>
      <template #footer>
        <div class="drawer-footer">
          <el-button @click="modifyDrawerVisible = false">
            {{ $t('common.cancel') }}
          </el-button>
          <el-button
            type="primary"
            @click="confirmModify"
          >
            {{ $t('common.confirm') }}
          </el-button>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Promotion, ChatDotRound, Grid, Check, Edit, Close } from '@element-plus/icons-vue'
import { getConfidenceTagType } from '@/utils/statusHelpers'
import { formatTimestamp } from '@/utils/formatters'

interface AlternativePlan {
  plan_id: string
  parameters: Record<string, unknown>
  expected_outcome: string
  confidence: number
  reasoning: string
}

interface AIRecommendation {
  [key: string]: string | number | boolean | null | undefined
}

interface Props {
  aiRecommendation?: AIRecommendation
  confidence?: number | null
  reasoning?: string
  alternatives?: AlternativePlan[]
  timestamp?: number
  allowModify?: boolean
  showAlternatives?: boolean
  showReasoning?: boolean
  showTimestamp?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  confidence: null,
  reasoning: '',
  alternatives: () => [],
  timestamp: () => Date.now(),
  allowModify: true,
  showAlternatives: true,
  showReasoning: true,
  showTimestamp: true,
})

const emit = defineEmits<{
  accept: [recommendation: AIRecommendation]
  modify: [modifiedParams: AIRecommendation]
  reject: [recommendation: AIRecommendation]
}>()

const { t } = useI18n()

const selectedAlternative = ref<string | undefined>(undefined)
const modifyDrawerVisible = ref(false)
const modifiedParams = ref<AIRecommendation>({})

function getStringValue(key: string | number): string { return String(modifiedParams.value[key] ?? '') }
function setStringValue(key: string | number, val: string) { modifiedParams.value[key] = val }
function getNumberValue(key: string | number): number | undefined { const v = modifiedParams.value[key]; return typeof v === 'number' ? v : undefined }
function setNumberValue(key: string | number, val: number | undefined) { if (val !== undefined) modifiedParams.value[key] = val }
function getBoolValue(key: string | number): boolean { return !!modifiedParams.value[key] }
function setBoolValue(key: string | number, val: boolean | string | number) { modifiedParams.value[key] = !!val }

function formatRecommendation(rec: AIRecommendation): string {
  return JSON.stringify(rec, null, 2)
}

function handleAccept() {
  const selected = selectedAlternative.value
    ? props.alternatives?.find(a => a.plan_id === selectedAlternative.value)
    : null

  const recommendation = selected
    ? { ...selected.parameters, plan_id: selected.plan_id }
    : { ...props.aiRecommendation }

  emit('accept', recommendation)
  ElMessage.success(t('acceptModifyReject.acceptSuccess'))
}

function handleModify() {
  modifiedParams.value = { ...props.aiRecommendation }
  modifyDrawerVisible.value = true
}

function confirmModify() {
  emit('modify', modifiedParams.value)
  modifyDrawerVisible.value = false
  ElMessage.info(t('acceptModifyReject.modifyApplied'))
}

function handleReject() {
  emit('reject', { ...props.aiRecommendation })
  ElMessage.warning(t('acceptModifyReject.rejectApplied'))
}
</script>

<style scoped>
.accept-modify-reject {
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  padding: 16px;
  background: var(--bg-secondary);
}

.decision-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}

.decision-header .title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.decision-header .timestamp {
  font-size: 12px;
  color: var(--text-tertiary);
}

.recommendation-card,
.reasoning-card {
  background: var(--bg-card);
  border-radius: var(--radius-sm);
  padding: 12px;
  margin-bottom: 12px;
  border: 1px solid var(--border-light);
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  font-weight: 500;
  color: var(--accent-primary);
}

.card-content {
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1.6;
}

.card-content pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  background: var(--bg-tertiary);
  padding: 8px;
  border-radius: var(--radius-sm);
  font-size: 12px;
}

.alternatives-section {
  margin: 16px 0;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  font-weight: 500;
  color: var(--success);
}

.alternatives-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.alternative-card {
  cursor: pointer;
  transition: all 0.3s;
  border: 2px solid transparent;
}

.alternative-card:hover {
  border-color: var(--accent-primary);
}

.alternative-card.is-selected {
  border-color: var(--accent-primary);
  background: var(--bg-secondary);
}

.alternative-header {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.alternative-title {
  font-size: 14px;
  color: var(--text-primary);
  line-height: 1.5;
}

.alternative-reasoning {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.5;
}

.action-buttons {
  display: flex;
  gap: 12px;
  justify-content: center;
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--border-light);
}

.modify-content {
  padding: 16px;
}

.drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>
