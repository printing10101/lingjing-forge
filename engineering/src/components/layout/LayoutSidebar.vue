<template>
  <aside class="layout-sidebar">
    <div class="sidebar-brand">
      <div class="brand-logo">
        <svg viewBox="0 0 32 32" width="28" height="28" fill="none">
          <rect width="32" height="32" rx="8" fill="var(--accent-primary)" />
          <path d="M8 16 L16 8 L24 16 L16 24Z" fill="white" opacity="0.9" />
        </svg>
      </div>
      <span class="brand-name">{{ t("appLayout.brandName") }}</span>
    </div>

    <nav class="sidebar-nav">
      <div v-for="group in navGroups" :key="group.labelKey" class="nav-group">
        <span class="nav-group-label">{{ t(group.labelKey) }}</span>
        <router-link
          v-for="item in group.items"
          :key="item.path"
          :to="item.path"
          :class="['nav-item', { active: isActive(item.path) }]"
        >
          <el-icon :size="18">
            <component :is="item.icon" />
          </el-icon>
          <span class="nav-item-text">{{ t(item.labelKey) }}</span>
        </router-link>
      </div>
    </nav>
  </aside>
</template>

<script setup lang="ts">
import { useRoute } from "vue-router";
import { useI18n } from "vue-i18n";
import { navGroups } from "@/config/navGroups";

const { t } = useI18n();
const route = useRoute();

function isActive(path: string): boolean {
  if (path === "/") return route.path === "/";
  return route.path.startsWith(path);
}
</script>

<style scoped>
.layout-sidebar {
  width: var(--sidebar-width);
  height: 100vh;
  position: fixed;
  left: 0;
  top: 0;
  background-color: var(--bg-100);
  border-right: 1px solid var(--border-light);
  display: flex;
  flex-direction: column;
  z-index: 100;
  overflow: hidden;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-light);
  flex-shrink: 0;
}

.brand-name {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.01em;
}

.sidebar-nav {
  flex: 1;
  overflow-y: auto;
  padding: 12px 12px;
}

.nav-group {
  margin-bottom: 20px;
}

.nav-group:last-child {
  margin-bottom: 0;
}

.nav-group-label {
  display: block;
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-400);
  padding: 0 8px;
  margin-bottom: 6px;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border-radius: var(--radius-md);
  color: var(--text-secondary);
  text-decoration: none;
  font-size: 0.875rem;
  font-weight: 500;
  transition: all var(--transition-fast);
  cursor: pointer;
  margin-bottom: 2px;
}

.nav-item:hover {
  background-color: var(--bg-200);
  color: var(--text-primary);
}

.nav-item.active {
  background-color: var(--accent-primary);
  color: white;
}

.nav-item.active:hover {
  background-color: var(--accent-hover);
  color: white;
}

.nav-item-text {
  white-space: nowrap;
}
</style>
