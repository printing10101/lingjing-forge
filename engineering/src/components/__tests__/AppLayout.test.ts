/* eslint-disable vue/no-unused-vars */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { shallowMount, VueWrapper } from '@vue/test-utils'
import AppLayout from '@/components/AppLayout.vue'

// Mock vue-i18n
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      if (params && 'n' in params) {
        return `${key}.${params.n}`
      }
      return key
    },
  }),
}))

// Mock vue-router
const mockRoute = vi.hoisted(() => ({ path: '/' }))
const mockRouter = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
  useRouter: () => mockRouter,
  RouterLink: { template: '<a class="router-link"><slot /></a>' },
  RouterView: { template: '<div class="router-view"></div>' },
}))

// Mock @element-plus/icons-vue
vi.mock('@element-plus/icons-vue', () => ({
  Folder: { name: 'Folder', template: '<i class="icon-folder" />' },
  Search: { name: 'Search', template: '<i class="icon-search" />' },
  Refresh: { name: 'Refresh', template: '<i class="icon-refresh" />' },
  Bell: { name: 'Bell', template: '<i class="icon-bell" />' },
  DocumentAdd: { name: 'DocumentAdd', template: '<i class="icon-doc-add" />' },
  FolderOpened: { name: 'FolderOpened', template: '<i class="icon-folder-opened" />' },
  Document: { name: 'Document', template: '<i class="icon-document" />' },
  CopyDocument: { name: 'CopyDocument', template: '<i class="icon-copy-document" />' },
  Download: { name: 'Download', template: '<i class="icon-download" />' },
  Upload: { name: 'Upload', template: '<i class="icon-upload" />' },
  DocumentCopy: { name: 'DocumentCopy', template: '<i class="icon-document-copy" />' },
}))

