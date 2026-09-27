// 系统信息与更新检查 API 客户端
//
// 对应后端路由：/api/v1/system 下的 /version 与 /update-check
// - version       当前版本（version + commit）
// - update-check  检查 GitHub Releases 最新版本（自动更新过渡方案）

import http from '@/utils/http'
import { API_CONFIG } from '@/config/api'

const BASE = API_CONFIG.SYSTEM

export interface VersionInfo {
  version: string
  commit: string
}

export interface UpdateCheckResult {
  current_version: string
  latest_version: string | null
  update_available: boolean
  latest_release_url: string | null
  checked_at: string
  error: string | null
}

// 获取当前系统版本信息
export async function getSystemVersion(): Promise<VersionInfo> {
  const resp = await http.get(`${BASE}/version`)
  return resp.data?.data ?? resp.data
}

// 检查 GitHub Releases 是否有新版本（网络失败 fail-soft，error 字段返回短代码）
export async function checkForUpdates(): Promise<UpdateCheckResult> {
  const resp = await http.get(`${BASE}/update-check`)
  return resp.data?.data ?? resp.data
}
