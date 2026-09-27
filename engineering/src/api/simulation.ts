/**
 * 仿真结果API客户端
 * 负责获取仿真数据、缓存管理和错误处理
 */

import http from '@/utils/http'
import type { SimulationResult, SimulationStatus } from '@/types'
import { API_CONFIG, buildApiPath } from '@/config/api'

/** 仿真结果数据（扩展用于可视化） */
export interface SimulationVisualizationData {
  task_id: string
  force_data?: ForceData[]
  temperature_data?: TemperatureData[]
  vibration_data?: VibrationData[]
  timestamp: number
}

/** 力数据点 */
export interface ForceData {
  position: [number, number, number]
  direction: [number, number, number]
  magnitude: number
  timestamp: number
}

/** 温度数据点 */
export interface TemperatureData {
  position: [number, number, number]
  temperature: number
  timestamp: number
}

/** 振动数据点 */
export interface VibrationData {
  position: [number, number, number]
  amplitude: number
  frequency: number
  timestamp: number
}

/** API响应包装 */
interface ApiResponse<T> {
  data: T
  message?: string
  status?: number
}

/** 缓存配置 */
const CACHE_CONFIG = {
  maxAge: 5 * 60 * 1000, // 5分钟缓存
  maxSize: 50, // 最多缓存50个结果
}

/** 缓存存储 */
const cache = new Map<string, { data: SimulationVisualizationData; timestamp: number }>()

/**
 * 清理过期缓存
 */
function cleanupCache(): void {
  const now = Date.now()
  const expiredKeys: string[] = []

  cache.forEach((value, key) => {
    if (now - value.timestamp > CACHE_CONFIG.maxAge) {
      expiredKeys.push(key)
    }
  })

  expiredKeys.forEach((key) => cache.delete(key))

  // 如果缓存仍然过大，删除最旧的条目
  while (cache.size > CACHE_CONFIG.maxSize) {
    const oldestKey = cache.keys().next().value
    if (oldestKey) {
      cache.delete(oldestKey)
    }
  }
}

/**
 * 获取仿真结果
 * @param taskId 任务ID
 * @param forceRefresh 是否强制刷新（忽略缓存）
 */
export async function getSimulationResult(
  taskId: string,
  forceRefresh = false
): Promise<SimulationVisualizationData> {
  // 检查缓存
  if (!forceRefresh) {
    const cached = cache.get(taskId)
    if (cached && Date.now() - cached.timestamp < CACHE_CONFIG.maxAge) {
      return cached.data
    }
  }

  try {
    // 获取仿真状态和结果
    const statusResponse = await http.get<ApiResponse<SimulationStatus>>(
      buildApiPath(API_CONFIG.SIMULATION, `/status/${taskId}`)
    )

    const status = statusResponse.data.data

    if (status.status !== 'completed' || !status.result) {
      throw new Error(`仿真任务未完成或失败: ${status.status}`)
    }

    // 获取仿真结果详情
    const resultResponse = await http.get<ApiResponse<SimulationResult>>(
      buildApiPath(API_CONFIG.SIMULATION, `/result/${taskId}`)
    )

    const result = resultResponse.data.data

    // 转换为可视化数据格式
    const visualizationData = convertToVisualizationData(result)

    // 更新缓存（先写入再清理，保证新条目不会被误删且 size 不超过上限）
    cache.set(taskId, {
      data: visualizationData,
      timestamp: Date.now(),
    })
    cleanupCache()

    return visualizationData
  } catch {
    throw new Error('获取仿真结果失败')
  }
}

/**
 * 将仿真结果转换为可视化数据
 *
 * 注意：温度场数据和振动数据需要后端提供专用接口。
 * 在后端实现前，这些字段返回空数组，禁止使用 Math.random() 伪造数据，
 * 因为伪造数据会误导用户做出错误的加工决策。
 */
function convertToVisualizationData(result: SimulationResult): SimulationVisualizationData {
  const visualizationData: SimulationVisualizationData = {
    task_id: result.task_id,
    timestamp: Date.now(),
    force_data: [],
    temperature_data: [],
    vibration_data: [],
  }

  // 碰撞位置 → 力矢量（基于真实碰撞检测结果映射）
  if (result.collision_details && result.collision_details.positions.length > 0) {
    const magnitude = result.collision_details.severity === 'critical' ? 1000 : 500
    visualizationData.force_data = result.collision_details.positions.map((pos) => ({
      position: pos,
      // 碰撞力方向默认沿 Z 轴负向（刀具进给方向）
      direction: [0, 0, -1] as [number, number, number],
      magnitude,
      timestamp: Date.now(),
    }))
  }

  // 温度场数据：后端暂未提供温度场接口，返回空数组
  // 若需启用，应新增 GET /api/v1/simulation/{task_id}/temperature 接口

  // 振动数据：后端暂未提供振动数据接口，返回空数组
  // 若需启用，应新增 GET /api/v1/simulation/{task_id}/vibration 接口

  return visualizationData
}

/**
 * 清除指定任务的缓存
 */
export function clearSimulationCache(taskId?: string): void {
  if (taskId) {
    cache.delete(taskId)
  } else {
    cache.clear()
  }
}

/**
 * 获取缓存统计信息
 */
export function getCacheStats(): { size: number; maxAge: number; maxSize: number } {
  return {
    size: cache.size,
    maxAge: CACHE_CONFIG.maxAge,
    maxSize: CACHE_CONFIG.maxSize,
  }
}
