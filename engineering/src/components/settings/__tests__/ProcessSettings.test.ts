/* eslint-disable vue/no-unused-vars */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, VueWrapper } from '@vue/test-utils'
import ProcessSettings from '@/components/settings/ProcessSettings.vue'

// Mock vue-i18n
vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}))

// Mock @element-plus/icons-vue
vi.mock('@element-plus/icons-vue', () => ({
  Download: { name: 'Download', template: '<i class="icon-download" />' },
  Delete: { name: 'Delete', template: '<i class="icon-delete" />' },
  Check: { name: 'Check', template: '<i class="icon-check" />' },
  Setting: { name: 'Setting', template: '<i class="icon-setting" />' },
  Document: { name: 'Document', template: '<i class="icon-document" />' },
}))

// Mock useSettingsStore
const mockSaveSettings = vi.hoisted(() => vi.fn())
const mockStore = {
  settings: {
    logSettings: {
      logLevel: 'INFO',
      maxFileSizeMB: 50,
      retentionDays: 30,
      exportDays: 7,
    },
  },
  saveSettings: mockSaveSettings,
}
vi.mock('@/stores/settings', () => ({
  useSettingsStore: () => mockStore,
}))

// Mock useAuditLog composable
const mockAuditLogs = ref([])
const mockAuditLogStatistics = ref(null)
const mockLoadingLogs = ref(false)
const mockLogSearchKeyword = ref('')
const mockLogDetailVisible = ref(false)
const mockSelectedLog = ref(null)
const mockLogFilters = ref({ decision: '', dateRange: [] })
const mockLogPagination = ref({ page: 1, pageSize: 20, total: 0 })
const mockLoadAuditLogs = vi.hoisted(() => vi.fn())
const mockSearchLogs = vi.hoisted(() => vi.fn())
const mockExportLogs = vi.hoisted(() => vi.fn())
const mockClearLogs = vi.hoisted(() => vi.fn())
const mockViewLogDetail = vi.hoisted(() => vi.fn())
const mockGetModuleName = vi.fn((m: string) => `模块:${m}`)
const mockGetDecisionName = vi.fn((d: string) => `决策:${d}`)
const mockGetDecisionType = vi.fn(() => 'info')
const mockGetStatusName = vi.fn((s: string) => `状态:${s}`)
const mockGetStatusType = vi.fn(() => 'info')

import { ref } from 'vue'

vi.mock('@/composables/useAuditLog', () => ({
  useAuditLog: () => ({
    auditLogs: mockAuditLogs,
    auditLogStatistics: mockAuditLogStatistics,
    loadingLogs: mockLoadingLogs,
    exporting: ref(false),
    clearing: ref(false),
    logSearchKeyword: mockLogSearchKeyword,
    logDetailVisible: mockLogDetailVisible,
    selectedLog: mockSelectedLog,
    logFilters: mockLogFilters,
    logPagination: mockLogPagination,
    loadAuditLogs: mockLoadAuditLogs,
    searchLogs: mockSearchLogs,
    exportLogs: mockExportLogs,
    clearLogs: mockClearLogs,
    viewLogDetail: mockViewLogDetail,
    getModuleName: mockGetModuleName,
    getDecisionName: mockGetDecisionName,
    getDecisionType: mockGetDecisionType,
    getStatusName: mockGetStatusName,
    getStatusType: mockGetStatusType,
  }),
}))

// Mock useSettings composable
const mockExportingLogs = ref(false)
const mockExportProgress = ref(0)
const mockExportResult = ref(null)
const mockExportSystemLogs = vi.hoisted(() => vi.fn())
const mockFormatTimestamp = vi.fn((ts: number) => `时间:${ts}`)

vi.mock('@/composables/useSettings', () => ({
  useSettings: () => ({
    exportingLogs: mockExportingLogs,
    exportProgress: mockExportProgress,
    exportResult: mockExportResult,
    exportSystemLogs: mockExportSystemLogs,
    formatTimestamp: mockFormatTimestamp,
  }),
}))

