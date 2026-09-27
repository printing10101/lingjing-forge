// DialectManager.vue 方言管理页测试
//
// 注意：ElTable 在 happy-dom 测试环境被 stub（见 src/tests/setup.ts），不渲染
// 数据行，因此行点击交互通过组件 defineExpose 暴露的 handleSelect 驱动。
// 完整交互链路由后端 API 测试（tests/api/test_postprocessor_dialects.py）覆盖。

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  listDialects: vi.fn(),
  getDialectDetail: vi.fn(),
  readTemplate: vi.fn(),
  previewDialect: vi.fn(),
  createDialect: vi.fn(),
  saveTemplate: vi.fn(),
  deleteDialect: vi.fn(),
  getDialectParams: vi.fn(),
  saveDialectParams: vi.fn(),
}))

// Mock API 模块（避免真实 HTTP 请求）
vi.mock('@/api/postprocessorDialects', () => ({
  listDialects: mocks.listDialects,
  getDialectDetail: mocks.getDialectDetail,
  readTemplate: mocks.readTemplate,
  previewDialect: mocks.previewDialect,
  createDialect: mocks.createDialect,
  saveTemplate: mocks.saveTemplate,
  deleteDialect: mocks.deleteDialect,
  getDialectParams: mocks.getDialectParams,
  saveDialectParams: mocks.saveDialectParams,
}))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: vi.fn().mockResolvedValue(undefined),
}))

// Mock ElMessageBox.confirm：happy-dom 下无确认交互，直接 resolve（同意删除）
vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal<typeof import('element-plus')>()
  return {
    ...actual,
    ElMessageBox: {
      ...actual.ElMessageBox,
      confirm: vi.fn().mockResolvedValue('confirm'),
    },
    ElMessage: {
      ...actual.ElMessage,
      success: vi.fn(),
      error: vi.fn(),
      warning: vi.fn(),
    },
  }
})

import DialectManager from '@/views/DialectManager.vue'

const builtinDialects = [
  {
    id: 'fanuc_0i',
    name: 'Fanuc 0i-MF',
    version: 'builtin',
    extends: null,
    source: 'builtin',
    template_methods: [],
    params_keys: [],
    hooks: null,
    author: '',
    description: '',
  },
]

const declaredDialects = [
  {
    id: 'gsk_980_25i',
    name: 'GSK 980/25i (Guangzhou CNC)',
    version: '1.0.0',
    extends: 'fanuc_0i',
    source: 'declared',
    template_methods: ['format_arc', 'format_header', 'format_footer'],
    params_keys: [],
    hooks: null,
    author: 'Lingjing Manufacturing Team',
    description: '广州数控 GSK 980/25i',
  },
]

const gskDetail = {
  ...declaredDialects[0],
  is_declared: true,
  compile_ok: true,
  compile_error: null,
  templates: {
    format_arc: 'arc.j2',
    format_header: 'header.j2',
    format_footer: 'footer.j2',
  },
}

const mountView = () =>
  mount(DialectManager, {
    global: {
      mocks: {
        $t: (key: string, params?: Record<string, unknown>) =>
          params ? `${key}:${JSON.stringify(params)}` : key,
      },
    },
  })

