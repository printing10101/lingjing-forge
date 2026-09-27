<template>
  <el-dialog
    :model-value="visible"
    :title="title"
    width="720px"
    :close-on-click-modal="false"
    append-to-body
    @update:model-value="onVisibleChange"
    @open="onOpen"
    @closed="onClosed"
  >
    <div
      v-if="!provider"
      class="empty-state"
    >
      <el-empty
        :description="t('settings.testDialog.emptyProvider')"
        :image-size="60"
      />
    </div>

    <template v-else>
      <!-- Provider 概要 -->
      <el-descriptions
        :column="2"
        border
        size="small"
        class="provider-meta"
      >
        <el-descriptions-item label="Provider">
          {{ provider.name }}
          <span class="mono">({{ provider.provider_id }})</span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('settings.testDialog.labelType')">
          <el-tag
            size="small"
            effect="plain"
          >
            {{ provider.provider_type }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item
          label="Base URL"
          :span="2"
        >
          <span class="mono">{{ provider.base_url || '-' }}</span>
        </el-descriptions-item>
        <el-descriptions-item
          :label="t('settings.testDialog.labelDefaultModel')"
          :span="2"
        >
          <span class="mono">{{ provider.default_model || '-' }}</span>
        </el-descriptions-item>
      </el-descriptions>

      <!-- 消息输入区 -->
      <div class="section-label">
        {{ t('settings.testDialog.sectionMessageList') }}
      </div>
      <div class="messages-area">
        <div
          v-for="msg in messages"
          :key="msg.id"
          class="message-row"
        >
          <el-select
            v-model="msg.role"
            size="small"
            style="width: 110px;"
          >
            <el-option
              label="system"
              value="system"
            />
            <el-option
              label="user"
              value="user"
            />
            <el-option
              label="assistant"
              value="assistant"
            />
          </el-select>
          <el-input
            v-model="msg.content"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 4 }"
            size="small"
            :placeholder="t('settings.testDialog.placeholderMessage')"
            style="flex: 1;"
          />
          <el-button
            size="small"
            type="danger"
            circle
            :disabled="messages.length <= 1"
            @click="removeMessage(msg.id)"
          >
            <el-icon><Delete /></el-icon>
          </el-button>
        </div>
        <el-button
          size="small"
          text
          type="primary"
          @click="addMessage"
        >
          {{ t('settings.testDialog.btnAddMessage') }}
        </el-button>
      </div>

      <!-- 参数 -->
      <div class="params-area">
        <div class="param-item">
          <span class="param-label">max_tokens</span>
          <el-slider
            v-model="params.max_tokens"
            :min="16"
            :max="4096"
            :step="16"
            show-input
            style="flex: 1; max-width: 360px;"
          />
        </div>
        <div class="param-item">
          <span class="param-label">temperature</span>
          <el-slider
            v-model="params.temperature"
            :min="0"
            :max="2"
            :step="0.1"
            show-input
            style="flex: 1; max-width: 360px;"
          />
        </div>
        <div class="param-item">
          <span class="param-label">{{ t('settings.testDialog.paramOverrideModel') }}</span>
          <el-input
            v-model="params.model"
            size="small"
            clearable
            :placeholder="t('settings.testDialog.placeholderOverrideModel', { model: provider.default_model || '-' })"
            style="flex: 1; max-width: 360px;"
          />
        </div>
      </div>

      <!-- 调用按钮 -->
      <div class="action-bar">
        <el-button
          type="primary"
          :loading="store.testing"
          :disabled="!canSubmit"
          @click="runTest"
        >
          <el-icon style="margin-right: 4px;">
            <VideoPlay />
          </el-icon>
          {{ t('settings.testDialog.btnInvoke') }}
        </el-button>
        <el-button
          :disabled="store.testing"
          @click="resetMessages"
        >
          <el-icon style="margin-right: 4px;">
            <RefreshLeft />
          </el-icon>
          {{ t('settings.testDialog.btnResetMessages') }}
        </el-button>
      </div>

      <!-- 响应结果 -->
      <div
        v-if="result"
        class="result-area"
      >
        <div class="section-label">
          {{ t('settings.testDialog.sectionResult') }}
          <el-tag
            :type="resultTagType"
            size="small"
            effect="plain"
            style="margin-left: 8px;"
          >
            {{ result.finish_reason || 'ok' }}
          </el-tag>
          <span class="result-latency">{{ result.latency_ms }}ms</span>
        </div>

        <div class="result-content">
          {{ result.content }}
        </div>

        <el-descriptions
          :column="3"
          border
          size="small"
          class="result-meta"
        >
          <el-descriptions-item label="Provider">
            <span class="mono">{{ result.provider_id }}</span>
          </el-descriptions-item>
          <el-descriptions-item :label="t('settings.testDialog.labelModel')">
            <span class="mono">{{ result.model }}</span>
          </el-descriptions-item>
          <el-descriptions-item :label="t('settings.testDialog.labelLatency')">
            {{ result.latency_ms }}ms
          </el-descriptions-item>
          <el-descriptions-item label="Prompt tokens">
            {{ result.usage?.prompt_tokens ?? '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="Completion tokens">
            {{ result.usage?.completion_tokens ?? '-' }}
          </el-descriptions-item>
          <el-descriptions-item label="Total tokens">
            {{ result.usage?.total_tokens ?? '-' }}
          </el-descriptions-item>
        </el-descriptions>
      </div>

      <!-- 错误提示 -->
      <el-alert
        v-if="errorMsg"
        :title="errorMsg"
        type="error"
        :closable="true"
        show-icon
        style="margin-top: 12px;"
        @close="errorMsg = ''"
      />
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Delete, VideoPlay, RefreshLeft } from '@element-plus/icons-vue'
import { useLLMProvidersStore } from '@/stores/llmProviders'
import type { LLMProvider, ChatTestResponse, ChatTestRequest } from '@/types/llmProvider'

const { t } = useI18n()

const props = defineProps<{
  visible: boolean
  provider: LLMProvider | null
}>()

const emit = defineEmits<{
  (e: 'update:visible', val: boolean): void
}>()

const store = useLLMProvidersStore()

interface MessageInput {
  id: number
  role: 'system' | 'user' | 'assistant'
  content: string
}

// 消息唯一 id 计数器，作为 v-for key 防止 splice 中间删除时 DOM 复用错误
let _nextMessageId = 1
const newMessageId = (): number => _nextMessageId++

const messages = ref<MessageInput[]>([
  { id: newMessageId(), role: 'user', content: t('settings.testDialog.defaultGreeting') },
])

const params = reactive({
  max_tokens: 256,
  temperature: 0.7,
  model: '',
})

const result = ref<ChatTestResponse | null>(null)
const errorMsg = ref('')

const title = computed(() => {
  if (!props.provider) return t('settings.testDialog.titleDefault')
  return t('settings.testDialog.titleSuffix', { name: props.provider.name })
})

const canSubmit = computed(() => {
  if (store.testing) return false
  return messages.value.some((m) => m.content.trim().length > 0)
})

const resultTagType = computed(() => {
  const fr = result.value?.finish_reason
  if (fr === 'stop') return 'success'
  if (fr === 'length' || fr === 'max_tokens') return 'warning'
  return 'info'
})

function addMessage(): void {
  messages.value.push({ id: newMessageId(), role: 'user', content: '' })
}

function removeMessage(id: number): void {
  const idx = messages.value.findIndex(m => m.id === id)
  if (idx >= 0) {
    messages.value.splice(idx, 1)
  }
}

function resetMessages(): void {
  messages.value = [{ id: newMessageId(), role: 'user', content: t('settings.testDialog.defaultGreeting') }]
  result.value = null
  errorMsg.value = ''
}

async function runTest(): Promise<void> {
  if (!props.provider) return
  if (!canSubmit.value) return

  result.value = null
  errorMsg.value = ''

  const payload: ChatTestRequest = {
    messages: messages.value
      .filter((m) => m.content.trim().length > 0)
      .map((m) => ({ role: m.role, content: m.content })),
    max_tokens: params.max_tokens,
    temperature: params.temperature,
  }
  if (params.model.trim()) {
    payload.model = params.model.trim()
  }

  try {
    const resp = await store.testChat(props.provider.provider_id, payload)
    if (resp) {
      result.value = resp
    } else {
      errorMsg.value = t('settings.testDialog.errorInvokeFailed')
    }
  } catch (e: unknown) {
    errorMsg.value = (e as Error)?.message ?? t('settings.testDialog.errorTestFailed')
  }
}

function onOpen(): void {
  // 打开时重置一次状态
  result.value = null
  errorMsg.value = ''
  params.model = ''
  if (messages.value.length === 0) {
    resetMessages()
  }
}

function onClosed(): void {
  // 关闭后清理
  result.value = null
  errorMsg.value = ''
}

function onVisibleChange(val: boolean): void {
  emit('update:visible', val)
}
</script>

<style scoped>
.empty-state {
  padding: 24px 0;
}

.provider-meta {
  margin-bottom: 16px;
}

.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}

