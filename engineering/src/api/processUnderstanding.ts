/**
 * 工艺理解与知识问答 API 客户端
 *
 * 对应后端路由：/api/process-understanding/*
 * - POST /query    工艺理解主接口
 * - POST /explain  模型预测结果解释
 * - GET  /stats    模块统计信息
 * - GET  /health   健康检查
 */

import http from '@/utils/http'
import { API_CONFIG } from '@/config/api'

const BASE = API_CONFIG.PROCESS_UNDERSTANDING

/** 工艺理解查询请求 */
export interface QueryRequest {
  query: string
  context?: Record<string, unknown>
}

/** 工艺理解查询响应 */
export interface QueryResponse {
  task_type: string
  intent: string
  entities: Record<string, string>
  response: string
  confidence: number
  sources: string[]
  actions: string[]
  details: Record<string, unknown>
}

/** 预测解释请求 */
export interface ExplainRequest {
  force_pred?: number
  force_conf?: number
  wear_pred?: number
  wear_conf?: number
  visual_status?: string
  anomaly_prob?: number
  context?: string
}

/** 模块统计信息 */
export interface ProcessUnderstandingStats {
  total_requests?: number
  avg_latency_ms?: number
  [key: string]: unknown
}

/** 健康检查响应 */
export interface HealthStatus {
  status: string
  total_requests: number
  avg_latency_ms: number
}

/** 工艺理解主接口 */
export async function query(input: string | QueryRequest): Promise<QueryResponse> {
  const payload: QueryRequest =
    typeof input === 'string' ? { query: input } : input
  const resp = await http.post(`${BASE}/query`, payload)
  return resp.data?.data ?? resp.data
}

/** 模型预测结果解释 */
export async function explainPrediction(req: ExplainRequest): Promise<QueryResponse> {
  const resp = await http.post(`${BASE}/explain`, req)
  return resp.data?.data ?? resp.data
}

/** 模块统计信息 */
export async function getStats(): Promise<ProcessUnderstandingStats> {
  const resp = await http.get(`${BASE}/stats`)
  return resp.data?.data ?? resp.data
}

/** 健康检查 */
export async function checkHealth(): Promise<HealthStatus> {
  const resp = await http.get(`${BASE}/health`)
  return resp.data?.data ?? resp.data
}
