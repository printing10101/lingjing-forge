/**
 * LLM Provider 类型定义
 * 对应后端 app.ai.llm.provider_base 模块
 */

/** Provider 类型枚举（与后端 ProviderType 对齐） */
export type ProviderType =
  | 'ollama'
  | 'lmstudio'
  | 'llamacpp'
  | 'llama'
  | 'vllm'
  | 'tgi'
  | 'koboldcpp'
  | 'openai'
  | 'anthropic'
  | 'deepseek'
  | 'qwen'
  | 'gemini'
  | 'openai_compatible'

/** Provider 能力标签 */
export type ProviderCapability =
  | 'chat'
  | 'streaming'
  | 'function_calling'
  | 'vision'
  | 'embeddings'

/** Provider 类别（用于 UI 分组） */
export type ProviderCategory = 'local' | 'cloud'

/** Provider 配置（与后端 ProviderConfig 对齐） */
export interface LLMProvider {
  provider_id: string
  name: string
  provider_type: ProviderType
  base_url: string
  /** API Key 是否已设置（不返回明文） */
  api_key_set: boolean
  default_model: string
  timeout: number
  max_retries: number
  retry_delay: number
  enabled: boolean
  is_active: boolean
  priority: number
  capabilities: ProviderCapability[]
  extra: Record<string, unknown>
  last_health_check?: string | null
  last_health_status?: 'healthy' | 'unhealthy' | 'unknown' | null
  last_latency_ms?: number | null
  created_at?: string
  updated_at?: string
}

/** 创建/更新 Provider 请求体 */
export interface ProviderUpsertRequest {
  provider_id: string
  name: string
  provider_type: ProviderType
  base_url?: string
  api_key?: string
  default_model?: string
  timeout?: number
  max_retries?: number
  retry_delay?: number
  enabled?: boolean
  priority?: number
  capabilities?: ProviderCapability[]
  extra?: Record<string, unknown>
}

/** 注册表状态摘要 */
export interface RegistryStatus {
  total: number
  enabled: number
  active_count: number
  local_count: number
  cloud_count: number
  active_provider_id: string | null
  encryption_available: boolean
  db_path: string
}

/** 自动探测结果项 */
export interface DetectedProvider {
  provider_type: ProviderType
  provider_id: string
  name: string
  base_url: string
  detected: boolean
  default_model: string
  capabilities: ProviderCapability[]
  detection_method: string
  detail: string
}

/** 自动探测预览响应 */
export interface DetectPreviewResponse {
  detected: DetectedProvider[]
  scanned_count: number
  detected_count: number
  duration_ms: number
}

/** 健康检查结果 */
export interface HealthCheckResult {
  provider_id: string
  healthy: boolean
  latency_ms: number | null
  status: string
  error?: string | null
  checked_at: string
}

/** 模型列表响应 */
export interface ModelInfo {
  id: string
  name?: string
  owned_by?: string
}

/** 路由策略枚举 */
export type RoutingStrategy =
  | 'active_only'
  | 'priority_fallback'
  | 'capability_match'
  | 'latency_first'
  | 'local_first'
  | 'cloud_first'

/** 路由器状态 */
export interface RouterStatus {
  current_strategy: RoutingStrategy
  active_provider_id: string | null
  available_providers: number
  total_latency_samples: number
  cache_hit_rate: number
  fallback_chain: string[]
}

/** 路由策略描述 */
export interface RoutingStrategyInfo {
  value: RoutingStrategy
  label: string
  description: string
}

/** Provider 类型描述 */
export interface ProviderTypeInfo {
  value: ProviderType
  label: string
  category: ProviderCategory
  default_base_url: string
  default_capabilities: ProviderCapability[]
  needs_api_key: boolean
  description: string
}

/** 调用测试请求体 */
export interface ChatTestRequest {
  messages: Array<{ role: 'system' | 'user' | 'assistant'; content: string }>
  max_tokens?: number
  temperature?: number
  model?: string
}

/** 调用测试响应 */
export interface ChatTestResponse {
  provider_id: string
  content: string
  model: string
  finish_reason: string
  usage: {
    prompt_tokens: number
    completion_tokens: number
    total_tokens: number
  }
  latency_ms: number
}
