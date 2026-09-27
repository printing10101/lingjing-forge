/**
 * LLM Provider 网关 API 客户端
 *
 * 对应后端路由：/api/v1/llm-providers/*
 */

import http from '@/utils/http'
import { API_CONFIG } from '@/config/api'
import type {
  LLMProvider,
  ProviderUpsertRequest,
  RegistryStatus,
  DetectPreviewResponse,
  HealthCheckResult,
  ModelInfo,
  RouterStatus,
  RoutingStrategyInfo,
  ProviderTypeInfo,
  ProviderCapability,
  ChatTestRequest,
  ChatTestResponse,
  ProviderType,
} from '@/types/llmProvider'

const BASE = API_CONFIG.LLM_PROVIDERS

/** 列出所有 Provider */
export async function listProviders(): Promise<LLMProvider[]> {
  const resp = await http.get(BASE)
  // 后端可能返回 {data: [...]} 或直接返回 [...]
  return normalizeList(resp.data)
}

/** 注册表状态摘要 */
export async function getRegistryStatus(): Promise<RegistryStatus | null> {
  const resp = await http.get(`${BASE}/status`)
  return unwrapData<RegistryStatus | null>(resp.data, null)
}

/** 当前激活 Provider */
export async function getActiveProvider(): Promise<LLMProvider | null> {
  const resp = await http.get(`${BASE}/active`)
  return unwrapData<LLMProvider | null>(resp.data, null)
}

/** 支持的 Provider 类型列表 */
export async function getProviderTypes(): Promise<ProviderTypeInfo[]> {
  const resp = await http.get(`${BASE}/types`)
  return unwrapData<ProviderTypeInfo[]>(resp.data, [])
}

/** 能力标签列表 */
export async function getCapabilities(): Promise<ProviderCapability[]> {
  const resp = await http.get(`${BASE}/capabilities`)
  return unwrapData<ProviderCapability[]>(resp.data, [])
}

/** 获取指定 Provider */
export async function getProvider(providerId: string): Promise<LLMProvider | null> {
  const resp = await http.get(`${BASE}/${encodeURIComponent(providerId)}`)
  return unwrapData<LLMProvider | null>(resp.data, null)
}

/** 列出指定 Provider 的可用模型 */
export async function listProviderModels(providerId: string): Promise<ModelInfo[]> {
  const resp = await http.get(`${BASE}/${encodeURIComponent(providerId)}/models`)
  // 后端可能返回 { data: [] } / 直接数组 / 空对象——统一规整为数组
  const models = unwrapData<ModelInfo[] | Record<string, unknown>>(resp.data, [])
  return Array.isArray(models) ? models : []
}

/** 健康检查指定 Provider */
export async function checkProviderHealth(providerId: string): Promise<HealthCheckResult> {
  const resp = await http.get(`${BASE}/${encodeURIComponent(providerId)}/health`)
  return unwrapData<HealthCheckResult>(resp.data, {} as HealthCheckResult)
}

/** 自动探测预览（不写库） */
export async function previewAutoDetect(): Promise<DetectPreviewResponse> {
  const resp = await http.get(`${BASE}/detect/preview`)
  return resp.data?.data ?? resp.data
}

/** 自动探测并导入到注册表 */
export async function importDetected(): Promise<{ imported: number; activated: string | null }> {
  const resp = await http.post(`${BASE}/detect/import`)
  return resp.data?.data ?? resp.data
}

/** 新增 Provider */
export async function createProvider(payload: ProviderUpsertRequest): Promise<LLMProvider> {
  const resp = await http.post(BASE, payload)
  return resp.data?.data ?? resp.data
}

/** 更新 Provider（部分字段） */
export async function updateProvider(
  providerId: string,
  payload: Partial<ProviderUpsertRequest> & { api_key?: string | null },
): Promise<LLMProvider> {
  const resp = await http.put(`${BASE}/${encodeURIComponent(providerId)}`, payload)
  return resp.data?.data ?? resp.data
}

/** 删除 Provider */
export async function deleteProvider(providerId: string): Promise<void> {
  await http.delete(`${BASE}/${encodeURIComponent(providerId)}`)
}

