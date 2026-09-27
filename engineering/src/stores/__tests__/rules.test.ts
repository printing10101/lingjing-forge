import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useRuleStore } from '@/stores/rules'

// mock http 客户端
vi.mock('@/utils/http', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

// 保留 error-handler 真实逻辑
vi.mock('@/utils/error-handler', async () => {
  const actual = await vi.importActual<typeof import('@/utils/error-handler')>('@/utils/error-handler')
  return { ...actual }
})

// mock triggerFileDownload
const triggerFileDownloadMock = vi.hoisted(() => vi.fn())
vi.mock('@/utils/download', () => ({
  triggerFileDownload: triggerFileDownloadMock,
}))

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

describe('useRuleStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  describe('initial state', () => {
    it('初始 rules 为空数组', () => {
      const store = useRuleStore()
      expect(store.rules).toEqual([])
    })

    it('初始 groups 为空数组', () => {
      const store = useRuleStore()
      expect(store.groups).toEqual([])
    })

    it('初始 currentRule 为 null', () => {
      const store = useRuleStore()
      expect(store.currentRule).toBeNull()
    })

    it('初始 stats 为 null', () => {
      const store = useRuleStore()
      expect(store.stats).toBeNull()
    })

    it('初始 loading 为 false', () => {
      const store = useRuleStore()
      expect(store.loading).toBe(false)
    })

    it('初始 showDialog 为 false', () => {
      const store = useRuleStore()
      expect(store.showDialog).toBe(false)
    })

    it('初始 editingRule 为 null', () => {
      const store = useRuleStore()
      expect(store.editingRule).toBeNull()
    })

    it('初始 showGroupDialog 为 false', () => {
      const store = useRuleStore()
      expect(store.showGroupDialog).toBe(false)
    })

    it('初始 editingGroup 为 null', () => {
      const store = useRuleStore()
      expect(store.editingGroup).toBeNull()
    })

    it('初始分页字段默认值', () => {
      const store = useRuleStore()
      expect(store.totalRules).toBe(0)
      expect(store.currentPage).toBe(1)
      expect(store.pageSize).toBe(20)
      expect(store.totalPages).toBe(0)
    })
  })

  describe('computed', () => {
    it('activeRules 过滤出 status=active 的规则', () => {
      const store = useRuleStore()
      store.$patch({
        rules: [
          { id: 1, status: 'active', name: 'r1' },
          { id: 2, status: 'draft', name: 'r2' },
          { id: 3, status: 'active', name: 'r3' },
        ] as never,
      })
      expect(store.activeRules).toHaveLength(2)
    })

    it('draftRules 过滤出 status=draft 的规则', () => {
      const store = useRuleStore()
      store.$patch({
        rules: [
          { id: 1, status: 'active', name: 'r1' },
          { id: 2, status: 'draft', name: 'r2' },
        ] as never,
      })
      expect(store.draftRules).toHaveLength(1)
      expect(store.draftRules[0].name).toBe('r2')
    })
  })

  describe('fetchRules', () => {
    it('获取规则列表成功时更新 rules 和分页信息', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: {
          data: {
            rules: [{ id: 1, name: 'r1' }, { id: 2, name: 'r2' }],
            total: 2,
            page: 1,
            page_size: 20,
            total_pages: 1,
          },
        },
      })
      const store = useRuleStore()
      await store.fetchRules({ page: 1 })
      expect(store.rules).toHaveLength(2)
      expect(store.totalRules).toBe(2)
      expect(store.currentPage).toBe(1)
      expect(store.pageSize).toBe(20)
      expect(store.totalPages).toBe(1)
      expect(store.loading).toBe(false)
    })

    it('网络异常时显示错误提示', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockRejectedValue({
        response: { data: { message: '服务不可用' } },
      })
      const store = useRuleStore()
      await store.fetchRules()
      expect(elMessageMock.error).toHaveBeenCalledWith('服务不可用')
      expect(store.loading).toBe(false)
    })
  })

  describe('fetchGroups', () => {
    it('获取分组列表成功时更新 groups', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: {
          data: {
            groups: [{ id: 1, name: 'g1' }, { id: 2, name: 'g2' }],
          },
        },
      })
      const store = useRuleStore()
      await store.fetchGroups()
      expect(store.groups).toHaveLength(2)
    })

    it('网络异常时显示错误提示', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('网络错误'))
      const store = useRuleStore()
      await store.fetchGroups()
      expect(elMessageMock.error).toHaveBeenCalledWith('网络错误')
    })
  })

  describe('fetchStats', () => {
    it('获取统计信息成功时更新 stats', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { total_rules: 10, active_rules: 8 } },
      })
      const store = useRuleStore()
      await store.fetchStats()
      expect(store.stats).toEqual({ total_rules: 10, active_rules: 8 })
    })

    it('网络异常时不抛出错误（静默降级）', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network'))
      const store = useRuleStore()
      await expect(store.fetchStats()).resolves.toBeUndefined()
      expect(store.stats).toBeNull()
    })
  })

  describe('createRule', () => {
    it('创建成功时显示成功提示并刷新列表', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { message: '创建成功', data: { id: 10, name: 'new' } },
      })
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { rules: [], total: 0, page: 1, page_size: 20, total_pages: 0 } },
      })
      const store = useRuleStore()
      const result = await store.createRule({ name: 'new' } as never)
      expect(elMessageMock.success).toHaveBeenCalledWith('创建成功')
      expect(result).toEqual({ id: 10, name: 'new' })
      // fetchRules 和 fetchStats 都会被调用
      expect(http.get).toHaveBeenCalled()
    })

    it('后端未返回 message 时使用默认成功提示', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { id: 10 } },
      })
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { rules: [], total: 0, page: 1, page_size: 20, total_pages: 0 } },
      })
      const store = useRuleStore()
      await store.createRule({ name: 'new' } as never)
      expect(elMessageMock.success).toHaveBeenCalledWith('规则创建成功')
    })

    it('网络异常时显示错误提示并抛出', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue({
        response: { data: { message: '权限不足' } },
      })
      const store = useRuleStore()
      await expect(store.createRule({ name: 'x' } as never)).rejects.toBeDefined()
      expect(elMessageMock.error).toHaveBeenCalledWith('权限不足')
    })
  })

  describe('updateRule', () => {
    it('更新成功时显示成功提示', async () => {
      (http.put as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { message: '更新成功', data: { id: 1 } },
      })
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { rules: [], total: 0, page: 1, page_size: 20, total_pages: 0 } },
      })
      const store = useRuleStore()
      const result = await store.updateRule(1, { name: 'updated' } as never)
      expect(elMessageMock.success).toHaveBeenCalledWith('更新成功')
      expect(result).toEqual({ id: 1 })
    })

    it('网络异常时显示错误提示并抛出', async () => {
      (http.put as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('timeout'))
      const store = useRuleStore()
      await expect(store.updateRule(1, {} as never)).rejects.toBeDefined()
      expect(elMessageMock.error).toHaveBeenCalledWith('timeout')
    })
  })

  describe('deleteRule', () => {
    it('删除成功时显示成功提示', async () => {
      (http.delete as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { message: '删除成功' },
      })
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { rules: [], total: 0, page: 1, page_size: 20, total_pages: 0 } },
      })
      const store = useRuleStore()
      await store.deleteRule(1)
      expect(elMessageMock.success).toHaveBeenCalledWith('删除成功')
    })

    it('网络异常时显示错误提示并抛出', async () => {
      (http.delete as ReturnType<typeof vi.fn>).mockRejectedValue({
        response: { data: { message: '规则不存在' } },
      })
      const store = useRuleStore()
      await expect(store.deleteRule(1)).rejects.toBeDefined()
      expect(elMessageMock.error).toHaveBeenCalledWith('规则不存在')
    })
  })

  describe('getRule', () => {
    it('获取规则详情成功时保存到 currentRule', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { id: 1, name: 'detail' } },
      })
      const store = useRuleStore()
      const result = await store.getRule(1)
      expect(store.currentRule).toEqual({ id: 1, name: 'detail' })
      expect(result).toEqual({ id: 1, name: 'detail' })
    })

    it('网络异常时显示错误提示并抛出', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network'))
      const store = useRuleStore()
      await expect(store.getRule(1)).rejects.toBeDefined()
      expect(elMessageMock.error).toHaveBeenCalledWith('network')
    })
  })

  describe('createGroup', () => {
    it('创建分组成功时显示成功提示并刷新分组列表', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { message: '分组创建成功', data: { id: 1 } },
      })
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { groups: [] } },
      })
      const store = useRuleStore()
      const result = await store.createGroup({ name: 'g1' } as never)
      expect(elMessageMock.success).toHaveBeenCalledWith('分组创建成功')
      expect(result).toEqual({ id: 1 })
    })

    it('网络异常时显示错误提示并抛出', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('网络错误'))
      const store = useRuleStore()
      await expect(store.createGroup({ name: 'x' } as never)).rejects.toBeDefined()
      expect(elMessageMock.error).toHaveBeenCalledWith('网络错误')
    })
  })

  describe('updateGroup', () => {
    it('更新分组成功时显示成功提示', async () => {
      (http.put as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { id: 1, name: 'updated' } },
      })
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { groups: [] } },
      })
      const store = useRuleStore()
      const result = await store.updateGroup(1, { name: 'updated' } as never)
      expect(elMessageMock.success).toHaveBeenCalledWith('分组更新成功')
      expect(result).toEqual({ id: 1, name: 'updated' })
    })

    it('网络异常时显示错误提示并抛出', async () => {
      (http.put as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('失败'))
      const store = useRuleStore()
      await expect(store.updateGroup(1, {} as never)).rejects.toBeDefined()
      expect(elMessageMock.error).toHaveBeenCalledWith('失败')
    })
  })

  describe('deleteGroup', () => {
    it('删除分组成功时显示成功提示', async () => {
      (http.delete as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { message: '分组删除成功' },
      })
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { groups: [] } },
      })
      const store = useRuleStore()
      await store.deleteGroup(1)
      expect(elMessageMock.success).toHaveBeenCalledWith('分组删除成功')
    })

    it('网络异常时显示错误提示并抛出', async () => {
      (http.delete as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('失败'))
      const store = useRuleStore()
      await expect(store.deleteGroup(1)).rejects.toBeDefined()
      expect(elMessageMock.error).toHaveBeenCalledWith('失败')
    })
  })

  describe('exportRules', () => {
    it('导出成功时触发文件下载', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: new Blob(['{}']),
        headers: { 'content-disposition': 'attachment; filename="rules.json"' },
      })
      const store = useRuleStore()
      await store.exportRules()
      expect(triggerFileDownloadMock).toHaveBeenCalled()
      expect(elMessageMock.success).toHaveBeenCalledWith('规则导出成功')
    })

    it('导出时无 content-disposition 时使用默认文件名', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: new Blob(['{}']),
        headers: {},
      })
      const store = useRuleStore()
      await store.exportRules()
      expect(triggerFileDownloadMock).toHaveBeenCalledWith(expect.any(Blob), 'rules_export.json')
    })

    it('导出时 content-disposition 含无引号文件名时正确解析', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: new Blob(['{}']),
        headers: { 'content-disposition': 'attachment; filename=custom.json' },
      })
      const store = useRuleStore()
      await store.exportRules()
      expect(triggerFileDownloadMock).toHaveBeenCalledWith(expect.any(Blob), 'custom.json')
    })

    it('导出失败时显示错误提示并抛出', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('导出失败'))
      const store = useRuleStore()
      await expect(store.exportRules()).rejects.toBeDefined()
      expect(elMessageMock.error).toHaveBeenCalledWith('导出失败')
    })
  })

  describe('importRules', () => {
    it('导入成功时显示成功提示并刷新列表', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: {
          message: '导入完成',
          data: { imported_rules: 5, imported_groups: 2 },
        },
      })
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { rules: [], total: 0, page: 1, page_size: 20, total_pages: 0 } },
      })
      const store = useRuleStore()
      const file = new File(['{}'], 'rules.json', { type: 'application/json' })
      const result = await store.importRules(file)
      expect(elMessageMock.success).toHaveBeenCalledWith('导入完成')
      expect(result).toEqual({ imported_rules: 5, imported_groups: 2 })
    })

    it('后端未返回 message 时使用默认提示', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { imported_rules: 3, imported_groups: 1 } },
      })
      ;(http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { rules: [], total: 0, page: 1, page_size: 20, total_pages: 0 } },
      })
      const store = useRuleStore()
      const file = new File(['{}'], 'rules.json', { type: 'application/json' })
      await store.importRules(file)
      expect(elMessageMock.success).toHaveBeenCalledWith('导入成功: 3 条规则, 1 个分组')
    })

    it('导入失败时显示错误提示并抛出', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('文件格式错误'))
      const store = useRuleStore()
      const file = new File(['{}'], 'rules.json', { type: 'application/json' })
      await expect(store.importRules(file)).rejects.toBeDefined()
      expect(elMessageMock.error).toHaveBeenCalledWith('文件格式错误')
    })
  })

  describe('backupDatabase', () => {
    it('备份成功时显示成功提示并返回数据', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { message: '备份成功', data: { backup_path: '/backup/db.sqlite' } },
      })
      const store = useRuleStore()
      const result = await store.backupDatabase()
      expect(elMessageMock.success).toHaveBeenCalledWith('备份成功')
      expect(result).toEqual({ backup_path: '/backup/db.sqlite' })
    })

    it('后端未返回 message 时使用默认提示', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: { backup_path: '/x' } },
      })
      const store = useRuleStore()
      await store.backupDatabase()
      expect(elMessageMock.success).toHaveBeenCalledWith('数据库备份成功')
    })

    it('备份失败时显示错误提示并抛出', async () => {
      (http.post as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('磁盘满'))
      const store = useRuleStore()
      await expect(store.backupDatabase()).rejects.toBeDefined()
      expect(elMessageMock.error).toHaveBeenCalledWith('磁盘满')
    })
  })

  describe('openCreateDialog', () => {
    it('打开创建对话框时清空 editingRule 并显示对话框', () => {
      const store = useRuleStore()
      store.$patch({ editingRule: { id: 1, name: 'old' } as never, showDialog: false })
      store.openCreateDialog()
      expect(store.editingRule).toBeNull()
      expect(store.showDialog).toBe(true)
    })
  })

  describe('openEditDialog', () => {
    it('打开编辑对话框时填充 editingRule 并显示对话框', () => {
      const store = useRuleStore()
      const rule = { id: 5, name: 'edit' }
      store.openEditDialog(rule as never)
      expect(store.editingRule).toEqual(rule)
      expect(store.showDialog).toBe(true)
    })

    it('编辑对话框的 editingRule 是副本（修改不影响原对象）', () => {
      const store = useRuleStore()
      const rule = { id: 5, name: 'edit' }
      store.openEditDialog(rule as never)
      store.editingRule!.name = 'modified'
      expect(rule.name).toBe('edit')
    })
  })

  describe('openCreateGroupDialog', () => {
    it('打开分组创建对话框时清空 editingGroup 并显示对话框', () => {
      const store = useRuleStore()
      store.$patch({ editingGroup: { id: 1, name: 'old' } as never, showGroupDialog: false })
      store.openCreateGroupDialog()
      expect(store.editingGroup).toBeNull()
      expect(store.showGroupDialog).toBe(true)
    })
  })

  describe('openEditGroupDialog', () => {
    it('打开分组编辑对话框时填充 editingGroup 并显示对话框', () => {
      const store = useRuleStore()
      const group = { id: 3, name: 'g-edit' }
      store.openEditGroupDialog(group as never)
      expect(store.editingGroup).toEqual(group)
      expect(store.showGroupDialog).toBe(true)
    })
  })

  describe('refreshAll', () => {
    it('调用时触发 fetchRules、fetchGroups、fetchStats', async () => {
      (http.get as ReturnType<typeof vi.fn>).mockResolvedValue({
        data: { data: {} },
      })
      const store = useRuleStore()
      store.refreshAll()
      // 三个并发请求，http.get 至少被调用 3 次
      await new Promise((r) => setTimeout(r, 10))
      expect(http.get).toHaveBeenCalledTimes(3)
    })
  })
})