describe('ProcessSettings.vue', () => {
  let wrapper: VueWrapper<any>

  beforeEach(() => {
    vi.clearAllMocks()
    mockAuditLogs.value = []
    mockAuditLogStatistics.value = null
    mockLoadingLogs.value = false
    mockExportingLogs.value = false
  })

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount()
    }
  })

  const mountComponent = () => {
    wrapper = mount(ProcessSettings, {
      global: {
        stubs: {
          'el-icon': { template: '<span class="el-icon"><slot /></span>' },
          'el-button': {
            template: '<button class="el-button" @click="$emit(\'click\', $event)"><slot /></button>',
            props: ['type', 'size', 'loading', 'disabled', 'plain'],
            emits: ['click'],
          },
          'el-form': { template: '<form class="el-form"><slot /></form>', props: ['model', 'labelWidth'] },
          'el-form-item': { template: '<div class="el-form-item"><slot /></div>', props: ['label'] },
          'el-select': { template: '<select class="el-select"><slot /></select>', props: ['modelValue', 'placeholder'] },
          'el-option': { template: '<option class="el-option" />', props: ['label', 'value'] },
          'el-input': { template: '<input class="el-input" />', props: ['modelValue', 'placeholder', 'clearable', 'size'] },
          'el-input-number': { template: '<input class="el-input-number" />', props: ['modelValue', 'min', 'max', 'step'] },
          'el-date-picker': { template: '<div class="el-date-picker" />', props: ['modelValue', 'type', 'rangeSeparator'] },
          'el-divider': { template: '<hr class="el-divider" />' },
          'el-table': { template: '<table class="el-table"><slot /></table>', props: ['data', 'stripe'] },
          'el-table-column': { template: '<td class="el-table-column" />', props: ['label', 'width', 'prop'] },
          'el-pagination': { template: '<div class="el-pagination" />', props: ['currentPage', 'pageSize', 'total', 'pageSizes', 'layout'] },
          'el-tag': { template: '<span class="el-tag"><slot /></span>', props: ['type', 'size', 'effect'] },
          'el-tooltip': { template: '<div class="el-tooltip"><slot /></div>', props: ['content', 'placement'] },
          'el-dialog': {
            template: '<div v-if="modelValue" class="el-dialog"><slot /></div>',
            props: ['modelValue', 'title', 'width'],
          },
          'el-descriptions': { template: '<div class="el-descriptions"><slot /></div>', props: ['column', 'border'] },
          'el-descriptions-item': { template: '<div class="el-descriptions-item"><slot /></div>', props: ['label'] },
          Download: { template: '<i class="icon-download" />' },
          Delete: { template: '<i class="icon-delete" />' },
          Check: { template: '<i class="icon-check" />' },
          Setting: { template: '<i class="icon-setting" />' },
          Document: { template: '<i class="icon-document" />' },
        },
      },
    })
    return wrapper
  }

  describe('组件挂载', () => {
    it('成功挂载并渲染根容器', () => {
      wrapper = mountComponent()
      expect(wrapper.find('.process-settings').exists()).toBe(true)
    })

    it('渲染日志管理卡片', () => {
      wrapper = mountComponent()
      const cards = wrapper.findAll('.content-card')
      expect(cards.length).toBeGreaterThan(0)
    })

    it('渲染导出日志按钮', () => {
      wrapper = mountComponent()
      const buttons = wrapper.findAll('.el-button')
      const texts = buttons.map(b => b.text())
      expect(texts.some(t => t.includes('导出日志'))).toBe(true)
    })
  })

  describe('代理 store', () => {
    it('store 来自 useSettingsStore', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.store).toBe(mockStore)
    })

    it('store.settings.logSettings 可访问', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.store.settings.logSettings.logLevel).toBe('INFO')
    })
  })

  describe('代理 useAuditLog', () => {
    it('auditLogs 来自 useAuditLog', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.auditLogs).toBe(mockAuditLogs.value)
    })

    it('auditLogStatistics 来自 useAuditLog', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.auditLogStatistics).toBe(mockAuditLogStatistics.value)
    })

    it('loadingLogs 来自 useAuditLog', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.loadingLogs).toBe(mockLoadingLogs.value)
    })

    it('loadAuditLogs 来自 useAuditLog', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.loadAuditLogs).toBe(mockLoadAuditLogs)
    })

    it('searchLogs 来自 useAuditLog', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.searchLogs).toBe(mockSearchLogs)
    })

    it('exportLogs 来自 useAuditLog', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.exportLogs).toBe(mockExportLogs)
    })

    it('clearLogs 来自 useAuditLog', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.clearLogs).toBe(mockClearLogs)
    })

    it('viewLogDetail 来自 useAuditLog', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.viewLogDetail).toBe(mockViewLogDetail)
    })

  })

  describe('代理 useSettings', () => {
    it('exportingLogs 来自 useSettings', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.exportingLogs).toBe(mockExportingLogs.value)
    })

    it('exportProgress 来自 useSettings', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.exportProgress).toBe(mockExportProgress.value)
    })

    it('exportResult 来自 useSettings', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.exportResult).toBe(mockExportResult.value)
    })

    it('exportSystemLogs 来自 useSettings', () => {
      wrapper = mountComponent()
      expect(wrapper.vm.exportSystemLogs).toBe(mockExportSystemLogs)
    })


  describe('日志导出状态渲染', () => {
    it('导出中显示进度百分比', async () => {
      mockExportingLogs.value = true
      mockExportProgress.value = 65
      wrapper = mountComponent()
      const buttons = wrapper.findAll('.el-button')
      const texts = buttons.map(b => b.text())
      expect(texts.some(t => t.includes('65'))).toBe(true)
    })

    it('非导出状态显示导出日志文本', () => {
      mockExportingLogs.value = false
      wrapper = mountComponent()
      const buttons = wrapper.findAll('.el-button')
      const texts = buttons.map(b => b.text())
      expect(texts.some(t => t.includes('导出日志'))).toBe(true)
    })
  })
  })

  describe('审计统计渲染', () => {
    it('auditLogStatistics 为 null 时不渲染统计区', () => {
      mockAuditLogStatistics.value = null
      wrapper = mountComponent()
      expect(wrapper.find('.audit-stats').exists()).toBe(false)
    })

    it('auditLogStatistics 有值时渲染统计区', async () => {
      mockAuditLogStatistics.value = {
        total_entries: 100,
        avg_confidence: 0.85,
        recent_24h: 20,
      } as any
      wrapper = mountComponent()
      await wrapper.vm.$nextTick()
      expect(wrapper.find('.audit-stats').exists()).toBe(true)
    })
  })
})