/** 激活指定 Provider（互斥） */
export async function activateProvider(providerId: string): Promise<LLMProvider> {
  const resp = await http.post(`${BASE}/${encodeURIComponent(providerId)}/activate`)
  return resp.data?.data ?? resp.data
}

/** 启用/禁用 Provider */
export async function setProviderEnabled(
  providerId: string,
  enabled: boolean,
): Promise<LLMProvider> {
  const resp = await http.post(`${BASE}/${encodeURIComponent(providerId)}/enable`, { enabled })
  return resp.data?.data ?? resp.data
}

/** 调用测试 */
export async function testProvider(
  providerId: string,
  payload: ChatTestRequest,
): Promise<ChatTestResponse> {
  const resp = await http.post(`${BASE}/${encodeURIComponent(providerId)}/test`, payload)
  return resp.data?.data ?? resp.data
}

/** 路由器状态 */
export async function getRouterStatus(): Promise<RouterStatus> {
  const resp = await http.get(`${BASE}/router/status`)
  return resp.data?.data ?? resp.data
}

/** 路由策略列表 */
export async function getRoutingStrategies(): Promise<RoutingStrategyInfo[]> {
  const resp = await http.get(`${BASE}/router/strategies`)
  return resp.data?.data ?? resp.data
}

// 工具函数

function normalizeList(raw: unknown): LLMProvider[] {
  if (Array.isArray(raw)) return raw as LLMProvider[]
  if (raw && typeof raw === 'object') {
    const obj = raw as Record<string, unknown>
    if (Array.isArray(obj.data)) return obj.data as LLMProvider[]
    if (Array.isArray(obj.providers)) return obj.providers as LLMProvider[]
  }
  return []
}

/**
 * 统一解包后端响应：优先 { data: T } 包装，兼容旧形状（resp.data 直接是数据）。
 * 与 `?? resp.data` 的区别：data 字段为 null 时（后端明确表示无数据）返回
 * null/默认值，而不是误把整个 { data: null } 包装对象当数据返回。
 */
function unwrapData<T>(respData: unknown, fallback: T): T {
  if (respData && typeof respData === 'object') {
    const obj = respData as Record<string, unknown>
    if ('data' in obj) {
      const d = obj.data
      return d === null || d === undefined ? fallback : (d as T)
    }
  }
  return (respData ?? fallback) as T
}

/**
 * LLM Provider 默认 Base URL 常量
 *
 * 集中管理各 provider 的默认地址，便于统一维护与端口调整。
 * 用户可在运行时通过 ProviderFormDialog 覆盖这些默认值。
 */
const PROVIDER_DEFAULT_BASE_URLS = {
  ollama: 'http://127.0.0.1:11434',
  lmstudio: 'http://127.0.0.1:1234/v1',
  llamacpp: 'http://127.0.0.1:8080/v1',
  llama: 'http://127.0.0.1:8081/v1',
  vllm: 'http://127.0.0.1:8000/v1',
  tgi: 'http://127.0.0.1:8090/v1',
} as const;

