<template>
  <div class="workspace-panel-host">
    <!-- 空状态 -->
    <div
      v-if="panels.length === 0"
      class="workspace-panel-host__empty"
    >
      <el-icon
        :size="48"
        class="workspace-panel-host__empty-icon"
      >
        <Grid />
      </el-icon>
      <p class="workspace-panel-host__empty-text">
        {{ emptyText }}
      </p>
    </div>

    <!-- Tabs 布局：每个面板一个 Tab -->
    <el-tabs
      v-else-if="layout === 'tabs'"
      v-model="activeKey"
      class="workspace-panel-host__tabs"
      type="border-card"
      @tab-change="onTabChange"
    >
      <el-tab-pane
        v-for="panel in panels"
        :key="panelKey(panel)"
        :name="panelKey(panel)"
        :lazy="true"
      >
        <template #label>
          <span class="workspace-panel-host__tab-label">
            <el-icon
              v-if="panelIcon(panel)"
              class="workspace-panel-host__tab-icon"
            >
              <component :is="panelIcon(panel)" />
            </el-icon>
            <span>{{ panelTitle(panel) }}</span>
          </span>
        </template>
        <WorkspacePanelItem
          :panel="panel"
          :load-component="loadComponent"
        />
      </el-tab-pane>
    </el-tabs>

    <!-- Grid 布局：响应式网格 -->
    <div
      v-else-if="layout === 'grid'"
      class="workspace-panel-host__grid"
    >
      <div
        v-for="panel in panels"
        :key="panelKey(panel)"
        class="workspace-panel-host__grid-item"
      >
        <div class="workspace-panel-host__grid-header">
          <span class="workspace-panel-host__grid-title">{{ panelTitle(panel) }}</span>
        </div>
        <WorkspacePanelItem
          :panel="panel"
          :load-component="loadComponent"
        />
      </div>
    </div>

    <!-- Stack 布局：垂直堆叠 -->
    <div
      v-else
      class="workspace-panel-host__stack"
    >
      <div
        v-for="panel in panels"
        :key="panelKey(panel)"
        class="workspace-panel-host__stack-item"
      >
        <div class="workspace-panel-host__stack-header">
          <span class="workspace-panel-host__stack-title">{{ panelTitle(panel) }}</span>
        </div>
        <WorkspacePanelItem
          :panel="panel"
          :load-component="loadComponent"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 工作区扩展点宿主组件
 *
 * 对应 ADR-005 阶段 3 p3-6：渲染插件向 `workspace.panel` 扩展点贡献的面板组件。
 *
 * 职责：
 *   1. 从 useExtensionRegistry 拉取 workspacePanels 贡献列表
 *   2. 通过 loadComponent() 异步加载每个面板组件
 *   3. 提供三种布局：tabs / grid / stack
 *   4. 单个面板加载/渲染失败不影响其他面板（错误隔离）
 *
 * 使用方式：
 *   <WorkspacePanelHost layout="tabs" />
 *   <WorkspacePanelHost layout="grid" :empty-text="'暂无扩展面板'" />
 */

import { ref, watch, defineComponent, shallowRef, h, type Component } from 'vue'
import { ElIcon } from 'element-plus'
import { Grid, Loading } from '@element-plus/icons-vue'
import { useExtensionRegistry } from '@/composables/useExtensionRegistry'
import type { FrontendContribution } from '@/composables/useExtensionRegistry'

/** 布局模式。 */
type LayoutMode = 'tabs' | 'grid' | 'stack'

const props = withDefaults(
  defineProps<{
    /** 布局模式：tabs（默认）/ grid / stack。 */
    layout?: LayoutMode
    /** 空状态提示文字。 */
    emptyText?: string
    /** 指定渲染的扩展点名，默认 'workspace.panel'。 */
    extensionPoint?: string
    /** 初始激活面板 key（仅 tabs 模式有效）。 */
    defaultActiveKey?: string
  }>(),
  {
    layout: 'tabs',
    emptyText: '暂无扩展面板，安装插件后将自动显示在此',
    extensionPoint: 'workspace.panel',
    defaultActiveKey: '',
  },
)

const emit = defineEmits<{
  /** 面板切换（仅 tabs 模式）。 */
  (e: 'change', panelKey: string, panel: FrontendContribution): void
  /** 面板加载失败。 */
  (e: 'error', panel: FrontendContribution, error: unknown): void
}>()

const { listComputed, loadComponent } = useExtensionRegistry()

/** 面板贡献列表（响应式）。 */
const panels = listComputed(props.extensionPoint)

/** 当前激活的 tab key（tabs 模式）。 */
const activeKey = ref<string>('')

