import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useProjectStore } from '@/stores/project'

// mock http 客户端，避免真实网络请求
vi.mock('@/utils/http', () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
    delete: vi.fn(),
  },
}))

// mock error-handler，保留 extractErrorMessage 真实逻辑以覆盖错误提取路径
vi.mock('@/utils/error-handler', async () => {
  const actual = await vi.importActual<typeof import('@/utils/error-handler')>('@/utils/error-handler')
  return { ...actual }
})

import http from '@/utils/http'

describe('useProjectStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('initial state', () => {
    it('初始 projectId 为空字符串', () => {
      const store = useProjectStore()
      expect(store.projectId).toBe('')
    })

    it('初始 manifest 为默认未命名工程', () => {
      const store = useProjectStore()
      expect(store.manifest.version).toBe('1.0')
      expect(store.manifest.metadata.name).toBe('未命名工程')
      expect(store.manifest.resources).toEqual([])
    })

    it('初始 currentFilePath 为空字符串', () => {
      const store = useProjectStore()
      expect(store.currentFilePath).toBe('')
    })

    it('初始 isModified 为 false', () => {
      const store = useProjectStore()
      expect(store.isModified).toBe(false)
    })

    it('初始 loading 为 false', () => {
      const store = useProjectStore()
      expect(store.loading).toBe(false)
    })

    it('初始 error 为 null', () => {
      const store = useProjectStore()
      expect(store.error).toBeNull()
    })

    it('初始 projectList 为空数组', () => {
      const store = useProjectStore()
      expect(store.projectList).toEqual([])
    })

    it('初始 listLoading 为 false', () => {
      const store = useProjectStore()
      expect(store.listLoading).toBe(false)
    })
  })

  describe('computed', () => {
    it('projectName 反映 manifest.metadata.name', () => {
      const store = useProjectStore()
      store.$patch({
        manifest: {
          ...store.manifest,
          metadata: { ...store.manifest.metadata, name: '测试工程' },
        } as never,
      })
      expect(store.projectName).toBe('测试工程')
    })

    it('resourceCount 反映 manifest.resources 长度', () => {
      const store = useProjectStore()
      store.$patch({
        manifest: {
          ...store.manifest,
          resources: [
            { id: 'r1', name: 'a' },
            { id: 'r2', name: 'b' },
            { id: 'r3', name: 'c' },
          ] as never,
        } as never,
      })
      expect(store.resourceCount).toBe(3)
    })

    it('resourceCount 在无资源时为 0', () => {
      const store = useProjectStore()
      expect(store.resourceCount).toBe(0)
    })
  })

  describe('markModified', () => {
    it('调用后 isModified 变为 true', () => {
      const store = useProjectStore()
      expect(store.isModified).toBe(false)
      store.markModified()
      expect(store.isModified).toBe(true)
    })
  })

  describe('createProject', () => {
    it('创建成功时保存 projectId 和 manifest', async () => {
      const manifest = {
        version: '1.0',
        metadata: { name: '新工程', created_at: '', modified_at: '', author: '', description: '' },
        resources: [],
        data: {
          stock_definition: {},
          tool_selection: [],
          process_steps: [],
          toolpath_config: {},
          postprocessor_config: {},
          simulation_config: {},
        },
        extensions: {},
      }
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { project_id: 'p_001', manifest } },
      })
      const store = useProjectStore()
      const result = await store.createProject({ name: '新工程' } as never)
      expect(result).toBe(true)
      expect(store.projectId).toBe('p_001')
      expect(store.manifest.metadata.name).toBe('新工程')
      expect(store.currentFilePath).toBe('')
      expect(store.isModified).toBe(true)
      expect(store.loading).toBe(false)
      expect(store.error).toBeNull()
    })

    it('后端返回非 0 code 时设置 error 并返回 false', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1, message: '工程名已存在' },
      })
      const store = useProjectStore()
      const result = await store.createProject({ name: 'dup' } as never)
      expect(result).toBe(false)
      expect(store.error).toBe('工程名已存在')
      expect(store.loading).toBe(false)
    })

    it('后端未返回 message 时使用默认错误信息', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1 },
      })
      const store = useProjectStore()
      const result = await store.createProject({ name: 'x' } as never)
      expect(result).toBe(false)
      expect(store.error).toBe('创建工程失败')
    })

    it('网络异常时通过 extractErrorMessage 提取错误并返回 false', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue({
        response: { data: { message: '服务不可用' } },
      })
      const store = useProjectStore()
      const result = await store.createProject({ name: 'x' } as never)
      expect(result).toBe(false)
      expect(store.error).toBe('服务不可用')
      expect(store.loading).toBe(false)
    })

    it('未知异常时使用默认错误信息', async () => {
      // 用 {}（无法提取任何信息）触发默认值；字符串异常会被 extractErrorMessage 原样返回
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue({})
      const store = useProjectStore()
      const result = await store.createProject({ name: 'x' } as never)
      expect(result).toBe(false)
      expect(store.error).toBe('创建工程失败')
    })
  })

  describe('openProject', () => {
    it('通过文件路径打开工程成功', async () => {
      const manifest = {
        version: '1.0',
        metadata: { name: '已存在工程', created_at: '', modified_at: '', author: '', description: '' },
        resources: [{ id: 'r1' }],
        data: {
          stock_definition: {},
          tool_selection: [],
          process_steps: [],
          toolpath_config: {},
          postprocessor_config: {},
          simulation_config: {},
        },
        extensions: {},
      }
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { manifest, file_path: '/path/to/project.vrm' } },
      })
      const store = useProjectStore()
      const result = await store.openProject('/path/to/project.vrm')
      expect(result).toBe(true)
      expect(store.manifest.metadata.name).toBe('已存在工程')
      expect(store.currentFilePath).toBe('/path/to/project.vrm')
      expect(store.isModified).toBe(false)
      expect(store.loading).toBe(false)
    })

    it('通过 upload_data 打开工程成功', async () => {
      const manifest = {
        version: '1.0',
        metadata: { name: '上传工程', created_at: '', modified_at: '', author: '', description: '' },
        resources: [],
        data: {
          stock_definition: {},
          tool_selection: [],
          process_steps: [],
          toolpath_config: {},
          postprocessor_config: {},
          simulation_config: {},
        },
        extensions: {},
      }
      ;(http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { manifest } },
      })
      const store = useProjectStore()
      const result = await store.openProject(undefined, 'base64data')
      expect(result).toBe(true)
      expect(store.manifest.metadata.name).toBe('上传工程')
      // 无 file_path 返回时回退到传入的 filePath（此处为空）
      expect(store.currentFilePath).toBe('')
      expect(http.post).toHaveBeenCalledWith(expect.any(String), {
        file_path: undefined,
        upload_data: 'base64data',
      })
    })

    it('后端返回非 0 code 时设置 error 并返回 false', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1, message: '文件不存在' },
      })
      const store = useProjectStore()
      const result = await store.openProject('/missing.vrm')
      expect(result).toBe(false)
      expect(store.error).toBe('文件不存在')
    })

    it('网络异常时提取错误信息', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('timeout'))
      const store = useProjectStore()
      const result = await store.openProject('/x.vrm')
      expect(result).toBe(false)
      expect(store.error).toBe('timeout')
    })
  })

  describe('saveProject', () => {
    it('保存成功时更新 currentFilePath 并清除 isModified', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { file_path: '/saved/project.vrm' } },
      })
      const store = useProjectStore()
      store.$patch({ isModified: true })
      const result = await store.saveProject()
      expect(result).toBe(true)
      expect(store.currentFilePath).toBe('/saved/project.vrm')
      expect(store.isModified).toBe(false)
      expect(store.loading).toBe(false)
    })

    it('保存时传入 outputName 参数', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { file_path: '/saved/custom.vrm' } },
      })
      const store = useProjectStore()
      await store.saveProject('custom')
      expect(http.post).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({ output_name: 'custom' }),
      )
    })

    it('后端返回非 0 code 时设置 error', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1, message: '磁盘空间不足' },
      })
      const store = useProjectStore()
      const result = await store.saveProject()
      expect(result).toBe(false)
      expect(store.error).toBe('磁盘空间不足')
    })

    it('网络异常时使用默认错误信息', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue({})
      const store = useProjectStore()
      const result = await store.saveProject()
      expect(result).toBe(false)
      expect(store.error).toBe('保存工程失败')
    })
  })

  describe('saveAsProject', () => {
    it('另存为成功时更新文件路径和工程名', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { file_path: '/new/as-save.vrm' } },
      })
      const store = useProjectStore()
      const result = await store.saveAsProject('as-save.vrm')
      expect(result).toBe(true)
      expect(store.currentFilePath).toBe('/new/as-save.vrm')
      expect(store.manifest.metadata.name).toBe('as-save')
      expect(store.isModified).toBe(false)
    })

    it('后端返回非 0 code 时设置 error', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1, message: '权限不足' },
      })
      const store = useProjectStore()
      const result = await store.saveAsProject('x.vrm')
      expect(result).toBe(false)
      expect(store.error).toBe('权限不足')
    })

    it('网络异常时提取错误信息', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue({
        response: { data: { detail: '服务器错误' } },
      })
      const store = useProjectStore()
      const result = await store.saveAsProject('x.vrm')
      expect(result).toBe(false)
      expect(store.error).toBe('服务器错误')
    })
  })

  describe('fetchProjectList', () => {
    it('获取列表成功时保存到 projectList', async () => {
      const items = [
        { path: '/a.vrm', name: 'a', created_at: '', modified_at: '', resource_count: 2, file_size: 100 },
        { path: '/b.vrm', name: 'b', created_at: '', modified_at: '', resource_count: 0, file_size: 200 },
      ]
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0, data: { items } },
      })
      const store = useProjectStore()
      await store.fetchProjectList()
      expect(store.projectList).toHaveLength(2)
      expect(store.projectList[0].name).toBe('a')
      expect(store.listLoading).toBe(false)
    })

    it('后端返回非 0 code 时不更新列表', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1 },
      })
      const store = useProjectStore()
      store.$patch({ projectList: [{ path: '/old', name: 'old', created_at: '', modified_at: '', resource_count: 0, file_size: 0 }] as never })
      await store.fetchProjectList()
      expect(store.projectList).toHaveLength(1)
      expect(store.projectList[0].name).toBe('old')
    })

    it('网络异常时设置 error', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('网络错误'))
      const store = useProjectStore()
      await store.fetchProjectList()
      expect(store.error).toBe('网络错误')
      expect(store.listLoading).toBe(false)
    })
  })

  describe('deleteProject', () => {
    it('删除成功时返回 true', async () => {
      (http.delete as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 0 },
      })
      const store = useProjectStore()
      const result = await store.deleteProject('target')
      expect(result).toBe(true)
      expect(http.delete).toHaveBeenCalledWith(expect.stringContaining('/target'))
    })

    it('后端返回非 0 code 时返回 false', async () => {
      (http.delete as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { code: 1 },
      })
      const store = useProjectStore()
      const result = await store.deleteProject('target')
      expect(result).toBe(false)
    })

    it('网络异常时返回 false', async () => {
      (http.delete as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network'))
      const store = useProjectStore()
      const result = await store.deleteProject('target')
      expect(result).toBe(false)
    })
  })

  describe('downloadProject', () => {
    it('创建 a 标签并触发点击下载', () => {
      const store = useProjectStore()
      const clickSpy = vi.fn()
      const setAttributeSpy = vi.fn()
      const fakeAnchor = {
        href: '',
        download: '',
        click: clickSpy,
        setAttribute: setAttributeSpy,
      }
      const createElementSpy = vi.spyOn(document, 'createElement').mockReturnValue(fakeAnchor as never)
      store.downloadProject('my-project')
      expect(createElementSpy).toHaveBeenCalledWith('a')
      expect(fakeAnchor.download).toBe('my-project')
      expect(clickSpy).toHaveBeenCalled()
      createElementSpy.mockRestore()
    })
  })

  describe('updateStockDefinition', () => {
    it('更新 stock_definition 并标记为已修改', () => {
      const store = useProjectStore()
      store.updateStockDefinition({ material: 'AL6061', size: '100x100' })
      expect(store.manifest.data.stock_definition).toEqual({ material: 'AL6061', size: '100x100' })
      expect(store.isModified).toBe(true)
    })
  })

  describe('updateToolSelection', () => {
    it('更新 tool_selection 并标记为已修改', () => {
      const store = useProjectStore()
      const tools = [{ id: 't1', name: 'endmill' }, { id: 't2', name: 'drill' }]
      store.updateToolSelection(tools as never)
      expect(store.manifest.data.tool_selection).toEqual(tools)
      expect(store.isModified).toBe(true)
    })
  })

  describe('updateProcessSteps', () => {
    it('更新 process_steps 并标记为已修改', () => {
      const store = useProjectStore()
      const steps = [{ name: 'roughing' }, { name: 'finishing' }]
      store.updateProcessSteps(steps as never)
      expect(store.manifest.data.process_steps).toEqual(steps)
      expect(store.isModified).toBe(true)
    })
  })

  describe('updateToolpathConfig', () => {
    it('更新 toolpath_config 并标记为已修改', () => {
      const store = useProjectStore()
      store.updateToolpathConfig({ strategy: 'parallel', stepover: 0.5 })
      expect(store.manifest.data.toolpath_config).toEqual({ strategy: 'parallel', stepover: 0.5 })
      expect(store.isModified).toBe(true)
    })
  })

  describe('updatePostProcessorConfig', () => {
    it('更新 postprocessor_config 并标记为已修改', () => {
      const store = useProjectStore()
      store.updatePostProcessorConfig({ controller: 'Fanuc', format: 'G-code' })
      expect(store.manifest.data.postprocessor_config).toEqual({ controller: 'Fanuc', format: 'G-code' })
      expect(store.isModified).toBe(true)
    })
  })

  describe('updateSimulationConfig', () => {
    it('更新 simulation_config 并标记为已修改', () => {
      const store = useProjectStore()
      store.updateSimulationConfig({ resolution: 'high', show_toolpath: true })
      expect(store.manifest.data.simulation_config).toEqual({ resolution: 'high', show_toolpath: true })
      expect(store.isModified).toBe(true)
    })
  })

  describe('updateExtensions', () => {
    it('更新 extensions 并标记为已修改', () => {
      const store = useProjectStore()
      store.updateExtensions({ custom_field: 'value' })
      expect(store.manifest.extensions).toEqual({ custom_field: 'value' })
      expect(store.isModified).toBe(true)
    })
  })

  describe('resetProject', () => {
    it('重置所有状态为初始值', () => {
      const store = useProjectStore()
      store.$patch({
        projectId: 'p_001',
        currentFilePath: '/x.vrm',
        isModified: true,
        error: 'some error',
        manifest: {
          ...store.manifest,
          metadata: { ...store.manifest.metadata, name: 'modified' },
        } as never,
      })
      store.resetProject()
      expect(store.projectId).toBe('')
      expect(store.currentFilePath).toBe('')
      expect(store.isModified).toBe(false)
      expect(store.error).toBeNull()
      expect(store.manifest.metadata.name).toBe('未命名工程')
    })
  })
})