.section-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 8px;
  display: flex;
  align-items: center;
}

.messages-area {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 16px;
  padding: 12px;
  background: var(--bg-50);
  border-radius: var(--radius-md);
  border: 1px solid var(--bg-100);
}

.message-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.params-area {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px;
  background: var(--bg-50);
  border-radius: var(--radius-md);
  border: 1px solid var(--bg-100);
  margin-bottom: 16px;
}

.param-item {
  display: flex;
  align-items: center;
  gap: 12px;
}

.param-label {
  width: 90px;
  font-size: 13px;
  color: var(--text-secondary);
  font-weight: 500;
  flex-shrink: 0;
}

.action-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.result-area {
  border-top: 1px solid var(--bg-100);
  padding-top: 12px;
}

.result-latency {
  margin-left: 12px;
  font-size: 12px;
  color: var(--text-tertiary);
  font-weight: 400;
}

.result-content {
  padding: 12px;
  background: var(--bg-50);
  border-radius: var(--radius-md);
  border: 1px solid var(--bg-100);
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--text-primary);
  white-space: pre-wrap;
  word-break: break-word;
  min-height: 60px;
  max-height: 240px;
  overflow-y: auto;
  margin-bottom: 12px;
}

.result-meta {
  margin-top: 8px;
}
</style>
