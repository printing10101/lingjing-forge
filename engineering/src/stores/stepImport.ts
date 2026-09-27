import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import http from '@/utils/http'
import { extractErrorMessage } from '@/utils/error-handler'
import { API_CONFIG, buildApiPath } from '@/config/api'
import type {
  StepImportResult,
  ImportHistoryEntry,
  ImportState,
  StlFileInfo,
} from '@/types'

/**
 * STEP 文件导入管理 Store
 * 管理 STEP 文件的上传、解析、STL 预览和导入历史记录。
 */
export const useStepImportStore = defineStore('stepImport', () => {
  /** 当前导入状态 */
  const importState = ref<ImportState>('idle')
  /** 上传进度百分比 (0-100) */
  const uploadProgress = ref(0)
  /** 当前导入结果 */
  const currentResult = ref<StepImportResult | null>(null)
  /** 错误信息 */
  const errorMessage = ref('')
  /** 导入历史记录 */
  const importHistory = ref<ImportHistoryEntry[]>([])
  /** 历史记录加载状态 */
  const historyLoading = ref(false)
  /** 当前激活的 STL 文件 URL */
  const activeStlUrl = ref('')
  /** 当前激活的 STL 文件列表 */
  const activeStlFiles = ref<StlFileInfo[]>([])
  /** 选中实体索引 */
  const selectedEntityIndex = ref(0)
  /** 对话框显示状态 */
  const showDialog = ref(false)

  /** 是否处于空闲状态 */
  const isIdle = computed(() => importState.value === 'idle')
  /** 是否正在上传 */
  const isUploading = computed(() => importState.value === 'uploading')
  /** 是否正在处理 */
  const isProcessing = computed(() => importState.value === 'processing')
  /** 是否导入成功 */
  const isSuccess = computed(() => importState.value === 'success')
  /** 是否导入出错 */
  const isError = computed(() => importState.value === 'error')
  /** 是否处于活动状态（上传或处理中） */
  const isActive = computed(() => importState.value === 'uploading' || importState.value === 'processing')

  /** 是否有 STL 文件 */
  const hasStlFiles = computed(() => activeStlFiles.value.length > 0)
  /** 模型信息 */
  const modelInfo = computed(() => currentResult.value?.model_info ?? null)
  /** 实体列表 */
  const entities = computed(() => currentResult.value?.entities ?? [])
  /** 警告信息列表 */
  const warnings = computed(() => currentResult.value?.warnings ?? [])

  function reset() {
    importState.value = 'idle'
    uploadProgress.value = 0
    currentResult.value = null
    errorMessage.value = ''
    activeStlUrl.value = ''
    activeStlFiles.value = []
    selectedEntityIndex.value = 0
  }

  async function importStepFile(
    file: File,
    precision: string = 'medium',
    outputFormat: string = 'stl',
  ): Promise<boolean> {
    reset()
    importState.value = 'uploading'
    uploadProgress.value = 0

    const formData = new FormData()
    formData.append('file', file)
    formData.append('precision', precision)
    formData.append('output_format', outputFormat)
    formData.append('use_cache', 'true')

    try {
      const response = await http.post<{ code: number; data: StepImportResult; message: string }>(
        buildApiPath(API_CONFIG.IMPORT, '/step'),
        formData,
        {
          headers: { 'Content-Type': 'multipart/form-data' },
          onUploadProgress: (progressEvent) => {
            if (progressEvent.total) {
              uploadProgress.value = Math.round((progressEvent.loaded * 100) / progressEvent.total)
            }
          },
          timeout: 120000,
        },
      )

      if (response.data.code === 0) {
        currentResult.value = response.data.data
        activeStlFiles.value = response.data.data.stl_files || []
        selectedEntityIndex.value = 0
        if (activeStlFiles.value.length > 0) {
          activeStlUrl.value = activeStlFiles.value[0].stl_url
        }
        importState.value = 'success'
        await fetchImportHistory()
        return true
      } else {
        errorMessage.value = response.data.message || '导入失败'
        importState.value = 'error'
        return false
      }
    } catch (err: unknown) {
      importState.value = 'error'
      const axiosErr = err as { response?: { status?: number }; code?: string }
      if (axiosErr.response?.status === 413) {
        errorMessage.value = '文件过大，请选择小于50MB的STEP文件'
      } else if (axiosErr.code === 'ECONNABORTED') {
        errorMessage.value = '请求超时，文件可能过大或网络不稳定'
      } else {
        errorMessage.value = extractErrorMessage(err, '网络错误，导入失败')
      }
      return false
    }
  }

  function selectEntity(index: number) {
    if (index >= 0 && index < activeStlFiles.value.length) {
      selectedEntityIndex.value = index
      activeStlUrl.value = activeStlFiles.value[index].stl_url
    }
  }

  async function fetchImportHistory() {
    historyLoading.value = true
    try {
      const response = await http.get<{ code: number; data: { history: ImportHistoryEntry[]; total: number } }>(
        buildApiPath(API_CONFIG.IMPORT, '/step/history'),
        { params: { limit: 20 } },
      )
      if (response.data.code === 0) {
        importHistory.value = response.data.data.history || []
      }
    } catch (e: unknown) {
      // 历史记录加载失败时降级为空列表，但需记录便于排查
      console.warn('[stepImport] fetchImportHistory failed:', e)
      importHistory.value = []
    } finally {
      historyLoading.value = false
    }
  }

  async function deleteHistoryFile(fileName: string) {
    try {
      await http.delete(buildApiPath(API_CONFIG.IMPORT, `/step/history/${encodeURIComponent(fileName)}`))
      await fetchImportHistory()
    } catch (e: unknown) {
      console.warn('[stepImport] deleteHistoryFile failed:', e)
      ElMessage.error('删除历史文件失败，请稍后重试')
    }
  }

  async function clearCache() {
    try {
      await http.delete(buildApiPath(API_CONFIG.IMPORT, '/step/cache'))
    } catch (e: unknown) {
      console.warn('[stepImport] clearCache failed:', e)
      ElMessage.error('清理缓存失败，请稍后重试')
    }
  }

  return {
    importState,
    uploadProgress,
    currentResult,
    errorMessage,
    importHistory,
    historyLoading,
    activeStlUrl,
    activeStlFiles,
    selectedEntityIndex,
    showDialog,
    isIdle,
    isUploading,
    isProcessing,
    isSuccess,
    isError,
    isActive,
    hasStlFiles,
    modelInfo,
    entities,
    warnings,
    reset,
    importStepFile,
    selectEntity,
    fetchImportHistory,
    deleteHistoryFile,
    clearCache,
  }
})
