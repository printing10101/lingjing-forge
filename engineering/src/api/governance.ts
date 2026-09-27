// AI 治理（主权/审批）API 客户端 —— W9.1 主权接线前端侧
//
// 对应后端路由：/api/v1/governance
// - GET  /sovereignty        读取主权设置（后端为权威数据源）
// - PUT  /sovereignty        部分更新（需 governance:write 权限）
// - POST /sovereignty/reset  恢复默认（推荐模式，等级 2）

import http from "@/utils/http";
import { API_CONFIG } from "@/config/api";

const BASE = `${API_CONFIG.V1}/governance`;

// 与后端 app/services/sovereignty.py SovereigntySettings 字段一一对应
export interface SovereigntySettings {
  ai_autonomy_level: number;
  require_confirmation_for_predict: boolean;
  require_confirmation_for_train: boolean;
  show_confidence_indicator: boolean;
  show_alternatives: boolean;
  show_reasoning: boolean;
}

export interface SovereigntyEnvelope {
  settings: SovereigntySettings;
  autonomy_labels: string[];
  confidence_threshold: number;
  action_types: string[];
  /** "backend" 表示设置已后端持久化（W9.1 起），前端据此显示同步状态 */
  storage: string;
}

export async function getSovereigntySettings() {
  const resp = await http.get(`${BASE}/sovereignty`);
  return resp.data;
}

export async function updateSovereigntySettings(
  updates: Partial<SovereigntySettings>,
) {
  const resp = await http.put(`${BASE}/sovereignty`, updates);
  return resp.data;
}

export async function resetSovereigntySettings() {
  const resp = await http.post(`${BASE}/sovereignty/reset`);
  return resp.data;
}
