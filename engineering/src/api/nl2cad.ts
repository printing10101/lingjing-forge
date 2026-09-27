/**
 * NL2CAD 自然语言建模 API 客户端
 *
 * 对应后端路由：/api/v1/nl2cad/*
 *
 * 统一替换原先散落在 NLModeling.vue / WorkflowGuide.vue / NLInputPanel.vue
 * 中的 fetch() 调用，解决以下断点：
 * - 断点 C：前端绕过 @/utils/http 统一客户端（无 token、无错误拦截）
 * - 断点 D：路径拼接不一致（NLInputPanel 用 V1/nl2cad/...，其他用 NL2CAD/...）
 * - 断点 F：NL2CAD 无独立 API 客户端模块
 */

import http from "@/utils/http";
import { API_CONFIG, buildApiPath } from "@/config/api";

const BASE = API_CONFIG.NL2CAD;

/** 参数提取请求 */
export interface ExtractParamsRequest {
  description: string;
}

/** 参数提取响应（后端可能返回 { params } 或直接展开字段） */
export interface ExtractParamsResponse {
  params: Record<string, unknown>;
  [key: string]: unknown;
}

/** 模型生成请求 */
export interface GenerateModelRequest {
  description: string;
  output_format?: "stl" | "step" | "obj";
}

/** 模型生成响应 */
export interface GenerateModelResponse {
  model_path: string;
  params?: Record<string, unknown>;
  /** 等价参数化 CadQuery 模板脚本（顶层命名尺寸变量），供滑杆调参重执行 */
  script?: string;
  /** 脚本可调参数表（参数名 → 当前值 mm）；为空表示脚本不可参数化 */
  parameters?: Record<string, number>;
}

/** 滑杆调参重执行请求（不调用 LLM） */
export interface RegenerateParamsRequest {
  script: string;
  params: Record<string, number>;
  output_format?: "stl" | "step" | "obj" | "gltf";
  task_id?: string;
}

/** 滑杆调参重执行响应 */
export interface RegenerateParamsResponse {
  model_path: string;
  params: Record<string, number>;
}

/** 工艺规划请求 */
export interface ProcessPlanningRequest {
  cad_params: Record<string, unknown>;
  material?: string;
  machine_type?: string;
  precision?: string;
}

/** 工艺规划响应 */
export interface ProcessPlanningResponse {
  process_plan: Record<string, unknown>;
}

/** NC 代码生成请求 */
export interface GenerateNCRequest {
  process_plan: Record<string, unknown>;
  machine_type?: string;
}

/** NC 代码生成响应 */
export interface GenerateNCResponse {
  nc_code: string;
}

/**
 * 从自然语言描述提取 CAD 参数
 * 后端端点：POST /api/v1/nl2cad/extract-params
 */
export async function extractParams(
  payload: ExtractParamsRequest,
): Promise<ExtractParamsResponse> {
  const resp = await http.post(buildApiPath(BASE, "extract-params"), payload);
  // 兼容 { data: { params } } / { params } / 直接展开三种返回结构；
  // 注意：data 字段为 null 时不能回退到整个 { data: null } 包装（null ?? 不生效）
  let body: unknown = resp.data;
  if (body && typeof body === "object" && "data" in body) {
    body = (body as Record<string, unknown>).data;
  }
  if (body && typeof body === "object" && "params" in body) {
    return body as ExtractParamsResponse;
  }
  return { params: (body as Record<string, unknown>) ?? {} };
}

/**
 * 根据描述生成 3D 模型
 * 后端端点：POST /api/v1/nl2cad/generate
 */
export async function generateModel(
  payload: GenerateModelRequest,
): Promise<GenerateModelResponse> {
  const resp = await http.post(buildApiPath(BASE, "generate"), payload);
  return resp.data?.data ?? resp.data;
}

/**
 * 按参数覆写重新执行脚本生成模型（不调用 LLM，亚秒级）
 * 后端端点：POST /api/v1/nl2cad/regenerate-params
 *
 * 典型用法：generateModel 返回 script + parameters 后，前端渲染滑杆；
 * 用户拖动滑杆调用本方法，零推理成本拿到新模型。
 */
export async function regenerateModelParams(
  payload: RegenerateParamsRequest,
): Promise<RegenerateParamsResponse> {
  const resp = await http.post(
    buildApiPath(BASE, "regenerate-params"),
    payload,
  );
  return resp.data?.data ?? resp.data;
}

/**
 * 基于 CAD 参数生成工艺规划
 * 后端端点：POST /api/v1/nl2cad/process-planning
 */
export async function generateProcessPlanning(
  payload: ProcessPlanningRequest,
): Promise<ProcessPlanningResponse> {
  const resp = await http.post(buildApiPath(BASE, "process-planning"), payload);
  return resp.data?.data ?? resp.data;
}

/**
 * 根据工艺规划生成 NC 代码
 * 后端端点：POST /api/v1/nl2cad/generate-nc
 */
export async function generateNC(
  payload: GenerateNCRequest,
): Promise<GenerateNCResponse> {
  const resp = await http.post(buildApiPath(BASE, "generate-nc"), payload);
  return resp.data?.data ?? resp.data;
}

/**
 * 导出仿真动画（返回 Blob，用于浏览器下载）
 * 后端端点：POST /api/simulation/export-animation
 *
 * 注意：该端点属于仿真模块而非 NL2CAD，但为方便调用统一放在此处。
 */
export async function exportSimulationAnimation(payload: {
  nc_code: string;
  format?: "gif" | "mp4";
}): Promise<Blob> {
  const resp = await http.post(
    buildApiPath(API_CONFIG.SIMULATION, "export-animation"),
    payload,
    { responseType: "blob" },
  );
  return resp.data as Blob;
}
