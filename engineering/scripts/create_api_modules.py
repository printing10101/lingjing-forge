"""Batch create Feature API service modules."""
from pathlib import Path

FEATURES = Path(__file__).resolve().parent.parent / "src" / "features"

modules = {
    "goals/api.ts": '''/** Goals — API service layer */
import http from '@/utils/http'
import { API_CONFIG, buildApiPath } from '@/config/api'

export interface Goal { id: string; title: string; status: string; [key: string]: unknown }

export async function fetchGoals(): Promise<Goal[]> {
  const res = await http.get(buildApiPath(API_CONFIG.GOALS, '/list'))
  return res.data.data
}
export async function createGoal(data: Record<string, unknown>): Promise<Goal> {
  const res = await http.post(buildApiPath(API_CONFIG.GOALS, '/create'), data)
  return res.data.data
}
export async function updateGoalProgress(id: string, progress: number): Promise<void> {
  await http.put(buildApiPath(API_CONFIG.GOALS, `/${id}/progress`), { progress })
}
export async function deleteGoal(id: string): Promise<void> {
  await http.delete(buildApiPath(API_CONFIG.GOALS, `/${id}`))
}
''',
    "home/api.ts": '''/** Home — API service layer */
import http from '@/utils/http'
import { API_CONFIG, buildApiPath } from '@/config/api'

export interface SystemStatus { version: string; uptime: number }
export interface ActivityBrief { alerts: unknown[]; recent: unknown[] }

export async function fetchSystemStatus(): Promise<SystemStatus> {
  const res = await http.get(buildApiPath(API_CONFIG.V1, '/system/status'))
  return res.data.data
}
export async function fetchActivityBrief(): Promise<ActivityBrief> {
  const res = await http.get(buildApiPath(API_CONFIG.V1, '/activity/brief'))
  return res.data.data
}
''',
    "plugin-logs/api.ts": '''/** Plugin Logs — API service layer */
import http from '@/utils/http'
import { API_CONFIG, buildApiPath } from '@/config/api'

export interface PluginLog { id: string; level: string; message: string; timestamp: string }

export async function fetchPluginLogs(params?: Record<string, unknown>): Promise<PluginLog[]> {
  const res = await http.get(buildApiPath(API_CONFIG.PLUGINS, '/logs'), { params })
  return res.data.data
}
''',
    "template-market/api.ts": '''/** Template Market — API service layer */
import http from '@/utils/http'
import { API_CONFIG } from '@/config/api'

export interface Template { id: string; name: string; version: string; category: string }
export interface TemplateDetail { id: string; name: string; content: unknown }

export async function fetchTemplates(params?: Record<string, unknown>): Promise<Template[]> {
  const res = await http.get(API_CONFIG.TEMPLATES, { params })
  return res.data.data
}
export async function fetchTemplate(id: string): Promise<TemplateDetail> {
  const res = await http.get(`${API_CONFIG.TEMPLATES}/${id}`)
  return res.data.data
}
export async function installTemplate(id: string): Promise<void> {
  await http.post(`${API_CONFIG.TEMPLATES}/${id}/install`)
}
export async function previewTemplate(id: string): Promise<TemplateDetail> {
  const res = await http.get(`${API_CONFIG.TEMPLATES}/${id}/preview`)
  return res.data.data
}
''',
    "simulation/api.ts": '''/** Simulation View — API service layer */
import http from '@/utils/http'
import { API_CONFIG, buildApiPath } from '@/config/api'

export interface SimRequest { gcode_path: string; stock_path: string; tool: Record<string, unknown> }
export interface SimTask { task_id: string; status: string }

export async function submitSimulation(req: SimRequest): Promise<SimTask> {
  const res = await http.post(buildApiPath(API_CONFIG.SIMULATION, '/run/async'), req)
  return res.data.data
}
export async function getSimulationStatus(taskId: string): Promise<SimTask> {
  const res = await http.get(buildApiPath(API_CONFIG.SIMULATION, `/status/${taskId}`))
  return res.data.data
}
export async function getHistory(projectId?: string): Promise<SimTask[]> {
  const params = projectId ? { project_id: projectId } : {}
  const res = await http.get(buildApiPath(API_CONFIG.SIMULATION, '/history'), { params })
  return res.data.data
}
''',
    "cost-dashboard/api.ts": '''/** Cost Dashboard — API service layer */
import http from '@/utils/http'
import { API_CONFIG, buildApiPath } from '@/config/api'

export interface CostPolicy { id: string; name: string; limit: number; current: number }
export interface CostSummary { total: number; by_category: Record<string, number> }

export async function fetchPolicies(): Promise<CostPolicy[]> {
  const res = await http.get(buildApiPath(API_CONFIG.COST_BUDGET, '/policies'))
  return res.data.data
}
export async function fetchSummary(params?: Record<string, unknown>): Promise<CostSummary> {
  const res = await http.get(buildApiPath(API_CONFIG.COST_BUDGET, '/summary'), { params })
  return res.data.data
}
''',
    "branch-manager/api.ts": '''/** Branch Manager — API service layer */
import http from '@/utils/http'
import { API_CONFIG } from '@/config/api'

export interface Branch { id: string; name: string; base_version: string }

export async function fetchBranches(url: string): Promise<Branch[]> {
  const res = await http.get(url)
  return res.data.data
}
export async function createBranch(data: Record<string, unknown>): Promise<Branch> {
  const res = await http.post(`${API_CONFIG.V1}/templates/branches`, data)
  return res.data.data
}
export async function mergeBranch(sourceId: string): Promise<void> {
  await http.post(`${API_CONFIG.V1}/templates/branches/${sourceId}/merge`)
}
''',
    "task-history/api.ts": '''/** Task History — API service layer */
import http from '@/utils/http'
import { API_CONFIG, buildApiPath } from '@/config/api'

export interface JobRecord { id: string; type: string; status: string; created_at: string }

export async function fetchJobs(params?: Record<string, unknown>): Promise<JobRecord[]> {
  const res = await http.get(API_CONFIG.JOBS, { params })
  return res.data.data
}
export async function resubmitTraining(config: Record<string, unknown>): Promise<JobRecord> {
  const res = await http.post(buildApiPath(API_CONFIG.LNN, '/train'), config)
  return res.data.data
}
export async function resubmitInference(config: Record<string, unknown>): Promise<JobRecord> {
  const res = await http.post(buildApiPath(API_CONFIG.LNN, '/batch-inference'), config)
  return res.data.data
}
''',
}

for path, content in modules.items():
    f = FEATURES / path
    f.write_text(content, encoding="utf-8")
    print(f"Created {path}  ({len(content.splitlines())} lines)")

print(f"\nDone: {len(modules)} modules")
