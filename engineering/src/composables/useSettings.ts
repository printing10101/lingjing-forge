/**
 * 系统设置管理
 * 处理国际化切换、系统日志导出等设置相关操作
 */

import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { setLocale, type SupportedLocale } from '@/i18n'
import { formatTimestamp } from '@/utils/formatters'

/**
 * 判断当前是否运行在 Tauri 桌面应用环境中。
 * 用于避免在 Web/测试环境静态导入 @tauri-apps/api 导致报错。
 */
function isTauriEnv(): boolean {
  return typeof window !== 'undefined' && '__TAURI__' in window
}

/** 日志导出结果 */
export interface LogExportResult {
  success: boolean
  message: string
  outputPath: string | null
  file_count?: number
  total_size_bytes?: number
}

/** Tauri invoke 导出日志的返回类型 */
export interface InvokeExportLogsResult {
  success: boolean
  message: string
  output_path?: string
  file_count?: number
  total_size_bytes?: number
}

export interface UseSettingsReturn {
  currentLocale: ReturnType<typeof ref<SupportedLocale>>
  handleLocaleChange: (locale: string) => void
  formatTimestamp: (ts: number) => string
  exportingLogs: ReturnType<typeof ref<boolean>>
  exportProgress: ReturnType<typeof ref<number>>
  exportResult: ReturnType<typeof ref<LogExportResult | null>>
  exportSystemLogs: (exportDays: number) => Promise<void>
}

export function useSettings(): UseSettingsReturn {
  const { t } = useI18n()

  const currentLocale = ref<SupportedLocale>(
    (localStorage.getItem('app_locale') as SupportedLocale) || 'zh-CN'
  )

  /**
   * 处理语言切换
   * @param locale - 目标语言代码
   */
  function handleLocaleChange(locale: string) {
    const setter = (window as unknown as Record<string, unknown>).__setLocale as
      | ((locale: SupportedLocale) => void)
      | undefined
    if (setter) {
      setter(locale as SupportedLocale)
    } else {
      setLocale(locale as SupportedLocale)
    }
    currentLocale.value = locale as SupportedLocale
  }

  /**
   * 带当前语言上下文的格式化时间戳
   * @param ts - Unix时间戳
   * @returns 格式化后的时间字符串
   */
  const formatTimestampWithLocale = (ts: number): string => formatTimestamp(ts, currentLocale.value)

  const exportingLogs = ref(false)
  const exportProgress = ref(0)
  const exportResult = ref<LogExportResult | null>(null)

  /**
   * 导出系统日志（仅限Tauri桌面应用）
   * @param exportDays - 导出多少天内的日志
   */
  async function exportSystemLogs(exportDays: number) {
    exportingLogs.value = true
    exportProgress.value = 0
    exportResult.value = null

    const progressTimer = setInterval(() => {
      if (exportProgress.value < 90) {
        exportProgress.value += Math.floor(Math.random() * 15) + 5
        if (exportProgress.value > 90) exportProgress.value = 90
      }
    }, 400)

    try {
      // 安全修复：动态导入 @tauri-apps/api/core，避免在非 Tauri 环境（Web/测试）静态导入抛错
      if (!isTauriEnv()) {
        exportResult.value = {
          success: false,
          message: '日志导出仅在桌面应用环境可用',
          outputPath: null,
        }
        ElMessage.error(t('settings.exportFailed'))
        return
      }
      const { invoke } = await import('@tauri-apps/api/core')
      const result = await invoke<InvokeExportLogsResult>('export_logs_cmd', {
        days: exportDays,
      })
      exportProgress.value = 100
      exportResult.value = {
        success: result.success,
        message: result.message,
        outputPath: result.output_path ?? null,
        file_count: result.file_count,
        total_size_bytes: result.total_size_bytes,
      }

      if (result.success && result.output_path) {
        ElMessage.success(t('settings.exportSuccess'))
      } else if (result.file_count === 0) {
        ElMessage.info(t('settings.noLogsToExport'))
      }
    } catch (e: unknown) {
      const message = typeof e === 'string' ? e : (e instanceof Error ? e.message : t('settings.exportFailed'))
      exportResult.value = {
        success: false,
        message,
        outputPath: null,
      }
      ElMessage.error(t('settings.exportFailed'))
    } finally {
      clearInterval(progressTimer)
      exportProgress.value = 100
      setTimeout(() => {
        exportingLogs.value = false
      }, 500)
    }
  }

  return {
    currentLocale,
    handleLocaleChange,
    formatTimestamp: formatTimestampWithLocale,
    exportingLogs,
    exportProgress,
    exportResult,
    exportSystemLogs,
  }
}
