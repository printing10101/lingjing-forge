<template>
  <div ref="rootRef" class="header-search" data-testid="header-search">
    <el-icon :size="16">
      <Search />
    </el-icon>
    <input
      ref="inputRef"
      v-model="query"
      type="text"
      :placeholder="t('appLayout.searchPlaceholder')"
      class="search-input"
      data-testid="header-search-input"
      @focus="openPanel"
      @keydown="handleKeydown"
    />
    <div
      v-if="panelOpen"
      class="search-panel"
      data-testid="header-search-panel"
    >
      <template v-if="resultGroups.length > 0">
        <div
          v-for="group in resultGroups"
          :key="group.key"
          class="search-group"
        >
          <span class="search-group-label">{{ group.title }}</span>
          <button
            v-for="item in group.items"
            :key="item.id"
            type="button"
            class="search-item"
            :class="{ active: item.flatIndex === activeIndex }"
            :data-testid="`search-item-${item.id}`"
            @click="execute(item)"
            @mousemove="activeIndex = item.flatIndex"
          >
            <el-icon :size="14">
              <component :is="item.icon" />
            </el-icon>
            <span class="search-item-label">{{ item.label }}</span>
            <span v-if="item.group" class="search-item-group">{{
              item.group
            }}</span>
          </button>
        </div>
      </template>
      <div v-else class="search-empty" data-testid="header-search-empty">
        {{ t("appLayout.searchNoResults") }}
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
// 全局快速导航（兑现引导文案「快速定位功能」的承诺）：
// 页面项来自 navGroups 单一数据源，快捷操作转发 file-command 给工程文件对话框。
// 纯 HTML 面板而非 el-popover：测试环境 el-* 依赖全局 stubs，自绘行为可测。
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import type { Component } from "vue";
import { navGroups } from "@/config/navGroups";
import {
  Search,
  DocumentAdd,
  FolderOpened,
  Document,
  CopyDocument,
  Download,
  Upload,
  DocumentCopy,
} from "@element-plus/icons-vue";

const emit = defineEmits<{
  (e: "file-command", cmd: string): void;
}>();

const { t } = useI18n();
const router = useRouter();

const query = ref("");
const panelOpen = ref(false);
const activeIndex = ref(0);
const inputRef = ref<HTMLInputElement | null>(null);
const rootRef = ref<HTMLElement | null>(null);

interface SearchEntry {
  id: string;
  kind: "page" | "command";
  label: string;
  /** 归属分组名（仅页面项），展示在行尾帮助定位 */
  group?: string;
  keywords: string;
  icon?: Component;
  path?: string;
  command?: string;
  flatIndex: number;
}

// 快捷操作复用工程文件菜单的 command 约定（AppFileDialogs 处理）
const commandDefs = [
  {
    id: "cmd-new",
    command: "new",
    labelKey: "appLayout.newProject",
    icon: DocumentAdd,
  },
  {
    id: "cmd-open",
    command: "open",
    labelKey: "appLayout.openProject",
    icon: FolderOpened,
  },
  {
    id: "cmd-save",
    command: "save",
    labelKey: "appLayout.save",
    icon: Document,
  },
  {
    id: "cmd-save-as",
    command: "save-as",
    labelKey: "appLayout.saveAs",
    icon: CopyDocument,
  },
  {
    id: "cmd-download",
    command: "download",
    labelKey: "appLayout.downloadProject",
    icon: Download,
  },
  {
    id: "cmd-import-step",
    command: "import-step",
    labelKey: "appLayout.importStep",
    icon: Upload,
  },
  {
    id: "cmd-import-dxf",
    command: "import-dxf",
    labelKey: "appLayout.importDxf",
    icon: DocumentCopy,
  },
] as const;

// computed 内取 t()，语言切换时结果与文案同步刷新
const allEntries = computed<Omit<SearchEntry, "flatIndex">[]>(() => {
  const pages = navGroups.flatMap((group) =>
    group.items.map((item) => ({
      id: `page-${item.path}`,
      kind: "page" as const,
      label: t(item.labelKey),
      group: t(group.labelKey),
      keywords: item.path,
      icon: item.icon,
      path: item.path,
    })),
  );
  const commands = commandDefs.map((def) => ({
    id: def.id,
    kind: "command" as const,
    label: t(def.labelKey),
    keywords: def.command,
    icon: def.icon,
    command: def.command,
  }));
  return [...pages, ...commands];
});

