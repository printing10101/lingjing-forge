/**
 * 工艺理解 Pinia Store
 *
 * 管理工艺理解对话历史、加载状态、健康状态。
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  query,
  checkHealth,
  getStats,
  type QueryResponse,
  type HealthStatus,
  type ProcessUnderstandingStats,
} from '@/api/processUnderstanding'
import { extractErrorMessage } from '@/utils/error-handler'

/** 对话消息类型 */
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** 工艺理解结构化结果（仅 assistant 消息） */
  result?: QueryResponse
  timestamp: number
}

export const useProcessUnderstandingStore = defineStore('processUnderstanding', () => {
  // 状态
  const messages = ref<ChatMessage[]>([])
  const loading = ref(false)
  const health = ref<HealthStatus | null>(null)
  const stats = ref<ProcessUnderstandingStats | null>(null)
  const lastError = ref<string | null>(null)

  // 计算属性
  const messageCount = computed(() => messages.value.length)
  const isHealthy = computed(() => health.value?.status === 'healthy')
  const hasHistory = computed(() => messages.value.length > 0)

  /** 生成消息 ID */
  function genId(): string {
    return `msg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
  }

  /** 发送工艺理解查询 */
  async function sendQuery(userInput: string): Promise<QueryResponse | null> {
    if (!userInput.trim() || loading.value) return null

    // 添加用户消息
    const userMsg: ChatMessage = {
      id: genId(),
      role: 'user',
      content: userInput.trim(),
      timestamp: Date.now(),
    }
    messages.value.push(userMsg)
    loading.value = true
    lastError.value = null

    try {
      const result = await query(userInput)

      // 添加助手回复
      const assistantMsg: ChatMessage = {
        id: genId(),
        role: 'assistant',
        content: result.response || '(无回复内容)',
        result,
        timestamp: Date.now(),
      }
      messages.value.push(assistantMsg)

      return result
    } catch (err) {
      const msg = extractErrorMessage(err)
      lastError.value = msg

      // 添加错误回复
      const errorMsg: ChatMessage = {
        id: genId(),
        role: 'assistant',
        content: `⚠️ 工艺理解服务暂时不可用：${msg}`,
        timestamp: Date.now(),
      }
      messages.value.push(errorMsg)

      return null
    } finally {
      loading.value = false
    }
  }

  /** 检查健康状态 */
  async function refreshHealth(): Promise<void> {
    try {
      health.value = await checkHealth()
    } catch (err) {
      health.value = {
        status: 'unhealthy',
        total_requests: 0,
        avg_latency_ms: 0,
      }
      lastError.value = extractErrorMessage(err)
    }
  }

  /** 刷新统计信息 */
  async function refreshStats(): Promise<void> {
    try {
      stats.value = await getStats()
    } catch (err) {
      lastError.value = extractErrorMessage(err)
    }
  }

  /** 清空对话历史 */
  function clearHistory(): void {
    messages.value = []
    lastError.value = null
  }

  return {
    // 状态
    messages,
    loading,
    health,
    stats,
    lastError,
    // 计算属性
    messageCount,
    isHealthy,
    hasHistory,
    // 动作
    sendQuery,
    refreshHealth,
    refreshStats,
    clearHistory,
  }
})
