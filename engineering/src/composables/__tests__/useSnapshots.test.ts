/**
 * useSnapshots composable 单元测试
 *
 * 覆盖范围：
 *   1. REST API 无状态函数（listSnapshots / getSnapshot / createSnapshot /
 *      reproduceSnapshot）—— URL、参数、响应解包、异常透传
 *   2. useSnapshots 聚合 composable —— 列表加载、分页、筛选、详情选择、
 *      创建并刷新、复现、清空、onUnmounted 生命周期清理
 *
 * 对应 ADR-005 阶段 2 验收标准（前端"实验快照"视图 + "一键复现"按钮）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import type { ExperimentSnapshot } from '@/contracts/observability'

// Mock 依赖：http 模块
const mocks = vi.hoisted(() => {
  return {
    http: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
    },
  }
})

vi.mock('@/utils/http', () => ({
  default: mocks.http,
}))

// 导入被测模块（在所有 mock 注册之后）
import {
  listSnapshots,
  getSnapshot,
  createSnapshot,
  reproduceSnapshot,
  useSnapshots,
} from '../useSnapshots'
import { API_CONFIG, buildApiPath } from '@/config/api'

// 测试数据构造

const BASE = API_CONFIG.SNAPSHOTS

function makeSnapshot(overrides: Partial<ExperimentSnapshot> = {}): ExperimentSnapshot {
  return {
    snapshot_id: 'snap-001-abcdefgh',
    created_at: '2026-07-13T10:00:00Z',
    created_by: 'user-1',
    git_sha: 'abc123def456789',
    code_dirty: false,
    config: { lr: 0.001, epochs: 100 },
    dataset_versions: ['dataset://phm2010/v1'],
    model_uri: 'model://ltc-v1',
    metrics: { val_loss: 0.06, pcc: 0.51 },
    environment: { python: '3.10', torch: '2.0.1' },
    ...overrides,
  }
}

/** 后端统一响应壳：{ code, message, data, request_id }，code !== 0 由 http 拦截器抛错。 */
function envelope<T>(data: T): { data: { data: T } } {
  return { data: { data } }
}

// 测试用例