const filtered = computed<SearchEntry[]>(() => {
  const q = query.value.trim().toLowerCase();
  const matches = q
    ? allEntries.value.filter((entry) =>
        `${entry.label} ${entry.keywords}`.toLowerCase().includes(q),
      )
    : allEntries.value;
  return matches.map((entry, index) => ({ ...entry, flatIndex: index }));
});

const resultGroups = computed(() => {
  const pages = filtered.value.filter((entry) => entry.kind === "page");
  const commands = filtered.value.filter((entry) => entry.kind === "command");
  const groups: Array<{ key: string; title: string; items: SearchEntry[] }> =
    [];
  if (pages.length > 0) {
    groups.push({
      key: "pages",
      title: t("appLayout.searchGroupPages"),
      items: pages,
    });
  }
  if (commands.length > 0) {
    groups.push({
      key: "actions",
      title: t("appLayout.searchGroupActions"),
      items: commands,
    });
  }
  return groups;
});

// 键盘选中项滚出可视区时跟随滚动（列表超高一屏时 ↑↓ 仍可见）
watch(activeIndex, async () => {
  await nextTick();
  rootRef.value
    ?.querySelector(".search-item.active")
    ?.scrollIntoView({ block: "nearest" });
});

function openPanel() {
  activeIndex.value = 0;
  panelOpen.value = true;
}

function closePanel() {
  panelOpen.value = false;
}

function execute(entry: SearchEntry) {
  closePanel();
  query.value = "";
  if (entry.kind === "page" && entry.path) {
    router.push(entry.path);
  } else if (entry.command) {
    emit("file-command", entry.command);
  }
  inputRef.value?.blur();
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    openPanel();
    activeIndex.value = Math.min(
      activeIndex.value + 1,
      filtered.value.length - 1,
    );
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    activeIndex.value = Math.max(activeIndex.value - 1, 0);
  } else if (event.key === "Enter") {
    const target = filtered.value[activeIndex.value];
    if (target) execute(target);
  } else if (event.key === "Escape") {
    closePanel();
    inputRef.value?.blur();
  }
}

function onDocumentClick(event: MouseEvent) {
  if (rootRef.value && !rootRef.value.contains(event.target as Node)) {
    closePanel();
  }
}

// Ctrl/Cmd+K 全局聚焦搜索：桌面端高频导航入口
function onGlobalKeydown(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    inputRef.value?.focus();
    openPanel();
  }
}

onMounted(() => {
  document.addEventListener("click", onDocumentClick);
  window.addEventListener("keydown", onGlobalKeydown);
});

onBeforeUnmount(() => {
  document.removeEventListener("click", onDocumentClick);
  window.removeEventListener("keydown", onGlobalKeydown);
});
</script>

<style scoped>
.header-search {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  background-color: var(--bg-200);
  border-radius: var(--radius-md);
  border: 1px solid transparent;
  transition:
    border-color var(--transition-fast),
    background-color var(--transition-fast),
    box-shadow var(--transition-fast);
  width: 280px;
  color: var(--text-400);
}

.header-search:focus-within {
  border-color: var(--accent-primary);
  background-color: var(--bg-0);
  box-shadow: 0 0 0 3px rgba(0, 122, 255, 0.12);
}

.search-input {
  border: none;
  background: transparent;
  outline: none;
  font-size: 0.85rem;
  color: var(--text-primary);
  width: 100%;
  font-family: var(--font-sans);
}

.search-input::placeholder {
  color: var(--text-400);
}

.search-panel {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  width: 320px;
  max-height: 420px;
  overflow-y: auto;
  background-color: var(--bg-0);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg, var(--shadow));
  padding: 6px;
  z-index: 200;
}

.search-group {
  margin-bottom: 4px;
}

.search-group:last-child {
  margin-bottom: 0;
}

.search-group-label {
  display: block;
  font-size: 0.65rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-400);
  padding: 6px 10px 4px;
}

.search-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 10px;
  border: none;
  background: transparent;
  border-radius: var(--radius-sm, 6px);
  color: var(--text-primary);
  font-size: 0.8rem;
  text-align: left;
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.search-item:hover,
.search-item.active {
  background-color: var(--bg-200);
}

.search-item-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.search-item-group {
  flex-shrink: 0;
  font-size: 0.65rem;
  color: var(--text-400);
}

.search-empty {
  padding: 16px;
  text-align: center;
  font-size: 0.8rem;
  color: var(--text-400);
}
</style>
