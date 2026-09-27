<template>
  <div class="dialect-settings-panel">
    <h3 class="dialect-settings-panel__title">后处理器方言插件</h3>
    <p class="dialect-settings-panel__desc">
      方言插件通过声明（dialect.yaml）+ 模板（Jinja2）定义后处理器行为，
      在「市场与工具 → 后处理器方言」页面管理。
    </p>
    <el-descriptions :column="1" border size="small">
      <el-descriptions-item label="插件 ID">dialect-manager</el-descriptions-item>
      <el-descriptions-item label="版本">1.0.0</el-descriptions-item>
      <el-descriptions-item label="扩展点">
        workspace.panel / command_palette.command / settings.tab
      </el-descriptions-item>
    </el-descriptions>
  </div>
</template>

<script setup lang="ts">
/**
 * 方言管理设置页签（settings.tab 扩展点贡献）
 *
 * 展示方言插件的基础信息与扩展点接入情况。
 */
</script>

<style scoped>
.dialect-settings-panel__title {
  margin: 0 0 8px;
  font-size: 16px;
}

.dialect-settings-panel__desc {
  margin: 0 0 16px;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
}
</style>