/** Provider 类型元信息（本地缓存，便于 UI 不必每次请求） */
export const PROVIDER_TYPE_META: Record<ProviderType, ProviderTypeInfo> = {
  ollama: {
    value: 'ollama',
    label: 'Ollama',
    category: 'local',
    default_base_url: PROVIDER_DEFAULT_BASE_URLS.ollama,
    default_capabilities: ['chat', 'streaming'],
    needs_api_key: false,
    description: '本地 Ollama 服务，桌面版常用，支持模型拉取与管理',
  },
  lmstudio: {
    value: 'lmstudio',
    label: 'LM Studio',
    category: 'local',
    default_base_url: PROVIDER_DEFAULT_BASE_URLS.lmstudio,
    default_capabilities: ['chat', 'streaming'],
    needs_api_key: false,
    description: 'LM Studio 桌面应用，OpenAI 兼容 API',
  },
  llamacpp: {
    value: 'llamacpp',
    label: 'llama.cpp',
    category: 'local',
    default_base_url: PROVIDER_DEFAULT_BASE_URLS.llamacpp,
    default_capabilities: ['chat'],
    needs_api_key: false,
    description: 'llama.cpp HTTP 服务器，OpenAI 兼容 API',
  },
  llama: {
    value: 'llama',
    label: 'Llama',
    category: 'local',
    default_base_url: PROVIDER_DEFAULT_BASE_URLS.llama,
    default_capabilities: ['chat', 'streaming'],
    needs_api_key: false,
    description: 'Llama 模型（llama-server 本地推理栈，OpenAI 兼容 API；服务端启用 --api-key-file 时需配置 Key）',
  },
  vllm: {
    value: 'vllm',
    label: 'vLLM',
    category: 'local',
    default_base_url: PROVIDER_DEFAULT_BASE_URLS.vllm,
    default_capabilities: ['chat', 'streaming'],
    needs_api_key: false,
    description: 'vLLM 高性能推理服务器，OpenAI 兼容 API',
  },
  tgi: {
    value: 'tgi',
    label: 'TGI',
    category: 'local',
    default_base_url: PROVIDER_DEFAULT_BASE_URLS.tgi,
    default_capabilities: ['chat', 'streaming'],
    needs_api_key: false,
    description: 'HuggingFace Text Generation Inference',
  },
  koboldcpp: {
    value: 'koboldcpp',
    label: 'KoboldCpp',
    category: 'local',
    default_base_url: 'http://127.0.0.1:5001/v1',
    default_capabilities: ['chat'],
    needs_api_key: false,
    description: 'KoboldCpp 桌面应用，OpenAI 兼容 API',
  },
  openai: {
    value: 'openai',
    label: 'OpenAI',
    category: 'cloud',
    default_base_url: 'https://api.openai.com/v1',
    default_capabilities: ['chat', 'streaming', 'function_calling', 'vision'],
    needs_api_key: true,
    description: 'OpenAI GPT 系列，需要 API Key',
  },
  anthropic: {
    value: 'anthropic',
    label: 'Anthropic Claude',
    category: 'cloud',
    default_base_url: 'https://api.anthropic.com/v1',
    default_capabilities: ['chat', 'streaming', 'vision'],
    needs_api_key: true,
    description: 'Anthropic Claude 系列，专属协议',
  },
  deepseek: {
    value: 'deepseek',
    label: 'DeepSeek',
    category: 'cloud',
    default_base_url: 'https://api.deepseek.com/v1',
    default_capabilities: ['chat', 'streaming'],
    needs_api_key: true,
    description: 'DeepSeek 深度求索，OpenAI 兼容 API',
  },
  qwen: {
    value: 'qwen',
    label: '通义千问',
    category: 'cloud',
    default_base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    default_capabilities: ['chat', 'streaming'],
    needs_api_key: true,
    description: '阿里云通义千问，OpenAI 兼容 API',
  },
  gemini: {
    value: 'gemini',
    label: 'Google Gemini',
    category: 'cloud',
    default_base_url: 'https://generativelanguage.googleapis.com/v1beta',
    default_capabilities: ['chat', 'vision'],
    needs_api_key: true,
    description: 'Google Gemini，专属协议',
  },
  openai_compatible: {
    value: 'openai_compatible',
    label: 'OpenAI 兼容（自定义）',
    category: 'cloud',
    default_base_url: '',
    default_capabilities: ['chat'],
    needs_api_key: false,
    description: '任何兼容 OpenAI API 的服务（如自建代理、第三方平台）',
  },
}

/** 路由策略元信息（本地缓存） */
export const ROUTING_STRATEGY_META: RoutingStrategyInfo[] = [
  {
    value: 'active_only',
    label: '仅激活 Provider',
    description: '只使用当前激活的 Provider，失败不降级',
  },
  {
    value: 'priority_fallback',
    label: '优先级降级',
    description: '按 priority 降序尝试，前一个失败时降级到下一个',
  },
  {
    value: 'capability_match',
    label: '能力匹配',
    description: '根据请求所需能力选择 Provider（如 vision/function_calling）',
  },
  {
    value: 'latency_first',
    label: '延迟优先',
    description: '优先选择历史平均延迟最低的 Provider',
  },
  {
    value: 'local_first',
    label: '本地优先',
    description: '优先使用本地 Provider，失败时降级到云端',
  },
  {
    value: 'cloud_first',
    label: '云端优先',
    description: '优先使用云端 Provider，失败时降级到本地',
  },
]
