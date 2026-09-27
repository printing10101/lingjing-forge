<template>
  <div class="process-understanding">
    <!-- 顶部标题栏 -->
    <header class="pu-header">
      <div class="pu-title">
        <h1>{{ t('processUnderstanding.pageTitle') }}</h1>
        <span class="pu-subtitle">{{ t('processUnderstanding.pageSubtitle') }}</span>
      </div>
      <div class="pu-status">
        <el-tag
          :type="store.isHealthy ? 'success' : 'danger'"
          effect="light"
          size="small"
        >
          <el-icon><component :is="store.isHealthy ? CircleCheck : CircleClose" /></el-icon>
          {{ store.isHealthy ? t('processUnderstanding.statusHealthy') : t('processUnderstanding.statusUnavailable') }}
        </el-tag>
        <el-button
          text
          size="small"
          :disabled="!store.hasHistory || store.loading"
          @click="store.clearHistory()"
        >
          <el-icon><Delete /></el-icon>
          {{ t('processUnderstanding.btnClearHistory') }}
        </el-button>
      </div>
    </header>

    <!-- 对话区域 -->
    <main
      ref="conversationRef"
      class="pu-conversation"
    >
      <!-- 空状态 -->
      <div
        v-if="!store.hasHistory"
        class="pu-empty"
      >
        <el-icon
          :size="48"
          color="var(--text-secondary)"
        >
          <ChatDotRound />
        </el-icon>
        <h2>{{ t('processUnderstanding.emptyTitle') }}</h2>
        <p>{{ t('processUnderstanding.emptyHint') }}</p>
        <div class="pu-suggestions">
          <el-button
            v-for="s in suggestions"
            :key="s"
            text
            @click="quickAsk(s)"
          >
            {{ s }}
          </el-button>
        </div>
      </div>

      <!-- 消息列表（拆分子组件 ProcessMessageList） -->
      <div
        v-else
        class="pu-messages"
      >
        <ProcessMessageList
          :messages="store.messages"
          :loading="store.loading"
        />
      </div>
    </main>

    <!-- 底部输入区 -->
    <footer class="pu-input-area">
      <el-input
        v-model="inputText"
        type="textarea"
        :rows="2"
        :placeholder="t('processUnderstanding.inputPlaceholder')"
        resize="none"
        :disabled="store.loading"
        @keydown.enter.exact.prevent="send"
      />
      <el-button
        type="primary"
        :loading="store.loading"
        :disabled="!inputText.trim()"
        @click="send"
      >
        <el-icon><Promotion /></el-icon>
        {{ t('processUnderstanding.btnSend') }}
      </el-button>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, nextTick } from 'vue'
import {
  ChatDotRound, Delete, Promotion,
  CircleCheck, CircleClose,
} from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import { useProcessUnderstandingStore } from '@/stores/processUnderstanding'
import ProcessMessageList from '@/components/process/ProcessMessageList.vue'

const { t } = useI18n()
const store = useProcessUnderstandingStore()

const inputText = ref('')
const conversationRef = ref<HTMLElement | null>(null)

/** 示例问题 */
const suggestions = computed(() => [
  t('processUnderstanding.suggestionMilling'),
  t('processUnderstanding.suggestionToolWear'),
  t('processUnderstanding.suggestionRoughing'),
  t('processUnderstanding.suggestionChatter'),
])

/** 发送消息 */
async function send(): Promise<void> {
  const text = inputText.value.trim()
  if (!text || store.loading) return
  inputText.value = ''
  await store.sendQuery(text)
  await scrollToBottom()
}

/** 快速提问 */
async function quickAsk(q: string): Promise<void> {
  if (store.loading) return
  await store.sendQuery(q)
  await scrollToBottom()
}

/** 滚动到底部 */
async function scrollToBottom(): Promise<void> {
  await nextTick()
  if (conversationRef.value) {
    conversationRef.value.scrollTop = conversationRef.value.scrollHeight
  }
}

onMounted(() => {
  store.refreshHealth()
})
</script>

<style scoped>
.process-understanding {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--bg-secondary);
}

/* 顶部标题栏 */
.pu-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 24px;
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border-lighter);
  flex-shrink: 0;
}

.pu-title h1 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.pu-subtitle {
  font-size: 12px;
  color: var(--text-secondary);
  margin-left: 8px;
}

.pu-status {
  display: flex;
  align-items: center;
  gap: 12px;
}

/* 对话区域 */
.pu-conversation {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  min-height: 0;
}

/* 空状态 */
.pu-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  text-align: center;
  color: var(--text-secondary);
}

.pu-empty h2 {
  margin: 16px 0 8px;
  font-size: 16px;
  color: var(--text-primary);
}

.pu-suggestions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 16px;
}

/* 消息列表（消息内部样式已迁移至 ProcessMessageList.vue） */
.pu-messages {
  display: flex;
  flex-direction: column;
  gap: 16px;
  max-width: 900px;
  margin: 0 auto;
}

/* 底部输入区 */
.pu-input-area {
  display: flex;
  gap: 12px;
  padding: 16px 24px;
  background: var(--bg-primary);
  border-top: 1px solid var(--border-lighter);
  flex-shrink: 0;
  max-width: 900px;
  margin: 0 auto;
  width: 100%;
  box-sizing: border-box;
}

.pu-input-area :deep(.el-textarea__inner) {
  resize: none;
}
</style>