describe('useSnapshots - REST API 无状态函数', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

// listSnapshots
  describe('listSnapshots', () => {
    it('GET / 带查询参数返回列表', async () => {
      const params = {
        created_by: 'user-1',
        git_sha: 'abc123',
        limit: 10,
        offset: 0,
        detail: false,
      }
      const listPayload = {
        items: [makeSnapshot({ snapshot_id: 'snap-001' })],
        limit: 10,
        offset: 0,
      }
      mocks.http.get.mockResolvedValueOnce(envelope(listPayload))

      const result = await listSnapshots(params)

      expect(mocks.http.get).toHaveBeenCalledWith(
        buildApiPath(BASE, ''),
        { params },
      )
      expect(result.items).toHaveLength(1)
      expect(result.items[0].snapshot_id).toBe('snap-001')
      expect(result.limit).toBe(10)
      expect(result.offset).toBe(0)
    })

    it('无参数调用使用默认空对象', async () => {
      mocks.http.get.mockResolvedValueOnce(
        envelope({ items: [], limit: 20, offset: 0 }),
      )
      await listSnapshots()
      expect(mocks.http.get).toHaveBeenCalledWith(
        buildApiPath(BASE, ''),
        { params: {} },
      )
    })

    it('http 抛错时透传异常', async () => {
      mocks.http.get.mockRejectedValueOnce(new Error('网络错误'))
      await expect(listSnapshots()).rejects.toThrow('网络错误')
    })
  })

// getSnapshot
  describe('getSnapshot', () => {
    it('GET /{snapshotId} 返回快照详情', async () => {
      const snapId = 'snap-001'
      const snap = makeSnapshot({ snapshot_id: snapId })
      mocks.http.get.mockResolvedValueOnce(envelope(snap))

      const result = await getSnapshot(snapId)

      expect(mocks.http.get).toHaveBeenCalledWith(
        buildApiPath(BASE, `/${snapId}`),
      )
      expect(result.snapshot_id).toBe(snapId)
      expect(result.git_sha).toBe('abc123def456789')
      expect(result.config.lr).toBe(0.001)
      expect(result.metrics.pcc).toBe(0.51)
    })

    it('http 抛错时透传异常', async () => {
      mocks.http.get.mockRejectedValueOnce(new Error('快照不存在'))
      await expect(getSnapshot('snap-x')).rejects.toThrow('快照不存在')
    })
  })

// createSnapshot
  describe('createSnapshot', () => {
    it('POST / 返回 snapshot_id + created_at', async () => {
      const body = {
        config: { lr: 0.001 },
        dataset_versions: ['dataset://x/v1'],
        model_uri: 'model://ltc-v2',
        metrics: { val_loss: 0.05 },
        created_by: 'user-2',
        notes: 'baseline',
      }
      mocks.http.post.mockResolvedValueOnce(
        envelope({ snapshot_id: 'snap-new', created_at: '2026-07-13T11:00:00Z' }),
      )

      const result = await createSnapshot(body)

      expect(mocks.http.post).toHaveBeenCalledWith(
        buildApiPath(BASE, ''),
        body,
      )
      expect(result.snapshot_id).toBe('snap-new')
      expect(result.created_at).toBe('2026-07-13T11:00:00Z')
    })

    it('http 抛错时透传异常', async () => {
      mocks.http.post.mockRejectedValueOnce(new Error('权限不足'))
      await expect(
        createSnapshot({
          config: {},
          dataset_versions: [],
          model_uri: '',
          metrics: {},
          created_by: '',
        }),
      ).rejects.toThrow('权限不足')
    })
  })

// reproduceSnapshot
  describe('reproduceSnapshot', () => {
    it('POST /{snapshotId}/reproduce 返回 workflow_run_id', async () => {
      const snapId = 'snap-001'
      mocks.http.post.mockResolvedValueOnce(
        envelope({ workflow_run_id: 'wf-repro-001', snapshot_id: snapId }),
      )

      const result = await reproduceSnapshot(snapId)

      expect(mocks.http.post).toHaveBeenCalledWith(
        buildApiPath(BASE, `/${snapId}/reproduce`),
      )
      expect(result.workflow_run_id).toBe('wf-repro-001')
      expect(result.snapshot_id).toBe(snapId)
    })

    it('http 抛错时透传异常', async () => {
      mocks.http.post.mockRejectedValueOnce(new Error('该快照不支持一键复现'))
      await expect(reproduceSnapshot('snap-x')).rejects.toThrow('该快照不支持一键复现')
    })
  })
})

// useSnapshots 聚合 composable 测试

