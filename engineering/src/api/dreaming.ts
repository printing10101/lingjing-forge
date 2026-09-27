// Dreaming 离线反思 API 客户端（W7.2 前端侧）
//
// 对应后端路由：/api/v1/dreaming
// - GET /learnings  「它这周学会了什么」事件流（飞轮看板数据源）
// - GET /status     子系统状态
// 其余写操作端点（reflect/publish/promote/rollback）暂无 UI 消费，
// 需要时按同款模式扩展。

import http from "@/utils/http";
import { API_CONFIG } from "@/config/api";

const BASE = `${API_CONFIG.V1}/dreaming`;

export interface DreamingLearningEvent {
  type: string;
  rule_id: string;
  description?: string;
  confidence?: number;
  from_stage?: string | null;
  to_stage?: string | null;
  traffic_percentage?: number | null;
  reason?: string | null;
  source_insight_category?: string;
  operated_at: string;
}

export interface DreamingLearnings {
  days: number;
  events: DreamingLearningEvent[];
  total: number;
}

export interface DreamingStatus {
  draft_count: number;
  published_count: number;
  stage_counts: Record<string, number>;
  last_reflection: { file: string; modified_at: string } | null;
}

export async function getDreamingLearnings(days = 7) {
  const resp = await http.get(`${BASE}/learnings`, { params: { days } });
  return resp.data;
}

export async function getDreamingStatus() {
  const resp = await http.get(`${BASE}/status`);
  return resp.data;
}
