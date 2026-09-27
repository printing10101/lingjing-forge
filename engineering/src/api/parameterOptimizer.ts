// 参数优化（Phase D）API 客户端
//
// 对应后端路由：/api/v1/optimizer
// - POST /recommend       参数推荐（L0/L1 分层 + 物理安全钳制）
// - POST /evaluate        单条实测结果评估（0-1 得分）
// - POST /compare         A/B 两组结果对比（提升率）
// - GET  /baselines      基线参数库（L0 经验表）

import http from '@/utils/http'
import { API_CONFIG } from '@/config/api'

const BASE = `${API_CONFIG.V1}/optimizer`

// 类型定义（与后端 app/api/v1/optimizer_routes.py 对齐）

export type OptimizationTarget = 'balanced' | 'cycle_time' | 'tool_life' | 'surface'

export interface Recommendation {
  depth_of_cut_mm: number
  feed_mm_per_rev: number
  spindle_rpm: number
  cutting_speed_m_min: number
  strategy: 'L0_baseline' | 'L1_statistical' | 'L2_model' | 'L3_bayesian'
  confidence: number
  basis: Array<Record<string, unknown>>
  clamped: boolean
}

export interface EvaluationResult {
  score: number
  cycle_time_ok: boolean
  wear_ok: boolean
  roughness_ok: boolean
  result_ok: boolean
  details: {
    cycle_time_s?: number | null
    tool_wear_percent?: number | null
    surface_roughness_ra?: number | null
    result: string
  }
}

export interface ComparisonResult {
  better: 'a' | 'b' | 'tie'
  improvement_pct: number
  a_samples: number
  b_samples: number
  a_avg_cycle?: number | null
  b_avg_cycle?: number | null
  a_avg_wear?: number | null
  b_avg_wear?: number | null
}

export interface BaselineEntryView {
  material: string
  machining_type: string
  tool_material: string
  depth_of_cut_mm: number
  feed_mm_per_rev: number
  spindle_rpm: number
  cutting_speed_m_min: number
}

export interface RecommendRequest {
  material: string
  machining_type?: string
  tool_id?: string
  target?: OptimizationTarget
}

// API 函数

/** 参数推荐（分层策略 + 物理安全钳制） */
export async function recommendParameters(
  req: RecommendRequest,
): Promise<Recommendation> {
  const resp = await http.post(`${BASE}/recommend`, req)
  return resp.data?.data?.recommendation ?? resp.data?.recommendation
}

/** 单条实测结果评估（0-1 得分） */
export async function evaluateResult(
  payload: {
    cycle_time_s?: number | null
    tool_wear_percent?: number | null
    surface_roughness_ra?: number | null
    result?: 'ok' | 'rework' | 'scrap'
  },
): Promise<EvaluationResult> {
  const resp = await http.post(`${BASE}/evaluate`, payload)
  return resp.data?.data ?? resp.data
}

/** A/B 两组结果对比 */
export async function compareResults(
  aResults: Array<Record<string, unknown>>,
  bResults: Array<Record<string, unknown>>,
): Promise<ComparisonResult> {
  const resp = await http.post(`${BASE}/compare`, {
    a_results: aResults,
    b_results: bResults,
  })
  return resp.data?.data ?? resp.data
}

/** 列出基线参数库（支持材料/加工类型过滤） */
export async function listBaselines(params?: {
  material?: string
  machining_type?: string
}): Promise<{ entries: BaselineEntryView[]; total: number }> {
  const resp = await http.get(`${BASE}/baselines`, { params })
  return resp.data?.data ?? resp.data
}
