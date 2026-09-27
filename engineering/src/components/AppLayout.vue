<template>
  <div class="app-layout">
    <LayoutSidebar />

    <!-- Main Area -->
    <div class="layout-main">
      <LayoutHeader
        :project-name="projectName"
        :is-modified="isModified"
        @file-command="(cmd: string) => emit('file-command', cmd)"
        @refresh="emit('refresh')"
      />

      <!-- Page Content：仅缓存白名单交互页，仪表盘/监控页每次进入重新挂载拉新数据 -->
      <main class="layout-content">
        <router-view v-slot="{ Component }">
          <keep-alive :include="keepAliveNames">
            <component :is="Component" />
          </keep-alive>
        </router-view>
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import LayoutSidebar from "@/components/layout/LayoutSidebar.vue";
import LayoutHeader from "@/components/layout/LayoutHeader.vue";
import { KEEP_ALIVE_PAGE_NAMES } from "@/config/keepAlivePages";

const keepAliveNames = KEEP_ALIVE_PAGE_NAMES;

const emit = defineEmits<{
  (e: "file-command", cmd: string): void;
  (e: "refresh"): void;
}>();

defineProps<{
  projectName?: string;
  isModified?: boolean;
}>();
</script>

<style scoped>
.app-layout {
  display: flex;
  min-height: 100vh;
  background-color: var(--bg-primary);
}

.layout-main {
  flex: 1;
  margin-left: var(--sidebar-width);
  display: flex;
  flex-direction: column;
  min-height: 100vh;
}

.layout-content {
  flex: 1;
  padding: var(--page-padding);
  max-width: var(--content-max-width);
  width: 100%;
}
</style>
