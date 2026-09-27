import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useDxfImportStore } from '@/stores/dxfImport'

// mock http 客户端（用于 parseDxfFile 和 extractDxfFeatures）
vi.mock('@/utils/http', () => ({
  default: {
    post: vi.fn(),
  },
}))

// 保留 error-handler 真实逻辑（包含 extractErrorMessage 和 isNetworkError）
vi.mock('@/utils/error-handler', async () => {
  const actual = await vi.importActual<typeof import('@/utils/error-handler')>('@/utils/error-handler')
  return { ...actual }
})

import http from '@/utils/http'

// XMLHttpRequest Mock
type XhrHandler = {
  onload: (() => void) | null
  onerror: (() => void) | null
  ontimeout: (() => void) | null
  upload: { onprogress: ((event: { loaded: number; total: number; lengthComputable: boolean }) => void) | null }
}

let xhrInstances: Array<{
  handler: XhrHandler
  status: number
  responseText: string
  send: ReturnType<typeof vi.fn>
  open: ReturnType<typeof vi.fn>
  timeout: number
}> = []

class MockXMLHttpRequest {
  handler: XhrHandler
  status = 0
  responseText = ''
  send = vi.fn()
  open = vi.fn()
  timeout = 0

  constructor() {
    this.handler = {
      onload: null,
      onerror: null,
      ontimeout: null,
      upload: { onprogress: null },
    }
    xhrInstances.push(this)
  }

  set onload(fn: (() => void) | null) { this.handler.onload = fn }
  get onload() { return this.handler.onload }
  set onerror(fn: (() => void) | null) { this.handler.onerror = fn }
  get onerror() { return this.handler.onerror }
  set ontimeout(fn: (() => void) | null) { this.handler.ontimeout = fn }
  get ontimeout() { return this.handler.ontimeout }

  get upload() { return this.handler.upload }
}

function lastXhr() {
  return xhrInstances[xhrInstances.length - 1]
}

