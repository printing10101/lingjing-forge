<template>
  <div class="pml-list">
    <div
      v-for="msg in messages"
      :key="msg.id"
      :class="['pml-message', `pml-message-${msg.role}`]"
    >
      <div class="pml-message-avatar">
        <el-icon :size="20">
          <component :is="msg.role === 'user' ? User : Cpu" />
        </el-icon>
      </div>
      <div class="pml-message-body">
        <!-- 用户消息 -->
        <div
          v-if="msg.role === 'user'"
          class="pml-message-content"
        >
          {{ msg.content }}
        </div>

        <!-- 助手消息 -->
        <template v-else>
          <div class="pml-message-content">
            {{ msg.content }}
          </div>

          <!-- 结构化结果展示 -->
          <div
            v-if="msg.result"
            class="pml-result"
          >
            <!-- 任务类型 + 置信度 -->
            <div class="pml-result-meta">
              <el-tag
                size="small"
                type="info"
              >
                {{ taskTypeLabel(msg.result.task_type) }}
              </el-tag>
              <el-tag
                size="small"
                type="primary"
              >
                {{ msg.result.intent }}
              </el-tag>
              <div class="pml-confidence">
                <span class="pml-confidence-label">{{ t('processUnderstanding.confidenceLabel') }}</span>
                <el-progress
                  :percentage="Math.round(msg.result.confidence * 100)"
                  :stroke-width="6"
                  :status="confidenceStatus(msg.result.confidence)"
                  style="width: 120px"
                />
              </div>
            </div>

            <!-- 实体识别 -->
            <div
              v-if="Object.keys(msg.result.entities).length > 0"
              class="pml-entities"
            >
              <span class="pml-section-label">{{ t('processUnderstanding.sectionEntities') }}</span>
              <div class="pml-entity-tags">
                <el-tag
                  v-for="(val, key) in msg.result.entities"
                  :key="key"
                  size="small"
                  effect="plain"
                >
                  {{ key }}: {{ val }}
                </el-tag>
              </div>
            </div>

            <!-- 来源 -->
            <div
              v-if="msg.result.sources.length > 0"
              class="pml-sources"
            >
              <span class="pml-section-label">{{ t('processUnderstanding.sectionSources') }}</span>
              <ul class="pml-source-list">
                <!-- 动态字符串列表，无业务唯一 id，index 作为 key 可接受 -->
                <li
                  v-for="(src, i) in msg.result.sources"
                  :key="`src-${i}`"
                >
                  {{ src }}
                </li>
              </ul>
            </div>

            <!-- 建议动作 -->
            <div
              v-if="msg.result.actions.length > 0"
              class="pml-actions"
            >
              <span class="pml-section-label">{{ t('processUnderstanding.sectionActions') }}</span>
              <ul class="pml-action-list">
                <!-- 动态字符串列表，无业务唯一 id，index 作为 key 可接受 -->
                <li
                  v-for="(act, i) in msg.result.actions"
                  :key="`src-${i}`"
                >
                  {{ act }}
                </li>
              </ul>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- 加载指示器 -->
    <div
      v-if="loading"
      class="pml-loading"
    >
      <el-icon class="pml-loading-icon">
        <Loading />
      </el-icon>
      <span>{{ t('processUnderstanding.loadingThinking') }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 工艺理解消息列表（ProcessUnderstanding 拆分子组件）
 *
 * 纯展示组件：渲染消息列表 + 结构化结果 + 加载指示器。
 * 状态由父组件（store）持有，本组件仅接收 messages/loading props。
 */
import { User, Cpu, Loading } from '@element-plus/icons-vue'
import { useI18n } from 'vue-i18n'
import type { ChatMessage } from '@/stores/processUnderstanding'

defineProps<{
  /** 消息列表（用户 + 助手）。 */
  messages: ChatMessage[]
  /** 是否正在等待回复（显示加载指示器）。 */
  loading: boolean
}>()

const { t } = useI18n()

/** 任务类型标签（与主组件保持一致）。 */
function taskTypeLabel(type: string): string {
  const map: Record<string, string> = {
    A: t('processUnderstanding.taskTypeConsulting'),
    B: t('processUnderstanding.taskTypeDiagnosis'),
    C: t('processUnderstanding.taskTypeSolution'),
    D: t('processUnderstanding.taskTypeKnowledge'),
    E: t('processUnderstanding.taskTypeChat'),
  }
  return map[type] || type || t('processUnderstanding.taskTypeUnknown')
}

/** 置信度 → Element Plus 进度条状态。 */
function confidenceStatus(c: number): 'success' | 'warning' | 'exception' {
  if (c >= 0.7) return 'success'
  if (c >= 0.4) return 'warning'
  return 'exception'
}
</script>

<style scoped>
/* 样式从主组件迁移（.pu-* → .pml-*，保持视觉一致） */
.pml-message {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
}

.pml-message-user {
  flex-direction: row-reverse;
}

.pml-message-avatar {
  flex-shrink: 0;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-50);
  color: var(--text-secondary);
}

.pml-message-user .pml-message-avatar {
  background: var(--brand-50, var(--el-color-primary-light-9));
  color: var(--brand-600, var(--el-color-primary));
}

.pml-message-body {
  max-width: 80%;
}

.pml-message-user .pml-message-body {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
}

.pml-message-content {
  background: var(--bg-50);
  border-radius: 12px;
  padding: 10px 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.pml-message-user .pml-message-content {
  background: var(--brand-500, var(--el-color-primary));
  color: #fff;
}

.pml-result {
  margin-top: 8px;
  background: var(--bg-0);
  border: 1px solid var(--bg-200, var(--el-border-color-light));
  border-radius: 12px;
  padding: 12px;
}

.pml-result-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.pml-confidence {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}

.pml-confidence-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.pml-section-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 6px;
}

.pml-entity-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}

.pml-source-list,
.pml-action-list {
  margin: 0 0 10px;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-primary);
}

.pml-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.pml-loading-icon {
  animation: pml-rotate 1.5s linear infinite;
}

@keyframes pml-rotate {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
