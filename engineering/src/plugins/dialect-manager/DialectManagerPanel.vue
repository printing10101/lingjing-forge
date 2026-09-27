<template>
  <DialectManager />
</template>

<script setup lang="ts">
/**
 * 方言管理面板（前端插件：dialect-manager）
 *
 * 作为 `workspace.panel` 扩展点的贡献组件，由 WorkspacePanelHost 渲染。
 * 复用完整的方言管理页（列表 + 详情 + NC 预览 + 新建向导 + 模板/参数编辑）。
 */
import DialectManager from '@/views/DialectManager.vue'
</script>
