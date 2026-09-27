import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useStepImportStore } from '@/stores/stepImport'

// mock http 客户端
vi.mock('@/utils/http', () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
    delete: vi.fn(),
  },
}))

// 保留 error-handler 真实逻辑
vi.mock('@/utils/error-handler', async () => {
  const actual = await vi.importActual<typeof import('@/utils/error-handler')>('@/utils/error-handler')
  return { ...actual }
})

// mock ElMessage
const elMessageMock = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))
vi.mock('element-plus', () => ({
  ElMessage: elMessageMock,
}))

import http from '@/utils/http'

function makeFile(name = 'test.step'): File {
  return new File(['dummy content'], name, { type: 'application/step' })
}

describe('useStepImportStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('initial state', () => {
    it('初始 importState 为 idle', () => {
      const store = useStepImportStore()
      expect(store.importState).toBe('idle')
    })

    it('初始 uploadProgress 为 0', () => {
      const store = useStepImportStore()
      expect(store.uploadProgress).toBe(0)
    })

    it('初始 currentResult 为 null', () => {
      const store = useStepImportStore()
      expect(store.currentResult).toBeNull()
    })

    it('初始 errorMessage 为空字符串', () => {
      const store = useStepImportStore()
      expect(store.errorMessage).toBe('')
    })

    it('初始 importHistory 为空数组', () => {
      const store = useStepImportStore()
      expect(store.importHistory).toEqual([])
    })

    it('初始 historyLoading 为 false', () => {
      const store = useStepImportStore()
      expect(store.historyLoading).toBe(false)
    })

    it('初始 activeStlUrl 为空字符串', () => {
      const store = useStepImportStore()
      expect(store.activeStlUrl).toBe('')
    })

    it('初始 activeStlFiles 为空数组', () => {
      const store = useStepImportStore()
      expect(store.activeStlFiles).toEqual([])
    })

    it('初始 selectedEntityIndex 为 0', () => {
      const store = useStepImportStore()
      expect(store.selectedEntityIndex).toBe(0)
    })

    it('初始 showDialog 为 false', () => {
      const store = useStepImportStore()
      expect(store.showDialog).toBe(false)
    })
  })

  describe('computed', () => {
    it('isIdle 在 idle 状态时为 true', () => {
      const store = useStepImportStore()
      expect(store.isIdle).toBe(true)
    })

    it('isUploading 在 uploading 状态时为 true', () => {
      const store = useStepImportStore()
      store.$patch({ importState: 'uploading' })
      expect(store.isUploading).toBe(true)
    })

    it('isProcessing 在 processing 状态时为 true', () => {
      const store = useStepImportStore()
      store.$patch({ importState: 'processing' })
      expect(store.isProcessing).toBe(true)
    })

    it('isSuccess 在 success 状态时为 true', () => {
      const store = useStepImportStore()
      store.$patch({ importState: 'success' })
      expect(store.isSuccess).toBe(true)
    })

    it('isError 在 error 状态时为 true', () => {
      const store = useStepImportStore()
      store.$patch({ importState: 'error' })
      expect(store.isError).toBe(true)
    })

    it('isActive 在 uploading 或 processing 状态时为 true', () => {
      const store = useStepImportStore()
      store.$patch({ importState: 'uploading' })
      expect(store.isActive).toBe(true)
      store.$patch({ importState: 'processing' })
      expect(store.isActive).toBe(true)
      store.$patch({ importState: 'idle' })
      expect(store.isActive).toBe(false)
    })

    it('hasStlFiles 在有 STL 文件时为 true', () => {
      const store = useStepImportStore()
      store.$patch({
        activeStlFiles: [{ stl_url: '/a.stl', name: 'a' }] as never,
      })
      expect(store.hasStlFiles).toBe(true)
    })

    it('modelInfo 反映 currentResult.model_info', () => {
      const store = useStepImportStore()
      store.$patch({
        currentResult: {
          model_info: { vertices: 100, faces: 50 },
          entities: [],
          warnings: [],
        } as never,
      })
      expect(store.modelInfo).toEqual({ vertices: 100, faces: 50 })
    })

    it('modelInfo 在无 currentResult 时为 null', () => {
      const store = useStepImportStore()
      expect(store.modelInfo).toBeNull()
    })

    it('entities 反映 currentResult.entities', () => {
      const store = useStepImportStore()
      store.$patch({
        currentResult: {
          model_info: null,
          entities: [{ id: 'e1' }, { id: 'e2' }],
          warnings: [],
        } as never,
      })
      expect(store.entities).toHaveLength(2)
    })

    it('warnings 反映 currentResult.warnings', () => {
      const store = useStepImportStore()
      store.$patch({
        currentResult: {
          model_info: null,
          entities: [],
          warnings: ['尺寸超限'],
        } as never,
      })
      expect(store.warnings).toEqual(['尺寸超限'])
    })
  })

  describe('reset', () => {
    it('重置所有状态为初始值', () => {
      const store = useStepImportStore()
      store.$patch({
        importState: 'success',
        uploadProgress: 80,
        currentResult: { model_info: {} } as never,
        errorMessage: '旧错误',
        activeStlUrl: '/old.stl',
        activeStlFiles: [{ stl_url: '/old.stl' }] as never,
        selectedEntityIndex: 2,
      })
      store.reset()
      expect(store.importState).toBe('idle')
      expect(store.uploadProgress).toBe(0)
      expect(store.currentResult).toBeNull()
      expect(store.errorMessage).toBe('')
      expect(store.activeStlUrl).toBe('')
      expect(store.activeStlFiles).toEqual([])
      expect(store.selectedEntityIndex).toBe(0)
    })
  })

  describe('importStepFile', () => {
    it('导入成功时保存结果并更新状态', async () => {
      const stlFiles = [
        { stl_url: '/a.stl', name: 'a' },
        { stl_url: '/b.stl', name: 'b' },
      ]
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: {
          code: 0,
          data: {
            model_info: { vertices: 10 },
            entities: [{ id: 'e1' }],
            warnings: [],
            stl_files: stlFiles,
          },
        },
      })
      // fetchImportHistory 的 get 调用
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { history: [], total: 0 } },
      })
      const store = useStepImportStore()
      const result = await store.importStepFile(makeFile())
      expect(result).toBe(true)
      expect(store.importState).toBe('success')
      expect(store.currentResult).not.toBeNull()
      expect(store.activeStlFiles).toHaveLength(2)
      expect(store.activeStlUrl).toBe('/a.stl')
      expect(store.selectedEntityIndex).toBe(0)
    })

    it('导入成功但无 STL 文件时不设置 activeStlUrl', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: {
          code: 0,
          data: {
            model_info: null,
            entities: [],
            warnings: [],
            stl_files: [],
          },
        },
      })
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { history: [], total: 0 } },
      })
      const store = useStepImportStore()
      const result = await store.importStepFile(makeFile())
      expect(result).toBe(true)
      expect(store.activeStlUrl).toBe('')
      expect(store.activeStlFiles).toEqual([])
    })

    it('后端返回非 0 code 时设置错误信息', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1, message: '文件格式不支持' },
      })
      const store = useStepImportStore()
      const result = await store.importStepFile(makeFile())
      expect(result).toBe(false)
      expect(store.importState).toBe('error')
      expect(store.errorMessage).toBe('文件格式不支持')
    })

    it('后端返回非 0 code 且无 message 时使用默认错误', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1 },
      })
      const store = useStepImportStore()
      const result = await store.importStepFile(makeFile())
      expect(result).toBe(false)
      expect(store.errorMessage).toBe('导入失败')
    })

    it('文件过大 (413) 时返回友好提示', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue({
        response: { status: 413 },
      })
      const store = useStepImportStore()
      const result = await store.importStepFile(makeFile())
      expect(result).toBe(false)
      expect(store.importState).toBe('error')
      expect(store.errorMessage).toBe('文件过大，请选择小于50MB的STEP文件')
    })

    it('请求超时 (ECONNABORTED) 时返回超时提示', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue({
        code: 'ECONNABORTED',
      })
      const store = useStepImportStore()
      const result = await store.importStepFile(makeFile())
      expect(result).toBe(false)
      expect(store.errorMessage).toBe('请求超时，文件可能过大或网络不稳定')
    })

    it('网络异常时通过 extractErrorMessage 提取错误', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue({
        response: { data: { message: '服务器内部错误' } },
      })
      const store = useStepImportStore()
      const result = await store.importStepFile(makeFile())
      expect(result).toBe(false)
      expect(store.errorMessage).toBe('服务器内部错误')
    })

    it('未知异常时使用默认错误信息', async () => {
      // 用 {}（无法提取任何信息）触发默认值；字符串异常会被 extractErrorMessage 原样返回
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue({})
      const store = useStepImportStore()
      const result = await store.importStepFile(makeFile())
      expect(result).toBe(false)
      expect(store.errorMessage).toBe('网络错误，导入失败')
    })

    it('上传过程中通过 onUploadProgress 更新进度', async () => {
      let progressCallback: ((event: { loaded: number; total: number }) => void) | undefined
      ;(http.post as ReturnType<typeof vi.fn>).mockImplementation(
        (_url: unknown, _data: unknown, config: { onUploadProgress?: (event: { loaded: number; total: number }) => void }) => {
          progressCallback = config?.onUploadProgress
          return Promise.resolve({
            data: { code: 0, data: { model_info: null, entities: [], warnings: [], stl_files: [] } },
          })
        },
      )
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { history: [], total: 0 } },
      })
      const store = useStepImportStore()
      const promise = store.importStepFile(makeFile())
      // 触发进度回调
      if (progressCallback) {
        progressCallback({ loaded: 50, total: 100 })
        expect(store.uploadProgress).toBe(50)
        progressCallback({ loaded: 100, total: 100 })
        expect(store.uploadProgress).toBe(100)
      }
      await promise
      expect(store.importState).toBe('success')
    })
  })

  describe('selectEntity', () => {
    it('选择有效索引时更新 selectedEntityIndex 和 activeStlUrl', () => {
      const store = useStepImportStore()
      store.$patch({
        activeStlFiles: [
          { stl_url: '/a.stl', name: 'a' },
          { stl_url: '/b.stl', name: 'b' },
        ] as never,
      })
      store.selectEntity(1)
      expect(store.selectedEntityIndex).toBe(1)
      expect(store.activeStlUrl).toBe('/b.stl')
    })

    it('选择无效索引时不更新状态', () => {
      const store = useStepImportStore()
      store.$patch({
        activeStlFiles: [{ stl_url: '/a.stl', name: 'a' }] as never,
        selectedEntityIndex: 0,
        activeStlUrl: '/a.stl',
      })
      store.selectEntity(5)
      expect(store.selectedEntityIndex).toBe(0)
      expect(store.activeStlUrl).toBe('/a.stl')
    })

    it('选择负索引时不更新状态', () => {
      const store = useStepImportStore()
      store.$patch({
        activeStlFiles: [{ stl_url: '/a.stl', name: 'a' }] as never,
      })
      store.selectEntity(-1)
      expect(store.selectedEntityIndex).toBe(0)
    })
  })

  describe('fetchImportHistory', () => {
    it('获取历史记录成功时保存到 importHistory', async () => {
      const history = [
        { file_name: 'a.step', import_time: '2024-01-01', status: 'success' },
        { file_name: 'b.step', import_time: '2024-01-02', status: 'success' },
      ]
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { history, total: 2 } },
      })
      const store = useStepImportStore()
      await store.fetchImportHistory()
      expect(store.importHistory).toHaveLength(2)
      expect(store.historyLoading).toBe(false)
    })

    it('后端返回空 history 时降级为空数组', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { history: null, total: 0 } },
      })
      const store = useStepImportStore()
      await store.fetchImportHistory()
      expect(store.importHistory).toEqual([])
    })

    it('网络异常时降级为空列表', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network'))
      const store = useStepImportStore()
      await store.fetchImportHistory()
      expect(store.importHistory).toEqual([])
      expect(store.historyLoading).toBe(false)
    })
  })

  describe('deleteHistoryFile', () => {
    it('删除成功后刷新历史列表', async () => {
      (http.delete as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { code: 0 } })
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { history: [], total: 0 } },
      })
      const store = useStepImportStore()
      await store.deleteHistoryFile('old.step')
      expect(http.delete).toHaveBeenCalledWith(expect.stringContaining('old.step'))
      expect(http.get).toHaveBeenCalled()
    })

    it('删除失败时显示错误提示', async () => {
      (http.delete as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network'))
      const store = useStepImportStore()
      await store.deleteHistoryFile('old.step')
      expect(elMessageMock.error).toHaveBeenCalledWith('删除历史文件失败，请稍后重试')
    })
  })

  describe('clearCache', () => {
    it('清理缓存成功时不显示错误', async () => {
      (http.delete as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { code: 0 } })
      const store = useStepImportStore()
      await store.clearCache()
      expect(http.delete).toHaveBeenCalledWith(expect.stringContaining('/step/cache'))
      expect(elMessageMock.error).not.toHaveBeenCalled()
    })

    it('清理缓存失败时显示错误提示', async () => {
      (http.delete as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network'))
      const store = useStepImportStore()
      await store.clearCache()
      expect(elMessageMock.error).toHaveBeenCalledWith('清理缓存失败，请稍后重试')
    })
  })
})