/** 初始化 / 切换扩展点时，设置默认激活项。 */
watch(
  () => panels.value,
  (list) => {
    if (list.length === 0) {
      activeKey.value = ''
      return
    }
    // 若 defaultActiveKey 仍存在于列表中，保持
    const exists = list.some((p) => panelKey(p) === activeKey.value)
    if (!exists) {
      const presetExists =
        props.defaultActiveKey && list.some((p) => panelKey(p) === props.defaultActiveKey)
      activeKey.value = presetExists ? props.defaultActiveKey : panelKey(list[0])
    }
  },
  { immediate: true },
)

/** 生成面板唯一 key（plugin_id + extension_point，确保稳定）。 */
function panelKey(panel: FrontendContribution): string {
  return `${panel.plugin_id}::${panel.extension_point}`
}

/** 从 metadata 中提取面板标题。 */
function panelTitle(panel: FrontendContribution): string {
  const title = panel.metadata?.title
  if (typeof title === 'string' && title.trim()) return title
  return panel.plugin_id
}

/** 从 metadata 中提取面板图标（Element Plus 图标组件名）。 */
function panelIcon(panel: FrontendContribution): Component | string | null {
  const icon = panel.metadata?.icon
  if (typeof icon === 'string' && icon.trim()) return icon
  return null
}

/** Tab 切换回调。 */
function onTabChange(key: string | number): void {
  const keyStr = String(key)
  const target = panels.value.find((p) => panelKey(p) === keyStr)
  if (target) {
    emit('change', keyStr, target)
  }
}

// 内部使用的面板项组件（错误隔离：单个面板加载/渲染失败不影响其他面板）
const WorkspacePanelItem = defineComponent({
  name: 'WorkspacePanelItem',
  props: {
    panel: { type: Object as () => FrontendContribution, required: true },
    loadComponent: {
      type: Function as unknown as () => (c: FrontendContribution) => Promise<unknown>,
      required: true,
    },
  },
  setup(panelProps) {
    const comp = shallowRef<Component | null>(null)
    const loading = ref(true)
    const errorMsg = ref<string>('')

    async function load() {
      loading.value = true
      errorMsg.value = ''
      try {
        const c = await panelProps.loadComponent(panelProps.panel)
        comp.value = (c as Component) ?? null
        if (!comp.value) {
          errorMsg.value = '面板组件未提供'
        }
      } catch (e: unknown) {
        errorMsg.value = e instanceof Error ? e.message : String(e)
        console.warn('[WorkspacePanelItem] 加载失败:', panelProps.panel.plugin_id, e)
      } finally {
        loading.value = false
      }
    }

    watch(() => panelProps.panel, load, { immediate: true })

    return () => {
      if (loading.value) {
        return h('div', { class: 'workspace-panel-host__loading' }, [
          h(ElIcon, { class: 'workspace-panel-host__loading-icon', size: 24 }, () => h(Loading)),
          h('span', '加载中...'),
        ])
      }
      if (errorMsg.value) {
        return h('div', { class: 'workspace-panel-host__error' }, [
          h('span', { class: 'workspace-panel-host__error-text' }, `面板加载失败：${errorMsg.value}`),
        ])
      }
      if (comp.value) {
        return h(comp.value as Component, { ...(panelProps.panel.props ?? {}) })
      }
      return h('div', { class: 'workspace-panel-host__error' }, '面板组件不可用')
    }
  },
})
</script>

<style scoped>
.workspace-panel-host {
  width: 100%;
  height: 100%;
}

.workspace-panel-host__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
  color: var(--text-tertiary);
  text-align: center;
}

.workspace-panel-host__empty-icon {
  margin-bottom: 16px;
  opacity: 0.6;
}

.workspace-panel-host__empty-text {
  margin: 0;
  font-size: 14px;
  line-height: 1.6;
}

.workspace-panel-host__tabs {
  height: 100%;
}

.workspace-panel-host__tab-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.workspace-panel-host__tab-icon {
  font-size: 14px;
}

.workspace-panel-host__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
  padding: 8px;
}

.workspace-panel-host__grid-item,
.workspace-panel-host__stack-item {
  background: var(--bg-primary);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  overflow: hidden;
  min-height: 200px;
  display: flex;
  flex-direction: column;
}

.workspace-panel-host__grid-header,
.workspace-panel-host__stack-header {
  padding: 8px 12px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-light);
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
}

.workspace-panel-host__stack {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.workspace-panel-host__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  color: var(--text-tertiary);
  font-size: 13px;
}

.workspace-panel-host__loading-icon {
  animation: workspace-panel-host-rotate 1.5s linear infinite;
}

@keyframes workspace-panel-host-rotate {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.workspace-panel-host__error {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  color: var(--error);
  font-size: 13px;
}

.workspace-panel-host__error-text {
  text-align: center;
}
</style>