describe('useSnapshots - 聚合 composable', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

// loadSnapshots
  describe('loadSnapshots', () => {
    it('调用 listSnapshots 并更新 snapshots 列表', async () => {
      mocks.http.get.mockResolvedValueOnce(
        envelope({
          items: [
            makeSnapshot({ snapshot_id: 'snap-001' }),
            makeSnapshot({ snapshot_id: 'snap-002' }),
          ],
          limit: 20,
          offset: 0,
        }),
      )

      const { loadSnapshots, snapshots, loading, totalCount } = useSnapshots()
      expect(loading.value).toBe(false)

      const promise = loadSnapshots()
      expect(loading.value).toBe(true)

      await promise

      expect(loading.value).toBe(false)
      expect(snapshots.value).toHaveLength(2)
      expect(snapshots.value[0].snapshot_id).toBe('snap-001')
      // totalCount = items.length + offset = 2 + 0
      expect(totalCount.value).toBe(2)
    })

    it('使用分页参数（currentPage + pageSize）', async () => {
      mocks.http.get.mockResolvedValueOnce(
        envelope({ items: [], limit: 10, offset: 20 }),
      )

      const snap = useSnapshots()
      snap.currentPage.value = 3
      snap.pageSize.value = 10

      await snap.loadSnapshots()

      expect(mocks.http.get).toHaveBeenCalledWith(
        buildApiPath(BASE, ''),
        {
          params: {
            limit: 10,
            offset: 20, // (3-1)*10
            detail: false,
          },
        },
      )
    })

    it('携带筛选参数（created_by / git_sha / model_uri）', async () => {
      mocks.http.get.mockResolvedValueOnce(
        envelope({ items: [], limit: 20, offset: 0 }),
      )

      const snap = useSnapshots()
      snap.filterCreatedBy.value = 'user-1'
      snap.filterGitSha.value = 'abc123'
      snap.filterModelUri.value = 'model://ltc-v1'

      await snap.loadSnapshots()

      expect(mocks.http.get).toHaveBeenCalledWith(
        buildApiPath(BASE, ''),
        {
          params: {
            limit: 20,
            offset: 0,
            detail: false,
            created_by: 'user-1',
            git_sha: 'abc123',
            model_uri: 'model://ltc-v1',
          },
        },
      )
    })

    it('部分筛选为空时不携带对应参数', async () => {
      mocks.http.get.mockResolvedValueOnce(
        envelope({ items: [], limit: 20, offset: 0 }),
      )

      const snap = useSnapshots()
      snap.filterCreatedBy.value = 'user-1'
      // git_sha / model_uri 留空

      await snap.loadSnapshots()

      const callArgs = mocks.http.get.mock.calls[0][1]
      expect(callArgs.params.created_by).toBe('user-1')
      expect(callArgs.params.git_sha).toBeUndefined()
      expect(callArgs.params.model_uri).toBeUndefined()
    })

    it('失败时不抛错，仅 console.warn', async () => {
      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
      mocks.http.get.mockRejectedValueOnce(new Error('网络错误'))

      const { loadSnapshots, loading, snapshots } = useSnapshots()
      await expect(loadSnapshots()).resolves.toBeUndefined()

      expect(loading.value).toBe(false)
      expect(snapshots.value).toHaveLength(0)
      expect(warnSpy).toHaveBeenCalled()
      warnSpy.mockRestore()
    })

    it('totalCount 估算为 items.length + offset', async () => {
      mocks.http.get.mockResolvedValueOnce(
        envelope({
          items: [makeSnapshot({ snapshot_id: 'snap-001' })],
          limit: 10,
          offset: 20,
        }),
      )

      const { loadSnapshots, totalCount } = useSnapshots()
      await loadSnapshots()

      // 1 + 20 = 21
      expect(totalCount.value).toBe(21)
    })
  })

// resetFilters
  describe('resetFilters', () => {
    it('清空所有筛选并回到第 1 页，然后重新加载', async () => {
      mocks.http.get.mockResolvedValueOnce(
        envelope({ items: [], limit: 20, offset: 0 }),
      )

      const snap = useSnapshots()
      snap.filterCreatedBy.value = 'user-1'
      snap.filterGitSha.value = 'abc'
      snap.filterModelUri.value = 'model://x'
      snap.currentPage.value = 5

      await snap.resetFilters()

      expect(snap.filterCreatedBy.value).toBe('')
      expect(snap.filterGitSha.value).toBe('')
      expect(snap.filterModelUri.value).toBe('')
      expect(snap.currentPage.value).toBe(1)
      expect(mocks.http.get).toHaveBeenCalled()
    })
  })

// selectSnapshot
  describe('selectSnapshot', () => {
    it('调用 getSnapshot 并更新 currentSnapshot', async () => {
      const snap = makeSnapshot({ snapshot_id: 'snap-001' })
      mocks.http.get.mockResolvedValueOnce(envelope(snap))

      const { selectSnapshot, currentSnapshot, currentLoading } = useSnapshots()
      expect(currentSnapshot.value).toBeNull()

      const promise = selectSnapshot('snap-001')
      expect(currentLoading.value).toBe(true)

      await promise

      expect(currentLoading.value).toBe(false)
      expect(currentSnapshot.value?.snapshot_id).toBe('snap-001')
      expect(currentSnapshot.value?.git_sha).toBe('abc123def456789')
    })

    it('失败时 console.warn 且 currentSnapshot 置空', async () => {
      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
      mocks.http.get.mockRejectedValueOnce(new Error('快照不存在'))

      const { selectSnapshot, currentSnapshot, currentLoading } = useSnapshots()
      await expect(selectSnapshot('snap-x')).resolves.toBeUndefined()

      expect(currentLoading.value).toBe(false)
      expect(currentSnapshot.value).toBeNull()
      expect(warnSpy).toHaveBeenCalled()
      warnSpy.mockRestore()
    })
  })

// clearCurrent
  describe('clearCurrent', () => {
    it('清空当前详情', async () => {
      const snap = makeSnapshot({ snapshot_id: 'snap-001' })
      mocks.http.get.mockResolvedValueOnce(envelope(snap))

      const s = useSnapshots()
      await s.selectSnapshot('snap-001')
      expect(s.currentSnapshot.value).not.toBeNull()

      s.clearCurrent()
      expect(s.currentSnapshot.value).toBeNull()
    })
  })

// submitSnapshot
  describe('submitSnapshot', () => {
    it('调用 createSnapshot 并刷新列表，返回 snapshot_id', async () => {
      mocks.http.post.mockResolvedValueOnce(
        envelope({ snapshot_id: 'snap-new', created_at: '2026-07-13T11:00:00Z' }),
      )
      mocks.http.get.mockResolvedValueOnce(
        envelope({
          items: [makeSnapshot({ snapshot_id: 'snap-new' })],
          limit: 20,
          offset: 0,
        }),
      )

      const { submitSnapshot, creating, snapshots } = useSnapshots()
      const body = {
        config: { lr: 0.001 },
        dataset_versions: ['dataset://x/v1'],
        model_uri: 'model://ltc-v2',
        metrics: { val_loss: 0.05 },
        created_by: 'user-2',
      }

      expect(creating.value).toBe(false)
      const promise = submitSnapshot(body)
      expect(creating.value).toBe(true)

      const newId = await promise

      expect(creating.value).toBe(false)
      expect(newId).toBe('snap-new')
      expect(mocks.http.post).toHaveBeenCalledWith(
        buildApiPath(BASE, ''),
        body,
      )
      // 创建成功后应自动刷新列表
      expect(mocks.http.get).toHaveBeenCalled()
      expect(snapshots.value[0].snapshot_id).toBe('snap-new')
    })

    it('createSnapshot 抛错时 creating 复位并透传异常', async () => {
      mocks.http.post.mockRejectedValueOnce(new Error('权限不足'))

      const { submitSnapshot, creating } = useSnapshots()
      await expect(
        submitSnapshot({
          config: {},
          dataset_versions: [],
          model_uri: '',
          metrics: {},
          created_by: '',
        }),
      ).rejects.toThrow('权限不足')

      expect(creating.value).toBe(false)
    })
  })

// reproduce
  describe('reproduce', () => {
    it('调用 reproduceSnapshot 并返回 workflow_run_id', async () => {
      mocks.http.post.mockResolvedValueOnce(
        envelope({ workflow_run_id: 'wf-repro-001', snapshot_id: 'snap-001' }),
      )

      const { reproduce, reproducing } = useSnapshots()
      expect(reproducing.value).toBe(false)

      const promise = reproduce('snap-001')
      expect(reproducing.value).toBe(true)

      const runId = await promise

      expect(reproducing.value).toBe(false)
      expect(runId).toBe('wf-repro-001')
      expect(mocks.http.post).toHaveBeenCalledWith(
        buildApiPath(BASE, '/snap-001/reproduce'),
      )
    })

    it('reproduceSnapshot 抛错时 reproducing 复位并透传异常', async () => {
      mocks.http.post.mockRejectedValueOnce(new Error('该快照不支持一键复现'))

      const { reproduce, reproducing } = useSnapshots()
      await expect(reproduce('snap-x')).rejects.toThrow('该快照不支持一键复现')
      expect(reproducing.value).toBe(false)
    })
  })

// onUnmounted 生命周期
  describe('onUnmounted 生命周期', () => {
    it('组件卸载时清空 currentSnapshot（通过真实组件挂载验证）', async () => {
      // onUnmounted 只在组件实例作用域生效；effectScope 无 currentInstance，
      // 回调不会注册。用真实组件挂载/卸载触发。
      const { defineComponent } = await import('vue')
      const { mount } = await import('@vue/test-utils')
      const TestHost = defineComponent({
        setup() {
          return useSnapshots()
        },
        template: '<div />',
      })

      const wrapper = mount(TestHost)
      // 模拟 selectSnapshot 填充 currentSnapshot（vm proxy 赋值写入 ref.value）
      wrapper.vm.currentSnapshot = makeSnapshot({ snapshot_id: 'snap-001' })
      expect(wrapper.vm.currentSnapshot).not.toBeNull()

      wrapper.unmount()

      // 卸载后 onUnmounted 回调置 null
      expect(wrapper.vm.currentSnapshot).toBeNull()
    })
  })
})
