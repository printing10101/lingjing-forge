import { describe, it, expect, vi, beforeEach } from 'vitest'
import http from '@/utils/http'
import { API_CONFIG, buildApiPath } from '@/config/api'
import {
  extractParams,
  generateModel,
  generateProcessPlanning,
  generateNC,
  exportSimulationAnimation,
} from '@/api/nl2cad'

vi.mock('@/utils/http', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

const BASE = API_CONFIG.NL2CAD

describe('nl2cad API', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  describe('extractParams', () => {
    it('返回包含 params 字段的响应（{data:{params}} 结构）', async () => {
      const params = { diameter: 10, height: 20 }
      vi.mocked(http.post).mockResolvedValueOnce({
        data: { data: { params } },
      })

      const result = await extractParams({ description: '一个直径10高度20的圆柱' })

      expect(http.post).toHaveBeenCalledWith(
        buildApiPath(BASE, 'extract-params'),
        { description: '一个直径10高度20的圆柱' },
      )
      expect(result.params).toEqual(params)
    })

    it('返回 { params } 结构时直接返回 body', async () => {
      const params = { x: 1 }
      vi.mocked(http.post).mockResolvedValueOnce({
        data: { params },
      })

      const result = await extractParams({ description: 'test' })

      expect(result.params).toEqual({ x: 1 })
    })

    it('body 不含 params 字段时，将 body 包成 params', async () => {
      const body = { diameter: 5, height: 10 }
      vi.mocked(http.post).mockResolvedValueOnce({
        data: { data: body },
      })

      const result = await extractParams({ description: 'test' })

      expect(result.params).toEqual(body)
    })

    it('body 为 null 时返回 { params: {} }', async () => {
      vi.mocked(http.post).mockResolvedValueOnce({
        data: { data: null },
      })

      const result = await extractParams({ description: 'test' })

      expect(result.params).toEqual({})
    })

    it('resp.data 为 null 时返回 { params: {} }', async () => {
      vi.mocked(http.post).mockResolvedValueOnce({ data: null })

      const result = await extractParams({ description: 'test' })

      expect(result.params).toEqual({})
    })

    it('空描述字符串也能正常调用', async () => {
      vi.mocked(http.post).mockResolvedValueOnce({
        data: { data: { params: {} } },
      })

      const result = await extractParams({ description: '' })

      expect(http.post).toHaveBeenCalledWith(buildApiPath(BASE, 'extract-params'), {
        description: '',
      })
      expect(result.params).toEqual({})
    })

    it('网络错误时抛出异常', async () => {
      vi.mocked(http.post).mockRejectedValueOnce(new Error('network down'))

      await expect(
        extractParams({ description: 'test' }),
      ).rejects.toThrow('network down')
    })
  })

  describe('generateModel', () => {
    it('成功生成 3D 模型（默认输出格式）', async () => {
      const response = { model_path: '/tmp/model.stl', params: { diameter: 10 } }
      vi.mocked(http.post).mockResolvedValueOnce({ data: { data: response } })

      const result = await generateModel({ description: '圆柱' })

      expect(http.post).toHaveBeenCalledWith(buildApiPath(BASE, 'generate'), {
        description: '圆柱',
      })
      expect(result.model_path).toBe('/tmp/model.stl')
      expect(result.params).toEqual({ diameter: 10 })
    })

    it('指定输出格式 step', async () => {
      const response = { model_path: '/tmp/model.step' }
      vi.mocked(http.post).mockResolvedValueOnce({ data: { data: response } })

      await generateModel({ description: '圆柱', output_format: 'step' })

      expect(http.post).toHaveBeenCalledWith(buildApiPath(BASE, 'generate'), {
        description: '圆柱',
        output_format: 'step',
      })
    })

    it('指定输出格式 obj', async () => {
      vi.mocked(http.post).mockResolvedValueOnce({
        data: { data: { model_path: '/m.obj' } },
      })

      await generateModel({ description: 'x', output_format: 'obj' })

      expect(http.post).toHaveBeenCalledWith(buildApiPath(BASE, 'generate'), {
        description: 'x',
        output_format: 'obj',
      })
    })

    it('回退到 resp.data', async () => {
      const response = { model_path: '/tmp/model.stl' }
      vi.mocked(http.post).mockResolvedValueOnce({ data: response })

      const result = await generateModel({ description: 'x' })

      expect(result.model_path).toBe('/tmp/model.stl')
    })

    it('请求失败时抛出异常', async () => {
      vi.mocked(http.post).mockRejectedValueOnce(new Error('500'))

      await expect(
        generateModel({ description: 'x' }),
      ).rejects.toThrow('500')
    })
  })

  describe('generateProcessPlanning', () => {
    it('成功生成工艺规划', async () => {
      const processPlan = { operations: [{ name: '粗加工' }, { name: '精加工' }] }
      vi.mocked(http.post).mockResolvedValueOnce({
        data: { data: { process_plan: processPlan } },
      })

      const result = await generateProcessPlanning({
        cad_params: { diameter: 10 },
        material: '铝',
        machine_type: 'CNC',
        precision: '0.01',
      })

      expect(http.post).toHaveBeenCalledWith(buildApiPath(BASE, 'process-planning'), {
        cad_params: { diameter: 10 },
        material: '铝',
        machine_type: 'CNC',
        precision: '0.01',
      })
      expect(result.process_plan).toEqual(processPlan)
    })

    it('仅必填字段（cad_params）也能调用', async () => {
      vi.mocked(http.post).mockResolvedValueOnce({
        data: { data: { process_plan: {} } },
      })

      await generateProcessPlanning({ cad_params: { x: 1 } })

      expect(http.post).toHaveBeenCalledWith(buildApiPath(BASE, 'process-planning'), {
        cad_params: { x: 1 },
      })
    })

    it('回退到 resp.data', async () => {
      const processPlan = { steps: [] }
      vi.mocked(http.post).mockResolvedValueOnce({ data: { process_plan: processPlan } })

      const result = await generateProcessPlanning({ cad_params: {} })

      expect(result.process_plan).toEqual(processPlan)
    })

    it('请求失败时抛出异常', async () => {
      vi.mocked(http.post).mockRejectedValueOnce(new Error('400 Bad Request'))

      await expect(
        generateProcessPlanning({ cad_params: {} }),
      ).rejects.toThrow('400 Bad Request')
    })
  })

  describe('generateNC', () => {
    it('成功生成 NC 代码', async () => {
      const ncCode = 'G01 X10 Y20\nM30'
      vi.mocked(http.post).mockResolvedValueOnce({
        data: { data: { nc_code: ncCode } },
      })

      const result = await generateNC({
        process_plan: { operations: [] },
        machine_type: 'HAAS',
      })

      expect(http.post).toHaveBeenCalledWith(buildApiPath(BASE, 'generate-nc'), {
        process_plan: { operations: [] },
        machine_type: 'HAAS',
      })
      expect(result.nc_code).toBe(ncCode)
    })

    it('仅必填字段（process_plan）也能调用', async () => {
      vi.mocked(http.post).mockResolvedValueOnce({
        data: { data: { nc_code: '' } },
      })

      await generateNC({ process_plan: {} })

      expect(http.post).toHaveBeenCalledWith(buildApiPath(BASE, 'generate-nc'), {
        process_plan: {},
      })
    })

    it('回退到 resp.data', async () => {
      vi.mocked(http.post).mockResolvedValueOnce({ data: { nc_code: 'G01' } })

      const result = await generateNC({ process_plan: {} })

      expect(result.nc_code).toBe('G01')
    })

    it('请求失败时抛出异常', async () => {
      vi.mocked(http.post).mockRejectedValueOnce(new Error('server error'))

      await expect(
        generateNC({ process_plan: {} }),
      ).rejects.toThrow('server error')
    })
  })

  describe('exportSimulationAnimation', () => {
    it('默认 gif 格式导出 Blob', async () => {
      const blob = new Blob(['fake-gif'], { type: 'image/gif' })
      vi.mocked(http.post).mockResolvedValueOnce({ data: blob })

      const result = await exportSimulationAnimation({ nc_code: 'G01' })

      expect(http.post).toHaveBeenCalledWith(
        buildApiPath(API_CONFIG.SIMULATION, 'export-animation'),
        { nc_code: 'G01' },
        { responseType: 'blob' },
      )
      expect(result).toBeInstanceOf(Blob)
      expect(result.size).toBeGreaterThan(0)
    })

    it('指定 mp4 格式', async () => {
      const blob = new Blob(['fake-mp4'], { type: 'video/mp4' })
      vi.mocked(http.post).mockResolvedValueOnce({ data: blob })

      await exportSimulationAnimation({ nc_code: 'G01', format: 'mp4' })

      expect(http.post).toHaveBeenCalledWith(
        buildApiPath(API_CONFIG.SIMULATION, 'export-animation'),
        { nc_code: 'G01', format: 'mp4' },
        { responseType: 'blob' },
      )
    })

    it('空 nc_code 也能调用', async () => {
      const blob = new Blob([])
      vi.mocked(http.post).mockResolvedValueOnce({ data: blob })

      const result = await exportSimulationAnimation({ nc_code: '' })

      expect(http.post).toHaveBeenCalledWith(
        buildApiPath(API_CONFIG.SIMULATION, 'export-animation'),
        { nc_code: '' },
        { responseType: 'blob' },
      )
      expect(result).toBeInstanceOf(Blob)
    })

    it('请求失败时抛出异常', async () => {
      vi.mocked(http.post).mockRejectedValueOnce(new Error('export failed'))

      await expect(
        exportSimulationAnimation({ nc_code: 'G01' }),
      ).rejects.toThrow('export failed')
    })
  })
})
