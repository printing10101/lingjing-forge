<template>
  <el-card v-loading="store.explanationLoading" class="ex-detail-card">
    <template #header>
      <div class="ex-detail-card__header">
        <span>{{ t('explainability.explanationDetail') }}</span>
        <div v-if="store.currentExplanation" class="ex-detail-card__actions">
          <el-button link type="primary" :icon="Download" :loading="store.explanationLoading" @click="handleLoadPayload">
            {{ t('explainability.loadPayload') }}
          </el-button>
          <el-button link type="warning" :icon="CopyDocument" @click="$emit('addToCompare')">
            {{ t('explainability.addToCompare') }}
          </el-button>
          <el-button link type="danger" :icon="Delete" :loading="store.deleting" @click="handleDelete">
            {{ t('common.delete') }}
          </el-button>
        </div>
      </div>
    </template>

    <el-empty v-if="!store.explanationLoading && !store.currentExplanation" :description="t('explainability.selectHint')" />

    <template v-else-if="store.currentExplanation">
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item :label="t('explainability.fields.id')">{{ store.currentExplanation.id }}</el-descriptions-item>
        <el-descriptions-item :label="t('explainability.fields.type')">
          <el-tag :type="EXPLANATION_TYPE_TAG_TYPE[store.currentExplanation.explanation_type]" size="small">
            {{ EXPLANATION_TYPE_LABELS[store.currentExplanation.explanation_type] }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('explainability.fields.modelUri')">{{ store.currentExplanation.model_uri }}</el-descriptions-item>
        <el-descriptions-item :label="t('explainability.fields.inputSignature')"><code>{{ store.currentExplanation.input_signature }}</code></el-descriptions-item>
        <el-descriptions-item :label="t('explainability.fields.payloadSize')">{{ formatBytes(store.currentExplanation.payload_size_bytes) }}</el-descriptions-item>
        <el-descriptions-item :label="t('explainability.fields.sourceSnapshot')">{{ store.currentExplanation.source_snapshot_id || '\u2014' }}</el-descriptions-item>
        <el-descriptions-item :label="t('explainability.fields.createdBy')">{{ store.currentExplanation.created_by || '\u2014' }}</el-descriptions-item>
        <el-descriptions-item :label="t('explainability.fields.createdAt')">{{ formatDateTime(store.currentExplanation.created_at) }}</el-descriptions-item>
        <el-descriptions-item :label="t('explainability.fields.expiresAt')">{{ store.currentExplanation.expires_at ? formatDateTime(store.currentExplanation.expires_at) : '\u2014' }}</el-descriptions-item>
      </el-descriptions>

      <div v-if="currentPayload" class="ex-payload">
        <el-divider content-position="left">{{ t('explainability.payload') }}</el-divider>
        <pre class="ex-payload-json">{{ JSON.stringify(currentPayload, null, 2) }}</pre>
      </div>
    </template>
  </el-card>
</template>

<script setup lang="ts">
import { shallowRef } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, Delete, CopyDocument } from '@element-plus/icons-vue'
import { useExplainabilityStore } from '@/stores/explainability'
import { EXPLANATION_TYPE_LABELS, EXPLANATION_TYPE_TAG_TYPE, type ExplanationPayload } from '@/contracts/explainability'
import { formatDateTime } from '@/utils/dateTime'
import { formatFileSize } from '@/utils/formatters'

const { t } = useI18n()
const store = useExplainabilityStore()

const emit = defineEmits<{ addToCompare: []; deleted: [] }>()

const currentPayload = shallowRef<ExplanationPayload | null>(null)

function formatBytes(bytes: number): string {
  return formatFileSize(bytes)
}

async function handleLoadPayload(): Promise<void> {
  if (!store.currentExplanation) return
  const result = await store.fetchExplanation(store.currentExplanation.id, { include_payload: true })
  if (!result) {
    ElMessage.error(store.error || t('explainability.loadPayloadFailed'))
  } else if (result.payload) {
    currentPayload.value = result.payload
    ElMessage.success(t('explainability.payloadLoaded'))
  }
}

async function handleDelete(): Promise<void> {
  if (!store.currentExplanation) return
  try {
    await ElMessageBox.confirm(t('explainability.deleteConfirm'), t('common.delete'), { type: 'warning' })
  } catch { return }
  const result = await store.deleteExplanation(store.currentExplanation.id)
  if (!result) {
    ElMessage.error(store.error || t('explainability.deleteFailed'))
  } else {
    ElMessage.success(t('explainability.deleteSuccess'))
    currentPayload.value = null
    emit('deleted')
  }
}
</script>

<style scoped>
.ex-detail-card__header { display: flex; justify-content: space-between; align-items: center; }
.ex-detail-card__actions { display: flex; gap: 4px; }
.ex-payload { margin-top: 12px; }
.ex-payload-json { margin: 0; padding: 12px; background: var(--el-fill-color-darker); border-radius: var(--radius-xs); font-size: 12px; font-family: var(--font-mono); color: var(--el-text-color-regular); max-height: 480px; overflow: auto; white-space: pre-wrap; word-break: break-all; }
</style>
