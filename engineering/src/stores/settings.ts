import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

/**
 * 简易防抖函数：在连续触发时只执行最后一次，避免高频写入 localStorage。
 * 不依赖 lodash，仅用于本 store 内部状态持久化。
 */
function debounce<A>(fn: (...args: A[]) => void, wait: number): (...args: A[]) => void {
  let timer: ReturnType<typeof setTimeout> | null = null
  return (...args: A[]) => {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => {
      timer = null
      fn(...args)
    }, wait)
  }
}

export interface LogSettings {
  logLevel: 'DEBUG' | 'INFO' | 'WARN' | 'ERROR'
  maxFileSizeMB: number
  retentionDays: number
  exportDays: number
}

// [U-P0-2] 硬件档位声明：与后端 HardwareTierConfig 对齐
//   - minimal : 仅规则引擎 + 云端 API（4核/8GB/无GPU）
//   - standard: 默认，支持本地小模型（8核/16GB/可选GPU）
//   - high    : 本地 7B-14B 模型（8核+/32GB/NVIDIA GPU ≥6GB）
//   - ultra   : 工作站级，本地 14B+ 模型 + GPU 训练
export type HardwareTier = 'minimal' | 'standard' | 'high' | 'ultra'

export interface AppSettings {
  aiMode: 'local' | 'cloud'
  localModel: string
  device: 'cpu' | 'cuda'
  offlineMode: boolean
  logSettings: LogSettings
  // [U-P0-2] 硬件档位 + 轻量模式（前端持久化用户偏好，后端通过环境变量在启动时固化）
  hardwareTier: HardwareTier
  lightweightMode: boolean
}

const STORAGE_KEY = 'lingjing_settings'

function getDefaultLogSettings(): LogSettings {
  return {
    logLevel: 'INFO',
    maxFileSizeMB: 50,
    retentionDays: 30,
    exportDays: 7,
  }
}

function loadSettings(): AppSettings {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      return {
        ...getDefaultSettings(),
        ...parsed,
        logSettings: {
          ...getDefaultLogSettings(),
          ...(parsed.logSettings || {}),
        },
      }
    }
  } catch (e: unknown) {
    // localStorage 数据损坏时清理脏数据并回退默认值，避免后续每次启动都重复抛错
    console.warn('[settings] loadSettings: failed to parse localStorage, falling back to defaults:', e)
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      /* localStorage 不可用时无可清理，忽略 */
    }
  }
  return getDefaultSettings()
}

function getDefaultSettings(): AppSettings {
  return {
    aiMode: 'local',
    localModel: 'qwen3.5:35b-128k',
    device: 'cpu',
    offlineMode: false,
    logSettings: getDefaultLogSettings(),
    // [U-P0-2] 默认 standard 档位，与后端 config.hardware.tier 默认值对齐
    hardwareTier: 'standard',
    lightweightMode: false,
  }
}

export const useSettingsStore = defineStore('settings', () => {
  const settings = ref<AppSettings>(loadSettings())

  // 深度 watch 会在每个嵌套字段变更时触发，未防抖时一次表单输入可能触发 N 次写入。
  // 使用 300ms 防抖合并连续变更，降低 localStorage I/O 频率。
  const persistToStorage = debounce((val: AppSettings) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(val))
    } catch (e: unknown) {
      // localStorage 配额超限或被禁用时仅记录日志，不影响应用内状态
      console.warn('[settings] persistToStorage: failed to write localStorage:', e)
    }
  }, 300)

  watch(settings, (val) => {
    persistToStorage(val)
  }, { deep: true })

  function saveSettings() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(settings.value))
    } catch (e: unknown) {
      console.warn('[settings] saveSettings: failed to write localStorage:', e)
    }
  }

  function resetSettings() {
    settings.value = getDefaultSettings()
  }

  function updateLogSettings(partial: Partial<LogSettings>) {
    settings.value.logSettings = {
      ...settings.value.logSettings,
      ...partial,
    }
  }

  return { settings, saveSettings, resetSettings, updateLogSettings }
})