// Mock http
const mockHttpGet = vi.hoisted(() => vi.fn())
vi.mock('@/utils/http', () => ({
  default: {
    get: mockHttpGet,
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

// Mock error-handler
vi.mock('@/utils/error-handler', () => ({
  extractErrorMessage: vi.fn((error: unknown) => String(error)),
}))

// Mock BackendStatusIndicator component
vi.mock('@/components/BackendStatusIndicator.vue', () => ({
  default: {
    name: 'BackendStatusIndicator',
    template: '<div class="mock-backend-status-indicator"></div>',
  },
}))

// Mock ElTooltip / ElDropdown / ElDropdownMenu / ElDropdownItem / ElButton / ElTag / ElDivider / ElIcon
vi.mock('element-plus', () => ({
  ElTooltip: { template: '<div class="el-tooltip"><slot /></div>', props: ['content', 'placement'] },
  ElDropdown: {
    template: '<div class="el-dropdown" @click="$emit(\'command\', \'new\')"><slot /><slot name="dropdown" /></div>',
    props: ['trigger', 'placement'],
    emits: ['command'],
  },
  ElDropdownMenu: { template: '<div class="el-dropdown-menu"><slot /></div>' },
  ElDropdownItem: {
    template: '<div class="el-dropdown-item" @click="$emit(\'click\')"><slot /></div>',
    props: ['command', 'divided'],
    emits: ['click'],
  },
  ElButton: {
    template: '<button class="el-button" @click="$emit(\'click\', $event)"><slot /></button>',
    props: ['type', 'loading', 'disabled', 'size', 'text', 'icon', 'circle'],
    emits: ['click'],
  },
  ElTag: { template: '<span class="el-tag"><slot /></span>', props: ['size', 'type', 'effect'] },
  ElDivider: { template: '<hr class="el-divider" />', props: ['direction'] },
  ElIcon: { template: '<span class="el-icon"><slot /></span>', props: ['size'] },
}))

// Mock sub-components to simulate shallow rendering with proper class names
vi.mock('@/components/layout/LayoutSidebar.vue', () => ({
  default: {
    name: 'LayoutSidebar',
    template: `
      <aside class="layout-sidebar">
        <div class="sidebar-brand">
          <div class="brand-logo"><svg /></div>
          <span class="brand-name">appLayout.brandName</span>
        </div>
        <nav class="sidebar-nav">
          <div class="nav-group"><span class="nav-group-label">核心功能</span></div>
          <div class="nav-group"><span class="nav-group-label">资源管理</span></div>
          <div class="nav-group"><span class="nav-group-label">智能模块</span></div>
          <div class="nav-group"><span class="nav-group-label">数据与运营</span></div>
          <div class="nav-group"><span class="nav-group-label">市场与工具</span></div>
          <div class="nav-group"><span class="nav-group-label">系统与帮助</span></div>
        </nav>
      </aside>
    `,
  },
}))

vi.mock('@/components/layout/LayoutHeader.vue', () => ({
  default: {
    name: 'LayoutHeader',
    props: ['projectName', 'isModified'],
    emits: ['file-command', 'refresh'],
    template: `
      <header class="layout-header">
        <div class="header-search">
          <input class="search-input" />
        </div>
        <div class="header-actions">
          <button class="header-btn">refresh</button>
          <div class="mock-backend-status-indicator"></div>
          <span v-if="projectName" class="project-indicator">{{ projectName }}</span>
        </div>
      </header>
    `,
  },
}))

describe('AppLayout.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
    mockHttpGet.mockResolvedValue({ data: { code: 0, data: [] } })
  })

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount()
    }
  })

  const mountComponent = (props: Record<string, unknown> = {}) => {
    wrapper = shallowMount(AppLayout, {
      props,
      global: {
        stubs: {
          'router-link': { template: '<a class="router-link"><slot /></a>' },
          'router-view': { template: '<div class="router-view"></div>' },
        },
      },
    })
    return wrapper
  }

  describe('组件挂载', () => {
    it('应该正确挂载组件', async () => {
      mountComponent()
      await wrapper.vm.$nextTick()
      expect(wrapper.exists()).toBe(true)
      expect(wrapper.find('.app-layout').exists()).toBe(true)
    })

    it('应该渲染侧边栏组件', async () => {
      mountComponent()
      await wrapper.vm.$nextTick()
      expect(wrapper.findComponent({ name: 'LayoutSidebar' }).exists()).toBe(true)
    })

    it('应该渲染主区域', async () => {
      mountComponent()
      await wrapper.vm.$nextTick()
      expect(wrapper.find('.layout-main').exists()).toBe(true)
    })

    it('应该渲染头部组件', async () => {
      mountComponent()
      await wrapper.vm.$nextTick()
      expect(wrapper.findComponent({ name: 'LayoutHeader' }).exists()).toBe(true)
    })

    it('应该渲染内容区域', async () => {
      mountComponent()
      await wrapper.vm.$nextTick()
      expect(wrapper.find('.layout-content').exists()).toBe(true)
    })
  })

  describe('props 传递', () => {
    it('projectName 和 isModified 应传递给 LayoutHeader', async () => {
      mountComponent({ projectName: '测试项目', isModified: true })
      await wrapper.vm.$nextTick()
      const header = wrapper.findComponent({ name: 'LayoutHeader' })
      expect(header.props('projectName')).toBe('测试项目')
      expect(header.props('isModified')).toBe(true)
    })
  })

  describe('事件转发', () => {
    it('LayoutHeader 的 refresh 事件应触发 AppLayout 的 refresh 事件', async () => {
      mountComponent()
      await wrapper.vm.$nextTick()
      const header = wrapper.findComponent({ name: 'LayoutHeader' })
      header.vm.$emit('refresh')
      await wrapper.vm.$nextTick()
      expect(wrapper.emitted('refresh')).toBeTruthy()
    })

    it('LayoutHeader 的 file-command 事件应触发 AppLayout 的 file-command 事件', async () => {
      mountComponent()
      await wrapper.vm.$nextTick()
      const header = wrapper.findComponent({ name: 'LayoutHeader' })
      header.vm.$emit('file-command', 'save')
      await wrapper.vm.$nextTick()
      expect(wrapper.emitted('file-command')).toBeTruthy()
      expect(wrapper.emitted('file-command')![0]).toEqual(['save'])
    })

    it('file-command 不同命令应正确转发', async () => {
      mountComponent()
      await wrapper.vm.$nextTick()
      const header = wrapper.findComponent({ name: 'LayoutHeader' })
      header.vm.$emit('file-command', 'new')
      header.vm.$emit('file-command', 'open')
      header.vm.$emit('file-command', 'import-step')
      await wrapper.vm.$nextTick()
      const emitted = wrapper.emitted('file-command')
      expect(emitted).toBeTruthy()
      expect(emitted!.length).toBe(3)
      expect(emitted![0]).toEqual(['new'])
      expect(emitted![1]).toEqual(['open'])
      expect(emitted![2]).toEqual(['import-step'])
    })
  })
})