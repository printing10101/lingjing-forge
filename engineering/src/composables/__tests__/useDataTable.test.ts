// P3-1 Headless 数据表格逻辑测试（纯逻辑，不依赖 Element Plus）
import { describe, it, expect, vi } from 'vitest'

import {
  clampPage,
  nextDirection,
  useDataTable,
  type PageResult,
} from '@/composables/headless/useDataTable'

interface Row {
  id: number
  name: string
}

function makeFetcher(data: Row[], total = data.length) {
  return vi.fn(async (q: { page: number; size: number }) => {
    const start = (q.page - 1) * q.size
    return { items: data.slice(start, start + q.size), total } as PageResult<Row>
  })
}

const rowKey = (r: Row) => r.id

describe('clampPage', () => {
  it('clamps below 1 to 1', () => {
    expect(clampPage(0, 100, 20)).toBe(1)
    expect(clampPage(-3, 100, 20)).toBe(1)
  })

  it('clamps non-finite to 1', () => {
    expect(clampPage(Number.NaN, 100, 20)).toBe(1)
    expect(clampPage(Number.POSITIVE_INFINITY, 100, 20)).toBe(1)
  })

  it('clamps above last page', () => {
    expect(clampPage(10, 50, 20)).toBe(3)
  })

  it('keeps valid page', () => {
    expect(clampPage(2, 50, 20)).toBe(2)
  })

  it('empty dataset still yields page 1', () => {
    expect(clampPage(5, 0, 20)).toBe(1)
  })
})

describe('nextDirection', () => {
  it('cycles asc -> desc -> null -> asc', () => {
    expect(nextDirection('asc')).toBe('desc')
    expect(nextDirection('desc')).toBe(null)
    expect(nextDirection(null)).toBe('asc')
  })
})

describe('useDataTable', () => {
  const data: Row[] = Array.from({ length: 25 }, (_, i) => ({ id: i + 1, name: `r${i + 1}` }))

  it('loads first page on init', async () => {
    const fetcher = makeFetcher(data)
    const t = useDataTable<Row>({ pageSize: 10, fetcher, rowKey })
    await t.reload()
    expect(fetcher).toHaveBeenCalledWith(expect.objectContaining({ page: 1, size: 10 }))
    expect(t.items.value).toHaveLength(10)
    expect(t.total.value).toBe(25)
  })

  it('setPage triggers fetcher with new page', async () => {
    const fetcher = makeFetcher(data)
    const t = useDataTable<Row>({ pageSize: 10, fetcher, rowKey })
    await t.reload()
    t.setPage(2)
    await vi.waitFor(() => {
      expect(fetcher).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }))
    })
  })

  it('setPage is idempotent for same page', async () => {
    const fetcher = makeFetcher(data)
    const t = useDataTable<Row>({ pageSize: 10, fetcher, rowKey })
    await t.reload()
    const calls = fetcher.mock.calls.length
    t.setPage(1) // 已是第 1 页
    expect(fetcher.mock.calls.length).toBe(calls)
  })

  it('setPageSize resets to page 1', async () => {
    const fetcher = makeFetcher(data)
    const t = useDataTable<Row>({ pageSize: 10, fetcher, rowKey })
    await t.reload()
    t.setPage(2)
    await vi.waitFor(() => expect(fetcher).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })))
    t.setPageSize(20)
    await vi.waitFor(() => {
      expect(fetcher).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, size: 20 }))
    })
  })

  it('sortBy cycles directions and resets page', async () => {
    const fetcher = makeFetcher(data)
    const t = useDataTable<Row>({ pageSize: 10, fetcher, rowKey })
    await t.reload()
    t.sortBy('name')
    expect(t.sort.value).toEqual({ prop: 'name', direction: 'asc' })
    t.sortBy('name')
    expect(t.sort.value).toEqual({ prop: 'name', direction: 'desc' })
    t.sortBy('name')
    expect(t.sort.value).toBeNull()
  })

  it('toggleRow toggles selection with dedup', async () => {
    const fetcher = makeFetcher(data)
    const t = useDataTable<Row>({ pageSize: 10, fetcher, rowKey })
    await t.reload()
    const first = t.items.value[0]
    t.toggleRow(first)
    expect(t.selectedKeys.value).toEqual([first.id])
    t.toggleRow(first)
    expect(t.selectedKeys.value).toEqual([])
  })

  it('toggleSelectAll selects current page rows', async () => {
    const fetcher = makeFetcher(data)
    const t = useDataTable<Row>({ pageSize: 10, fetcher, rowKey })
    await t.reload()
    t.toggleSelectAll()
    expect(t.selectedKeys.value).toHaveLength(10)
    expect(t.isAllSelected.value).toBe(true)
    t.toggleSelectAll()
    expect(t.selectedKeys.value).toHaveLength(0)
  })

  it('selectedRows returns filtered current-page rows', async () => {
    const fetcher = makeFetcher(data)
    const t = useDataTable<Row>({ pageSize: 10, fetcher, rowKey })
    await t.reload()
    t.toggleRow(t.items.value[0])
    t.toggleRow(t.items.value[1])
    expect(t.selectedRows.value).toHaveLength(2)
  })

  it('reload failure sets errorMessage and clears items', async () => {
    const fetcher = vi.fn(async () => {
      throw new Error('boom')
    })
    const t = useDataTable<Row>({ pageSize: 10, fetcher, rowKey })
    await t.reload()
    expect(t.errorMessage.value).toBe('boom')
    expect(t.items.value).toEqual([])
  })

  it('page clamp after reload when last page emptied', async () => {
    // total 收缩为 1 页数据，当前第 2 页 → 回退第 1 页
    const fetcher = makeFetcher(data.slice(0, 10), 10)
    const t = useDataTable<Row>({ pageSize: 10, fetcher, rowKey })
    await t.reload()
    t.setPage(2) // 页码 2，但 total=10 → 钳制回 1
    await vi.waitFor(() => {
      expect(t.page.value).toBe(1)
    })
  })

  it('setExtraQuery resets page and passes query', async () => {
    const fetcher = makeFetcher(data)
    const t = useDataTable<Row, { material: string }>({ pageSize: 10, fetcher, rowKey })
    await t.reload()
    t.setPage(2)
    await vi.waitFor(() => expect(fetcher).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })))
    t.setExtraQuery({ material: 'AL6061' })
    await vi.waitFor(() => {
      expect(fetcher).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 1, material: 'AL6061' }),
      )
    })
  })
})