describe('useDxfImportStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
    xhrInstances = []
    ;(globalThis as Record<string, unknown>).XMLHttpRequest = MockXMLHttpRequest
  })

  afterEach(() => {
    delete (globalThis as Record<string, unknown>).XMLHttpRequest
  })

  describe('initial state', () => {
    it('初始 showDialog 为 false', () => {
      const store = useDxfImportStore()
      expect(store.showDialog).toBe(false)
    })

    it('初始 phase 为 idle', () => {
      const store = useDxfImportStore()
      expect(store.phase).toBe('idle')
    })

    it('初始 uploadProgress 为 0', () => {
      const store = useDxfImportStore()
      expect(store.uploadProgress).toBe(0)
    })

    it('初始 parseProgress 为 0', () => {
      const store = useDxfImportStore()
      expect(store.parseProgress).toBe(0)
    })

    it('初始 currentFileId 为空字符串', () => {
      const store = useDxfImportStore()
      expect(store.currentFileId).toBe('')
    })

    it('初始 currentFileName 为空字符串', () => {
      const store = useDxfImportStore()
      expect(store.currentFileName).toBe('')
    })

    it('初始 currentFileSize 为 0', () => {
      const store = useDxfImportStore()
      expect(store.currentFileSize).toBe(0)
    })

    it('初始 parseResult 为 null', () => {
      const store = useDxfImportStore()
      expect(store.parseResult).toBeNull()
    })

    it('初始 featureResult 为 null', () => {
      const store = useDxfImportStore()
      expect(store.featureResult).toBeNull()
    })

    it('初始 errorMessage 为空字符串', () => {
      const store = useDxfImportStore()
      expect(store.errorMessage).toBe('')
    })
  })

  describe('computed', () => {
    it('isIdle 在 idle 阶段为 true', () => {
      const store = useDxfImportStore()
      expect(store.isIdle).toBe(true)
    })

    it('isUploading 在 uploading 阶段为 true', () => {
      const store = useDxfImportStore()
      store.$patch({ phase: 'uploading' })
      expect(store.isUploading).toBe(true)
    })

    it('isParsing 在 parsing 阶段为 true', () => {
      const store = useDxfImportStore()
      store.$patch({ phase: 'parsing' })
      expect(store.isParsing).toBe(true)
    })

    it('isSuccess 在 success 阶段为 true', () => {
      const store = useDxfImportStore()
      store.$patch({ phase: 'success' })
      expect(store.isSuccess).toBe(true)
    })

    it('isError 在 error 阶段为 true', () => {
      const store = useDxfImportStore()
      store.$patch({ phase: 'error' })
      expect(store.isError).toBe(true)
    })

    it('isActive 在 uploading 或 parsing 阶段为 true', () => {
      const store = useDxfImportStore()
      store.$patch({ phase: 'uploading' })
      expect(store.isActive).toBe(true)
      store.$patch({ phase: 'parsing' })
      expect(store.isActive).toBe(true)
      store.$patch({ phase: 'idle' })
      expect(store.isActive).toBe(false)
    })

    it('overallProgress 在 uploading 阶段为 uploadProgress * 0.5', () => {
      const store = useDxfImportStore()
      store.$patch({ phase: 'uploading', uploadProgress: 80 })
      expect(store.overallProgress).toBe(40)
    })

    it('overallProgress 在 parsing 阶段为 50 + parseProgress * 0.5', () => {
      const store = useDxfImportStore()
      store.$patch({ phase: 'parsing', parseProgress: 60 })
      expect(store.overallProgress).toBe(80)
    })

    it('overallProgress 在 idle 阶段为 0', () => {
      const store = useDxfImportStore()
      expect(store.overallProgress).toBe(0)
    })
  })

  describe('reset', () => {
    it('重置所有状态为初始值', () => {
      const store = useDxfImportStore()
      store.$patch({
        phase: 'success',
        uploadProgress: 80,
        parseProgress: 100,
        currentFileId: 'f1',
        currentFileName: 'a.dxf',
        currentFileSize: 1024,
        parseResult: { entities: [] } as never,
        featureResult: { holes: [] } as never,
        errorMessage: '旧错误',
      })
      store.reset()
      expect(store.phase).toBe('idle')
      expect(store.uploadProgress).toBe(0)
      expect(store.parseProgress).toBe(0)
      expect(store.currentFileId).toBe('')
      expect(store.currentFileName).toBe('')
      expect(store.currentFileSize).toBe(0)
      expect(store.parseResult).toBeNull()
      expect(store.featureResult).toBeNull()
      expect(store.errorMessage).toBe('')
    })
  })

  describe('uploadDxfFile', () => {
    it('上传成功时解析响应并返回 data', async () => {
      const store = useDxfImportStore()
      const file = new File(['data'], 'test.dxf', { type: 'application/dxf' })
      const promise = (store as any).uploadDxfFile(file)

      const xhr = lastXhr()
      expect(xhr.open).toHaveBeenCalledWith('POST', expect.stringContaining('/upload'), true)
      expect(xhr.send).toHaveBeenCalled()

      xhr.status = 200
      xhr.responseText = JSON.stringify({
        code: 0,
        data: { file_id: 'f_001', file_name: 'test.dxf' },
      })
      xhr.handler.onload!()

      const result = await promise
      expect(result).toEqual({ file_id: 'f_001', file_name: 'test.dxf' })
    })

    it('上传过程中通过 onprogress 更新 uploadProgress', async () => {
      const store = useDxfImportStore()
      const file = new File(['data'], 'test.dxf')
      const promise = (store as any).uploadDxfFile(file)

      const xhr = lastXhr()
      // 触发上传进度
      xhr.handler.upload.onprogress!({ loaded: 50, total: 100, lengthComputable: true })
      expect(store.uploadProgress).toBe(50)

      xhr.handler.upload.onprogress!({ loaded: 100, total: 100, lengthComputable: true })
      expect(store.uploadProgress).toBe(100)

      xhr.status = 200
      xhr.responseText = JSON.stringify({ code: 0, data: { file_id: 'f1' } })
      xhr.handler.onload!()
      await promise
    })

    it('后端返回非 0 code 时拒绝并返回 message', async () => {
      const store = useDxfImportStore()
      const file = new File(['data'], 'test.dxf')
      const promise = (store as any).uploadDxfFile(file)

      const xhr = lastXhr()
      xhr.status = 200
      xhr.responseText = JSON.stringify({ code: 1, message: '文件格式不支持' })
      xhr.handler.onload!()

      await expect(promise).rejects.toThrow('文件格式不支持')
    })

    it('响应体非 JSON 时拒绝并返回解析错误', async () => {
      const store = useDxfImportStore()
      const file = new File(['data'], 'test.dxf')
      const promise = (store as any).uploadDxfFile(file)

      const xhr = lastXhr()
      xhr.status = 200
      xhr.responseText = 'invalid json'
      xhr.handler.onload!()

      await expect(promise).rejects.toThrow('解析上传响应失败')
    })

    it('HTTP 状态码非 2xx 时拒绝并返回错误信息', async () => {
      const store = useDxfImportStore()
      const file = new File(['data'], 'test.dxf')
      const promise = (store as any).uploadDxfFile(file)

      const xhr = lastXhr()
      xhr.status = 500
      xhr.responseText = JSON.stringify({ message: '服务器内部错误' })
      xhr.handler.onload!()

      await expect(promise).rejects.toThrow('服务器内部错误')
    })

    it('HTTP 错误响应非 JSON 时返回状态码错误', async () => {
      const store = useDxfImportStore()
      const file = new File(['data'], 'test.dxf')
      const promise = (store as any).uploadDxfFile(file)

      const xhr = lastXhr()
      xhr.status = 503
      xhr.responseText = 'Service Unavailable'
      xhr.handler.onload!()

      await expect(promise).rejects.toThrow('上传失败 (503)')
    })

    it('HTTP 错误响应包含 detail 字符串时返回 detail', async () => {
      const store = useDxfImportStore()
      const file = new File(['data'], 'test.dxf')
      const promise = (store as any).uploadDxfFile(file)

      const xhr = lastXhr()
      xhr.status = 400
      xhr.responseText = JSON.stringify({ detail: '参数错误' })
      xhr.handler.onload!()

      await expect(promise).rejects.toThrow('参数错误')
    })

    it('HTTP 错误响应包含 detail 对象时返回 detail.message', async () => {
      const store = useDxfImportStore()
      const file = new File(['data'], 'test.dxf')
      const promise = (store as any).uploadDxfFile(file)

      const xhr = lastXhr()
      xhr.status = 400
      xhr.responseText = JSON.stringify({ detail: { message: '嵌套错误信息' } })
      xhr.handler.onload!()

      await expect(promise).rejects.toThrow('嵌套错误信息')
    })

    it('网络错误时拒绝', async () => {
      const store = useDxfImportStore()
      const file = new File(['data'], 'test.dxf')
      const promise = (store as any).uploadDxfFile(file)

      const xhr = lastXhr()
      xhr.handler.onerror!()

      await expect(promise).rejects.toThrow('网络连接错误，请检查网络状态后重试')
    })

    it('上传超时时拒绝', async () => {
      const store = useDxfImportStore()
      const file = new File(['data'], 'test.dxf')
      const promise = (store as any).uploadDxfFile(file)

      const xhr = lastXhr()
      expect(xhr.timeout).toBe(120000)
      xhr.handler.ontimeout!()

      await expect(promise).rejects.toThrow('上传超时，请重试')
    })
  })

  describe('parseDxfFile', () => {
    it('解析成功时更新 parseProgress 并返回数据', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { entities: [{ id: 'e1' }] } },
      })
      const store = useDxfImportStore()
      const result = await (store as any).parseDxfFile('f1')
      expect(result).toEqual({ entities: [{ id: 'e1' }] })
      expect(store.parseProgress).toBe(100)
    })

    it('后端返回非 0 code 时抛出错误', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1, message: '解析失败' },
      })
      const store = useDxfImportStore()
      await expect((store as any).parseDxfFile('f1')).rejects.toThrow('解析失败')
      expect(store.parseProgress).toBe(80)
    })

    it('后端返回非 0 code 且无 message 时使用默认错误', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1 },
      })
      const store = useDxfImportStore()
      await expect((store as any).parseDxfFile('f1')).rejects.toThrow('解析失败')
    })

    it('网络异常时抛出 axios 错误', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network'))
      const store = useDxfImportStore()
      await expect((store as any).parseDxfFile('f1')).rejects.toThrow('network')
    })
  })

  describe('extractDxfFeatures', () => {
    it('特征提取成功时返回数据', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { holes: 5, planes: 3 } },
      })
      const store = useDxfImportStore()
      const result = await (store as any).extractDxfFeatures('f1')
      expect(result).toEqual({ holes: 5, planes: 3 })
    })

    it('后端返回非 0 code 时返回 null', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1 },
      })
      const store = useDxfImportStore()
      const result = await (store as any).extractDxfFeatures('f1')
      expect(result).toBeNull()
    })

    it('网络异常时返回 null（不阻塞主流程）', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network'))
      const store = useDxfImportStore()
      const result = await (store as any).extractDxfFeatures('f1')
      expect(result).toBeNull()
    })
  })

  describe('importDxfFile', () => {
    it('完整导入流程成功时返回 true', async () => {
      const store = useDxfImportStore()
      const file = new File(['data'], 'test.dxf', { type: 'application/dxf' })

      const promise = store.importDxfFile(file)

      // 阶段1：上传
      expect(store.phase).toBe('uploading')
      const uploadXhr = lastXhr()
      uploadXhr.status = 200
      uploadXhr.responseText = JSON.stringify({ code: 0, data: { file_id: 'f1' } })
      uploadXhr.handler.onload!()

      // 阶段2：解析（mock parseDxfFile）
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
        data: { code: 0, data: { entities: [] } },
      })
      // 阶段3：特征提取（mock extractDxfFeatures）
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
        data: { code: 0, data: { holes: 2 } },
      })

      const result = await promise
      expect(result).toBe(true)
      expect(store.phase).toBe('success')
      expect(store.currentFileName).toBe('test.dxf')
      expect(store.currentFileId).toBe('f1')
      expect(store.parseResult).toEqual({ entities: [] })
      expect(store.featureResult).toEqual({ holes: 2 })
    })

    it('上传失败时返回 false 并设置错误信息', async () => {
      const store = useDxfImportStore()
      const file = new File(['data'], 'test.dxf')

      const promise = store.importDxfFile(file)

      const uploadXhr = lastXhr()
      uploadXhr.status = 400
      uploadXhr.responseText = JSON.stringify({ message: '文件过大' })
      uploadXhr.handler.onload!()

      const result = await promise
      expect(result).toBe(false)
      expect(store.phase).toBe('error')
      expect(store.errorMessage).toBe('文件过大')
    })

    it('上传网络错误时返回网络异常提示', async () => {
      const store = useDxfImportStore()
      const file = new File(['data'], 'test.dxf')

      const promise = store.importDxfFile(file)

      const uploadXhr = lastXhr()
      uploadXhr.handler.onerror!()

      const result = await promise
      expect(result).toBe(false)
      expect(store.phase).toBe('error')
      // XHR onerror reject 的是普通 Error（专有网络文案），isNetworkError 分支
      // 对 XHR 错误不可达——extractErrorMessage 返回 uploadDxfFile 提供的文案
      expect(store.errorMessage).toBe('网络连接错误，请检查网络状态后重试')
    })

    it('解析失败时返回 false 并设置错误信息', async () => {
      const store = useDxfImportStore()
      const file = new File(['data'], 'test.dxf')

      const promise = store.importDxfFile(file)

      const uploadXhr = lastXhr()
      uploadXhr.status = 200
      uploadXhr.responseText = JSON.stringify({ code: 0, data: { file_id: 'f1' } })
      uploadXhr.handler.onload!()

      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
        data: { code: 1, message: 'DXF 文件损坏' },
      })

      const result = await promise
      expect(result).toBe(false)
      expect(store.phase).toBe('error')
      expect(store.errorMessage).toBe('DXF 文件损坏')
    })

    it('解析网络错误时返回网络异常提示', async () => {
      const store = useDxfImportStore()
      const file = new File(['data'], 'test.dxf')

      const promise = store.importDxfFile(file)

      const uploadXhr = lastXhr()
      uploadXhr.status = 200
      uploadXhr.responseText = JSON.stringify({ code: 0, data: { file_id: 'f1' } })
      uploadXhr.handler.onload!()

      ;(http.post as ReturnType<typeof vi.fn>).mockRejectedValueOnce({ code: 'ERR_NETWORK' })

      const result = await promise
      expect(result).toBe(false)
      expect(store.phase).toBe('error')
      expect(store.errorMessage).toBe('网络异常，解析失败。请稍后重试。')
    })

    it('特征提取失败时不影响导入成功状态', async () => {
      const store = useDxfImportStore()
      const file = new File(['data'], 'test.dxf')

      const promise = store.importDxfFile(file)

      const uploadXhr = lastXhr()
      uploadXhr.status = 200
      uploadXhr.responseText = JSON.stringify({ code: 0, data: { file_id: 'f1' } })
      uploadXhr.handler.onload!()

      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
        data: { code: 0, data: { entities: [] } },
      })
      ;(http.post as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('features fail'))

      const result = await promise
      expect(result).toBe(true)
      expect(store.phase).toBe('success')
      expect(store.featureResult).toBeNull()
    })
  })

  describe('openDialog', () => {
    it('打开对话框时重置状态并显示对话框', () => {
      const store = useDxfImportStore()
      store.$patch({ phase: 'error', errorMessage: '旧错误', showDialog: false })
      store.openDialog()
      expect(store.showDialog).toBe(true)
      expect(store.phase).toBe('idle')
      expect(store.errorMessage).toBe('')
    })
  })

  describe('closeDialog', () => {
    it('关闭对话框时设置 showDialog 为 false', () => {
      const store = useDxfImportStore()
      store.$patch({ showDialog: true })
      store.closeDialog()
      expect(store.showDialog).toBe(false)
    })
  })
})
