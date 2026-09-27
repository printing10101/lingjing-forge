// About 视图「检查更新」功能测试（自动更新过渡方案）
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const mocks = vi.hoisted(() => ({
  getSystemVersion: vi.fn(),
  checkForUpdates: vi.fn(),
}))

// Mock api/system（避免真实 HTTP 请求）
vi.mock('@/api/system', () => ({
  getSystemVersion: mocks.getSystemVersion,
  checkForUpdates: mocks.checkForUpdates,
}))

// Mock Tauri invoke（动态导入，避免非 Tauri 环境报错）
vi.mock('@tauri-apps/api/core', () => ({
  invoke: vi.fn().mockResolvedValue(undefined),
}))

import About from '@/views/About.vue'

const upToDateResult = {
  current_version: '2.7.0',
  latest_version: 'v2.7.0',
  update_available: false,
  latest_release_url: 'https://github.com/printing10101/Virtual-Realm-Manufacturing/releases/tag/v2.7.0',
  checked_at: '2026-08-14T00:00:00+00:00',
  error: null,
}

const updateAvailableResult = {
  ...upToDateResult,
  latest_version: 'v2.8.0',
  update_available: true,
  latest_release_url: 'https://github.com/printing10101/Virtual-Realm-Manufacturing/releases/tag/v2.8.0',
}

describe('About.vue 更新检查', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.getSystemVersion.mockResolvedValue({ version: '2.7.0', commit: 'abc123' })
    mocks.checkForUpdates.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  const mountAbout = () =>
    mount(About, {
      global: {
        mocks: {
          $t: (key: string, params?: Record<string, unknown>) =>
            params ? `${key}:${JSON.stringify(params)}` : key,
        },
      },
    })

  it('挂载时加载并显示当前版本', async () => {
    const wrapper = mountAbout()
    await flushPromises()
    expect(mocks.getSystemVersion).toHaveBeenCalled()
    expect(wrapper.find('.version-text').text()).toContain('2.7.0')
  })

  it('挂载时后端不可用则保持占位（不阻塞页面）', async () => {
    mocks.getSystemVersion.mockRejectedValue(new Error('backend down'))
    const wrapper = mountAbout()
    await flushPromises()
    expect(wrapper.exists()).toBe(true)
  })

  it('点击检查更新 - 有可用更新时显示提示与下载按钮', async () => {
    mocks.checkForUpdates.mockResolvedValue(updateAvailableResult)
    const wrapper = mountAbout()
    await flushPromises()
    await wrapper.find('[data-testid="check-update-btn"]').trigger('click')
    await flushPromises()
    expect(mocks.checkForUpdates).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="update-alert"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="download-btn"]').exists()).toBe(true)
  })

  it('点击检查更新 - 已是最新版本时不显示下载按钮', async () => {
    mocks.checkForUpdates.mockResolvedValue(upToDateResult)
    const wrapper = mountAbout()
    await flushPromises()
    await wrapper.find('[data-testid="check-update-btn"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="update-alert"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="download-btn"]').exists()).toBe(false)
  })

  it('网络失败（fail-soft）时显示错误提示', async () => {
    mocks.checkForUpdates.mockResolvedValue({
      ...upToDateResult,
      latest_version: null,
      latest_release_url: null,
      error: 'network',
    })
    const wrapper = mountAbout()
    await flushPromises()
    await wrapper.find('[data-testid="check-update-btn"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="update-alert-error"]').exists()).toBe(true)
  })

  it('检查接口抛异常时同样 fail-soft 为网络错误', async () => {
    mocks.checkForUpdates.mockRejectedValue(new Error('request failed'))
    const wrapper = mountAbout()
    await flushPromises()
    await wrapper.find('[data-testid="check-update-btn"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="update-alert-error"]').exists()).toBe(true)
  })
})
