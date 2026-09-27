// 世界模型 API 客户端（W6 物理预演卡前端侧）
//
// 对应后端路由：/api/v1/world-model
// - POST /preview  物理预演（轨迹预测 + 阈值带 + 安全盾裁决 + 主权决策）

import http from "@/utils/http";
import { API_CONFIG } from "@/config/api";

const BASE = `${API_CONFIG.V1}/world-model`;

export interface PreviewBands {
  chatter_probability: { safe_below: number; danger_at: number };
  tool_wear: { danger_at: number };
  model_confidence: { floor: number };
}

export interface PreviewMetrics {
  max_chatter_probability: number;
  mean_chatter_probability: number;
  cumulative_tool_wear: number;
  final_surface_roughness: number;
  confidence_mean: number;
}

export interface PreviewStep {
  step: number;
  predicted_state: Record<string, number>;
  chatter_probability: number;
  tool_wear_increment: number;
  surface_roughness: number;
  confidence: number;
}

export interface PhysicalPreview {
  verdict: "safe" | "warning" | "danger";
  reasons: string[];
  metrics: PreviewMetrics;
  bands: PreviewBands;
  trajectory: PreviewStep[];
  shield: {
    action: number[];
    original_action: number[];
    violated: boolean;
    violations: string[];
    fallback_used: boolean;
  };
  conservatism: { level: number; factor: number; scaled: boolean };
  final_action: Record<string, number>;
  sovereignty: {
    requires_confirmation: boolean;
    autonomy_label: string;
    reason: string;
  };
}

export interface PhysicalPreviewRequest {
  current_state: Record<string, number>;
  candidate_action: Record<string, number>;
  horizon?: number;
}

export async function getPhysicalPreview(payload: PhysicalPreviewRequest) {
  const resp = await http.post(`${BASE}/preview`, payload);
  return resp.data;
}
