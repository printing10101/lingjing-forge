/**
 * DXF 文件导入管理 Store
 *
 * 管理 DXF 文件的上传、解析、特征提取、预览和导入工程全流程。
 * 必须使用项目封装的 http 客户端 (@/utils/http) 进行后端调用。
 */
import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import http from '@/utils/http'
import { extractErrorMessage, isNetworkError } from '@/utils/error-handler'
import { API_CONFIG, buildApiPath } from '@/config/api'
import type {
  DxfParseResponse,
  DxfFeatureResponse,
  DxfUploadResponse,
} from '@/types'

/** 单个状态阶段 */
export type DxfImportPhase = 'idle' | 'uploading' | 'parsing' | 'success' | 'error'

/**
 * DXF 导入管理 Store
 *
 * 负责：
 * 1. 上传 DXF 文件至后端（POST {API_CONFIG.DXF}/upload）
 * 2. 触发后端解析（POST {API_CONFIG.DXF}/parse）
 * 3. 保存解析结果与特征信息用于预览
 * 4. 维护 UI 状态（进度、错误、阶段）
 */
export const useDxfImportStore = defineStore('dxfImport', () => {
  /** 对话框显示状态 */
  const showDialog = ref(false)
  /** 当前阶段 */
  const phase = ref<DxfImportPhase>('idle')
  /** 上传进度（0-100） */
  const uploadProgress = ref(0)
  /** 解析进度（0-100） */
  const parseProgress = ref(0)
  /** 当前上传后的文件ID（用于解析阶段） */
  const currentFileId = ref('')
  /** 当前原始文件名 */
  const currentFileName = ref('')
  /** 当前文件大小（字节） */
  const currentFileSize = ref(0)
  /** 解析结果 */
  const parseResult = ref<DxfParseResponse | null>(null)
  /** 特征提取结果 */
  const featureResult = ref<DxfFeatureResponse | null>(null)
  /** 错误信息 */
  const errorMessage = ref('')

  /** 是否处于空闲 */
  const isIdle = computed(() => phase.value === 'idle')
  /** 是否正在上传 */
  const isUploading = computed(() => phase.value === 'uploading')
  /** 是否正在解析 */
  const isParsing = computed(() => phase.value === 'parsing')
  /** 是否成功 */
  const isSuccess = computed(() => phase.value === 'success')
  /** 是否失败 */
  const isError = computed(() => phase.value === 'error')
  /** 是否处于活动状态（上传或解析） */
  const isActive = computed(() => phase.value === 'uploading' || phase.value === 'parsing')
  /** 综合进度（0-100） */
  const overallProgress = computed(() => {
    if (phase.value === 'uploading') return Math.round(uploadProgress.value * 0.5)
    if (phase.value === 'parsing') return 50 + Math.round(parseProgress.value * 0.5)
    return 0
  })

  function reset() {
    phase.value = 'idle'
    uploadProgress.value = 0
    parseProgress.value = 0
    currentFileId.value = ''
    currentFileName.value = ''
    currentFileSize.value = 0
    parseResult.value = null
    featureResult.value = null
    errorMessage.value = ''
  }

  /**
   * 上传 DXF 文件至后端。
   * 使用 XHR 以便捕获上传进度（Axios 同样支持但使用 XMLHttpRequest 更稳定）。
   */
  function uploadDxfFile(file: File): Promise<DxfUploadResponse> {
    return new Promise((resolve, reject) => {
      const formData = new FormData()
      formData.append('file', file)

      const xhr = new XMLHttpRequest()
      // 通过相对路径，让项目封装的 Vite proxy / 反向代理处理
      // 鉴权由后端会话/Cookie 或反向代理层统一处理，前端不持有 token
      xhr.open('POST', buildApiPath(API_CONFIG.DXF, '/upload'), true)

      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && event.total > 0) {
          uploadProgress.value = Math.round((event.loaded * 100) / event.total)
        }
      }

      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            const body = JSON.parse(xhr.responseText)
            if (body && body.code === 0 && body.data) {
              resolve(body.data as DxfUploadResponse)
              return
            }
            reject(new Error(body?.message || '上传失败'))
          } catch (e) {
            reject(new Error('解析上传响应失败'))
          }
        } else {
          let message = `上传失败 (${xhr.status})`
          try {
            const body = JSON.parse(xhr.responseText)
            if (body?.message) message = body.message
            else if (body?.detail) message = typeof body.detail === 'string' ? body.detail : (body.detail?.message || message)
          } catch {
            // ignore parse error
          }
          reject(new Error(message))
        }
      }

      xhr.onerror = () => reject(new Error('网络连接错误，请检查网络状态后重试'))
      xhr.ontimeout = () => reject(new Error('上传超时，请重试'))
      xhr.timeout = 120000
      xhr.send(formData)
    })
  }

  /**
   * 解析 DXF 文件（基于已上传的 file_id）。
   * 该步骤可能耗时较长，使用 indeterminate 进度条展示。
   */
  async function parseDxfFile(fileId: string): Promise<DxfParseResponse> {
    parseProgress.value = 10
    const response = await http.post<{ code: number; data: DxfParseResponse; message: string }>(
      buildApiPath(API_CONFIG.DXF, '/parse'),
      { file_id: fileId },
      { timeout: 120000 },
    )
    parseProgress.value = 80

    if (response.data?.code !== 0) {
      throw new Error(response.data?.message || '解析失败')
    }
    parseProgress.value = 100
    return response.data.data
  }

  /**
   * 提取 DXF 特征（孔、平面、整体尺寸等）。
   * 若解析接口已包含特征信息可省略此调用。
   */
  async function extractDxfFeatures(fileId: string): Promise<DxfFeatureResponse | null> {
    try {
      const response = await http.post<{ code: number; data: DxfFeatureResponse; message: string }>(
        buildApiPath(API_CONFIG.DXF, '/features'),
        { file_id: fileId },
        { timeout: 120000 },
      )
      if (response.data?.code === 0) return response.data.data
      return null
    } catch {
      // 特征提取失败不影响主流程
      return null
    }
  }

  /**
   * 完整导入流程：上传 → 解析 → 特征提取。
   * @returns 成功时返回 true，失败返回 false（错误信息写入 errorMessage）
   */
  async function importDxfFile(file: File): Promise<boolean> {
    reset()
    currentFileName.value = file.name
    currentFileSize.value = file.size

    // 阶段 1：上传
    phase.value = 'uploading'
    uploadProgress.value = 0
    let uploadResp: DxfUploadResponse
    try {
      uploadResp = await uploadDxfFile(file)
      currentFileId.value = uploadResp.file_id
    } catch (err) {
      phase.value = 'error'
      errorMessage.value = isNetworkError(err)
        ? '网络异常，上传失败。请检查网络后重试。'
        : extractErrorMessage(err, '上传失败')
      return false
    }

    // 阶段 2：解析
    phase.value = 'parsing'
    parseProgress.value = 0
    try {
      const parseResp = await parseDxfFile(uploadResp.file_id)
      parseResult.value = parseResp
    } catch (err) {
      phase.value = 'error'
      errorMessage.value = isNetworkError(err)
        ? '网络异常，解析失败。请稍后重试。'
        : extractErrorMessage(err, 'DXF 解析失败')
      return false
    }

    // 阶段 3：特征提取（异步且非阻塞）
    if (parseResult.value) {
      const features = await extractDxfFeatures(uploadResp.file_id)
      if (features) featureResult.value = features
    }

    phase.value = 'success'
    return true
  }

  function openDialog() {
    reset()
    showDialog.value = true
  }

  function closeDialog() {
    showDialog.value = false
  }

  return {
    showDialog,
    phase,
    uploadProgress,
    parseProgress,
    overallProgress,
    currentFileId,
    currentFileName,
    currentFileSize,
    parseResult,
    featureResult,
    errorMessage,
    isIdle,
    isUploading,
    isParsing,
    isSuccess,
    isError,
    isActive,
    reset,
    importDxfFile,
    // 内部阶段函数（上传/解析/特征提取）暴露供测试与高级调用；
    // 常规使用请走 importDxfFile 全流程
    uploadDxfFile,
    parseDxfFile,
    extractDxfFeatures,
    openDialog,
    closeDialog,
  }
})
