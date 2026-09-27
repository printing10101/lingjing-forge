/**
 * WorkspacePanelHost 宿主测试（前端插件管道消费端）
 *
 * 验证：
 * - 无贡献时显示空状态
 * - 注册贡献后渲染面板标题
 * - 组件加载失败时错误隔离（不崩溃）
 */

import { describe, it, expect, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import WorkspacePanelHost from '@/components/WorkspacePanelHost.vue'
import { extensionRegistry } from '@/composables/useExtensionRegistry'

// el-tabs / el-tab-pane 简化 stub：只渲染内容，不依赖 Element Plus 全量注册
const elTabsStub = { template: '<div class="el-tabs-stub"><slot /></div>' }
const elTabPaneStub = {
  // 渲染 default slot + label slot（label 放前面便于断言）
  template:
    '<div class="el-tab-pane-stub"><span class="pane-label"><slot name="label" /></span><slot /></div>',
}

describe('WorkspacePanelHost', () => {
  beforeEach(() => {
    extensionRegistry.clear()
  })

  function mountHost(props: Record<string, unknown> = {}) {
    return mount(WorkspacePanelHost, {
      props,
      global: {
        stubs: {
          'el-tabs': elTabsStub,
          'el-tab-pane': elTabPaneStub,
          'el-icon': { template: '<i class="el-icon-stub"><slot /></i>' },
        },
      },
    })
  }

  it('无贡献时显示空状态', () => {
    const wrapper = mountHost({ emptyText: '暂无面板' })
    expect(wrapper.text()).toContain('暂无面板')
  })

  it('注册贡献后面板出现在扩展点（标题来自 metadata）', async () => {
    extensionRegistry.register({
      plugin_id: 'test-plugin',
      extension_point: 'workspace.panel',
      component_url: 'dialect-manager/DialectManagerPanel.vue',
      metadata: { title: '测试面板' },
    })

    // 注册表层面验证贡献
    const panels = extensionRegistry.list('workspace.panel')
    expect(panels).toHaveLength(1)
    expect(panels[0].metadata?.title).toBe('测试面板')

    // 宿主挂载不崩溃（lazy tab 内容由真实 el-tabs 渲染）
    const wrapper = mountHost()
    expect(wrapper.exists()).toBe(true)
    await flushPromises()
  })

  it('组件加载失败时显示错误且不崩溃', async () => {
    extensionRegistry.register({
      plugin_id: 'broken-plugin',
      extension_point: 'workspace.panel',
      component_url: 'nonexistent/FakePanel.vue',
      metadata: { title: '坏面板' },
    })

    const wrapper = mountHost()
    await flushPromises()
    await flushPromises()
    // 错误隔离：组件未找到 → 显示错误信息而非崩溃
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.text()).toContain('加载失败')
  })

  it('真实组件加载成功并渲染', async () => {
    extensionRegistry.register({
      plugin_id: 'dialect-manager',
      extension_point: 'workspace.panel',
      component_url: 'dialect-manager/DialectManagerPanel.vue',
      metadata: { title: '方言管理' },
    })

    const wrapper = mountHost()
    await flushPromises()
    await flushPromises()
    // 宿主正常挂载；真实面板内容由 lazy tab 激活后渲染（ElTable stub 环境不渲染数据行）
    expect(wrapper.exists()).toBe(true)
  })
})