describe('DialectManager.vue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.listDialects.mockResolvedValue({
      dialects: [...builtinDialects, ...declaredDialects],
      total: 2,
      declared: 1,
      compile_errors: {},
    })
    mocks.getDialectDetail.mockResolvedValue(gskDetail)
    mocks.readTemplate.mockResolvedValue({
      dialect_id: 'gsk_980_25i',
      method: 'format_header',
      path: 'postprocessor-plugins/gsk_980_25i/templates/header.j2',
      content: 'O{{ "%04d" | format(program_number) }}',
    })
    mocks.previewDialect.mockResolvedValue({
      dialect_id: 'gsk_980_25i',
      program_number: 1000,
      output: '%\nO1000 (GSK)\nG30 X0. Y0.\nM30\n%',
    })
    mocks.createDialect.mockResolvedValue({ id: 'new_dialect', name: 'New', extends: 'fanuc_0i' })
    mocks.saveTemplate.mockResolvedValue({ dialect_id: 'gsk_980_25i', method: 'format_header', path: 'x' })
    mocks.deleteDialect.mockResolvedValue({ id: 'gsk_980_25i' })
    mocks.getDialectParams.mockResolvedValue({
      dialect_id: 'gsk_980_25i',
      effective: { safe_z_height: 80, spindle: { max_rpm: 24000 }, rapid_feed: 10000 },
      dialect_params: {},
      base_keys: ['safe_z_height', 'spindle', 'rapid_feed'],
    })
    mocks.saveDialectParams.mockResolvedValue({ dialect_id: 'gsk_980_25i', params: {} })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('挂载时加载方言列表', async () => {
    mountView()
    await flushPromises()
    expect(mocks.listDialects).toHaveBeenCalledTimes(1)
  })

  it('列表加载失败不阻塞页面', async () => {
    mocks.listDialects.mockRejectedValue(new Error('backend down'))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.exists()).toBe(true)
  })

  it('页面标题渲染（真实 i18n）', async () => {
    const wrapper = mountView()
    await flushPromises()
    // setup.ts 注入真实 i18n（zh-CN），标题应为中文「后处理器方言」
    expect(wrapper.text()).toContain('后处理器方言')
  })

  it('选择声明式方言：加载详情并读取首个模板', async () => {
    const wrapper = mountView()
    await flushPromises()
    // 通过 defineExpose 暴露的 handleSelect 驱动选择（ElTable stub 不渲染行）
    const vm = wrapper.vm as unknown as {
      handleSelect: (row: unknown) => Promise<void>
    }
    await vm.handleSelect(declaredDialects[0])
    await flushPromises()

    expect(mocks.getDialectDetail).toHaveBeenCalledWith('gsk_980_25i')
    expect(mocks.readTemplate).toHaveBeenCalled()
    // 模板内容渲染进模板编辑器（el-input textarea stub）
    expect(wrapper.find('.template-editor').exists()).toBe(true)
  })

  it('生成预览显示 NC 输出', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      handleSelect: (row: unknown) => Promise<void>
      loadPreview: () => Promise<void>
    }
    await vm.handleSelect(declaredDialects[0])
    await flushPromises()
    await vm.loadPreview()
    await flushPromises()

    expect(mocks.previewDialect).toHaveBeenCalledWith('gsk_980_25i')
    const output = wrapper.find('.nc-output')
    expect(output.text()).toContain('G30 X0. Y0.')
  })

  it('预览失败不崩溃', async () => {
    mocks.previewDialect.mockRejectedValue(new Error('preview failed'))
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      handleSelect: (row: unknown) => Promise<void>
      loadPreview: () => Promise<void>
    }
    await vm.handleSelect(declaredDialects[0])
    await flushPromises()
    await vm.loadPreview()
    await flushPromises()
    expect(wrapper.exists()).toBe(true)
  })

  it('新建方言：调用 createDialect 并刷新列表', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      createForm: { id: string; name: string; extends: string }
      handleCreate: () => Promise<void>
    }
    vm.createForm.id = 'new_machine'
    vm.createForm.name = 'New Machine'
    vm.createForm.extends = 'fanuc_0i'
    await vm.handleCreate()
    await flushPromises()

    expect(mocks.createDialect).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'new_machine', name: 'New Machine', extends: 'fanuc_0i' }),
    )
    // 创建成功后刷新列表
    expect(mocks.listDialects).toHaveBeenCalledTimes(2)
  })

  it('新建方言缺必填字段不调用 API', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as unknown as { handleCreate: () => Promise<void> }
    await vm.handleCreate()
    await flushPromises()
    expect(mocks.createDialect).not.toHaveBeenCalled()
  })

  it('保存模板：调用 saveTemplate 并刷新预览', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      handleSelect: (row: unknown) => Promise<void>
      loadTemplate: () => Promise<void>
      handleSaveTemplate: () => Promise<void>
    }
    await vm.handleSelect(declaredDialects[0])
    await flushPromises()
    await vm.loadTemplate()
    await flushPromises()
    await vm.handleSaveTemplate()
    await flushPromises()

    expect(mocks.saveTemplate).toHaveBeenCalledWith(
      'gsk_980_25i',
      'format_arc', // template_methods[0]
      expect.any(String),
    )
  })

  it('删除方言：调用 deleteDialect 并清空选择', async () => {
    // ElMessageBox.confirm 在 happy-dom 下直接 resolve（stub 无确认交互）
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      handleSelect: (row: unknown) => Promise<void>
      handleDelete: () => Promise<void>
    }
    await vm.handleSelect(declaredDialects[0])
    await flushPromises()
    await vm.handleDelete()
    await flushPromises()

    expect(mocks.deleteDialect).toHaveBeenCalledWith('gsk_980_25i')
  })

  it('选择声明式方言后加载参数', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      handleSelect: (row: unknown) => Promise<void>
    }
    await vm.handleSelect(declaredDialects[0])
    await flushPromises()

    expect(mocks.getDialectParams).toHaveBeenCalledWith('gsk_980_25i')
  })

  it('保存参数：调用 saveDialectParams 并刷新', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      handleSelect: (row: unknown) => Promise<void>
      handleSaveParams: () => Promise<void>
      paramsJson: string
    }
    await vm.handleSelect(declaredDialects[0])
    await flushPromises()
    vm.paramsJson = '{"safe_z_height": 100}'
    await vm.handleSaveParams()
    await flushPromises()

    expect(mocks.saveDialectParams).toHaveBeenCalledWith('gsk_980_25i', { safe_z_height: 100 })
  })

  it('保存非法 JSON 不调用 API', async () => {
    const wrapper = mountView()
    await flushPromises()
    const vm = wrapper.vm as unknown as {
      handleSelect: (row: unknown) => Promise<void>
      handleSaveParams: () => Promise<void>
      paramsJson: string
    }
    await vm.handleSelect(declaredDialects[0])
    await flushPromises()
    vm.paramsJson = '{invalid json'
    await vm.handleSaveParams()
    await flushPromises()

    expect(mocks.saveDialectParams).not.toHaveBeenCalled()
  })
})
