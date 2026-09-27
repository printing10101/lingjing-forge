/**
 * 方言管理前端插件（dialect-manager）
 *
 * 对应后端 postprocessor 方言插件（postprocessor-plugins/，市场条目 dialect:*）。
 * 本插件向 `workspace.panel` 扩展点注册方言管理面板，使工艺员在工作区直接
 * 访问方言管理（列表 / 详情 / NC 预览 / 新建向导 / 模板与参数编辑）。
 *
 * 注册方式：应用初始化时调用 registerDialectManagerPlugin()，或由
 * extensionRegistry.syncFromBackend() 触发的插件初始化流程调用。
 */

import { extensionRegistry } from '@/composables/useExtensionRegistry'

export const DIALECT_MANAGER_PLUGIN_ID = 'dialect-manager'

/** 方言管理面板组件 URL（相对 src/plugins/ 根）。 */
const PANEL_COMPONENT_URL = 'dialect-manager/DialectManagerPanel.vue'

/** 面板元信息（WorkspacePanelHost 读取 title/icon）。 */
const PANEL_METADATA = {
  title: '后处理器方言',
  icon: 'Cpu',
  description: '管理后处理器方言（声明/模板/参数/NC 预览）',
}

/**
 * 注册方言管理面板到 workspace.panel 扩展点。
 *
 * 幂等：重复调用会先卸载再注册，避免重复面板。
 */
export function registerDialectManagerPlugin(): void {
  // 先卸载旧贡献，保证幂等
  extensionRegistry.unregister(DIALECT_MANAGER_PLUGIN_ID)

  extensionRegistry.register({
    plugin_id: DIALECT_MANAGER_PLUGIN_ID,
    extension_point: 'workspace.panel',
    component_url: PANEL_COMPONENT_URL,
    metadata: PANEL_METADATA,
  })

  // 命令面板贡献：打开方言管理页（路由跳转）
  extensionRegistry.register({
    plugin_id: DIALECT_MANAGER_PLUGIN_ID,
    extension_point: 'command_palette.command',
    handler: async () => {
      const { default: router } = await import('@/router')
      await router.push('/dialect-manager')
    },
    metadata: {
      title: '打开方言管理',
      description: '跳转到后处理器方言管理页',
      category: '制造工具',
      icon: 'Cpu',
    },
  })

  // 设置页签贡献：方言插件信息
  extensionRegistry.register({
    plugin_id: DIALECT_MANAGER_PLUGIN_ID,
    extension_point: 'settings.tab',
    component_url: 'dialect-manager/DialectSettingsTab.vue',
    metadata: {
      title: '方言插件',
      icon: 'Cpu',
    },
  })
}

/**
 * 卸载方言管理插件（取消所有贡献）。
 */
export function unregisterDialectManagerPlugin(): void {
  extensionRegistry.unregister(DIALECT_MANAGER_PLUGIN_ID)
}

export default {
  id: DIALECT_MANAGER_PLUGIN_ID,
  name: '后处理器方言管理',
  version: '1.0.0',
  register: registerDialectManagerPlugin,
  unregister: unregisterDialectManagerPlugin,
}

