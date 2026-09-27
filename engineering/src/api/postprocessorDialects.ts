// 后处理器方言管理 API 客户端（P3）
//
// 对应后端路由：/api/v1/postprocessor/dialects
// - GET  /                         列出方言（内置 + 声明镜像）
// - GET  /{id}                     方言详情
// - POST /template                 读取模板内容
// - POST /preview                  NC 输出预览（杀手锏：工艺员改模板立刻看到效果）

import http from '@/utils/http'
import { API_CONFIG } from '@/config/api'

const BASE = `${API_CONFIG.V1}/postprocessor/dialects`

export interface DialectInfo {
  id: string
  name: string
  version: string
  extends: string | null
  source: 'builtin' | 'declared'
  template_methods: string[]
  params_keys: string[]
  hooks: string | null
  author: string
  description: string
  /** 详情接口专用 */
  is_declared?: boolean
  compile_ok?: boolean
  compile_error?: string | null
  templates?: Record<string, string>
}

export interface DialectListResult {
  dialects: DialectInfo[]
  total: number
  declared: number
  compile_errors: Record<string, string>
}

export interface TemplateContent {
  dialect_id: string
  method: string
  path: string
  content: string
}

export interface PreviewResult {
  dialect_id: string
  program_number: number
  output: string
}

/** 列出所有方言（内置 + 声明镜像） */
export async function listDialects(): Promise<DialectListResult> {
  const resp = await http.get(BASE)
  return resp.data?.data ?? resp.data
}

/** 获取方言详情 */
export async function getDialectDetail(dialectId: string): Promise<DialectInfo> {
  const resp = await http.get(`${BASE}/${encodeURIComponent(dialectId)}`)
  return resp.data?.data ?? resp.data
}

/** 读取方言模板内容 */
export async function readTemplate(
  dialectId: string,
  method: string,
): Promise<TemplateContent> {
  const resp = await http.post(`${BASE}/template`, { dialect_id: dialectId, method })
  return resp.data?.data ?? resp.data
}

/** NC 输出预览 */
export async function previewDialect(
  dialectId: string,
  programNumber = 1000,
): Promise<PreviewResult> {
  const resp = await http.post(`${BASE}/preview`, {
    dialect_id: dialectId,
    program_number: programNumber,
  })
  return resp.data?.data ?? resp.data
}

/** 新建声明式方言（创建目录 + dialect.yaml + 骨架模板） */
export async function createDialect(req: {
  id: string
  name: string
  extends: string
  description?: string
  author?: string
}): Promise<{ id: string; name: string; extends: string }> {
  const resp = await http.post(BASE, req)
  return resp.data?.data ?? resp.data
}

/** 保存方言模板内容 */
export async function saveTemplate(
  dialectId: string,
  method: string,
  content: string,
): Promise<{ dialect_id: string; method: string; path: string }> {
  const resp = await http.put(`${BASE}/${encodeURIComponent(dialectId)}/template`, {
    dialect_id: dialectId,
    method,
    content,
  })
  return resp.data?.data ?? resp.data
}

/** 删除声明式方言 */
export async function deleteDialect(
  dialectId: string,
): Promise<{ id: string }> {
  const resp = await http.delete(`${BASE}/${encodeURIComponent(dialectId)}`)
  return resp.data?.data ?? resp.data
}

/** 读取方言参数（有效配置 + 方言自己的参数） */
export async function getDialectParams(dialectId: string): Promise<{
  dialect_id: string
  effective: Record<string, unknown>
  dialect_params: Record<string, unknown>
  base_keys: string[]
}> {
  const resp = await http.get(
    `${BASE}/${encodeURIComponent(dialectId)}/params`,
  )
  return resp.data?.data ?? resp.data
}

/** 保存方言参数（写回 dialect.yaml 的 params 段） */
export async function saveDialectParams(
  dialectId: string,
  params: Record<string, unknown>,
): Promise<{ dialect_id: string; params: Record<string, unknown> }> {
  const resp = await http.put(
    `${BASE}/${encodeURIComponent(dialectId)}/params`,
    { dialect_id: dialectId, params },
  )
  return resp.data?.data ?? resp.data
}
