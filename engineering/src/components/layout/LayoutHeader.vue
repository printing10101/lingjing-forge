<template>
  <header class="layout-header">
    <!-- 全局快速搜索：页面导航 + 工程文件快捷操作（Ctrl/Cmd+K 聚焦） -->
    <HeaderSearch @file-command="handleFileCommand" />
    <div class="header-actions">
      <el-tooltip :content="t('appLayout.refresh')" placement="bottom">
        <button class="header-btn" @click="emit('refresh')">
          <el-icon :size="18">
            <Refresh />
          </el-icon>
        </button>
      </el-tooltip>
      <el-dropdown trigger="click" placement="bottom-end">
        <button class="header-btn notification-btn">
          <el-icon :size="18">
            <Bell />
          </el-icon>
          <!-- 仅存在未读时亮红点，避免"永远有通知"的误导 -->
          <span v-if="unreadCount > 0" class="notification-dot" />
        </button>
        <template #dropdown>
          <el-dropdown-menu class="notification-dropdown">
            <div class="notification-header">
              <span class="notification-title">{{
                t("appLayout.notifications")
              }}</span>
              <el-button
                v-if="unreadCount > 0"
                text
                size="small"
                @click="markAllRead"
              >
                {{ t("appLayout.markAllRead") }}
              </el-button>
            </div>
            <el-divider style="margin: 4px 0" />
            <div
              v-if="notifications.length === 0"
              class="notification-empty"
              data-testid="notification-empty"
            >
              {{ t("appLayout.noNotifications") }}
            </div>
            <div
              v-for="n in notifications"
              :key="n.id"
              class="notification-item"
              :class="{ unread: !n.read }"
              @click="markRead(n)"
            >
              <div class="notification-dot-indicator" :class="n.type" />
              <div class="notification-content">
                <span class="notification-text">{{ n.text }}</span>
                <span class="notification-time">{{ n.time }}</span>
              </div>
            </div>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <BackendStatusIndicator />
      <el-dropdown trigger="click" @command="handleFileCommand">
        <button class="header-btn file-btn">
          <el-icon :size="18">
            <Folder />
          </el-icon>
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="new">
              <el-icon :size="16">
                <DocumentAdd /> </el-icon
              >{{ t("appLayout.newProject") }}
            </el-dropdown-item>
            <el-dropdown-item command="open">
              <el-icon :size="16">
                <FolderOpened /> </el-icon
              >{{ t("appLayout.openProject") }}
            </el-dropdown-item>
            <el-dropdown-item divided command="save">
              <el-icon :size="16">
                <Document /> </el-icon
              >{{ t("appLayout.save") }}
            </el-dropdown-item>
            <el-dropdown-item command="save-as">
              <el-icon :size="16">
                <CopyDocument /> </el-icon
              >{{ t("appLayout.saveAs") }}
            </el-dropdown-item>
            <el-dropdown-item divided command="download">
              <el-icon :size="16">
                <Download /> </el-icon
              >{{ t("appLayout.downloadProject") }}
            </el-dropdown-item>
            <el-dropdown-item divided command="import-step">
              <el-icon :size="16">
                <Upload /> </el-icon
              >{{ t("appLayout.importStep") }}
            </el-dropdown-item>
            <el-dropdown-item command="import-dxf">
              <el-icon :size="16">
                <DocumentCopy /> </el-icon
              >{{ t("appLayout.importDxf") }}
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <!-- 帮助菜单：引导重放（W10.2）——修复「帮助 → 重新引导」断线承诺 -->
      <el-dropdown trigger="click" @command="handleHelpCommand">
        <button class="header-btn help-btn" data-testid="header-help-menu">
          <el-icon :size="18">
            <QuestionFilled />
          </el-icon>
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="replay-tour">
              <el-icon :size="16">
                <Guide /> </el-icon
              >{{ t("appLayout.replayTour") }}
            </el-dropdown-item>
            <el-dropdown-item command="replay-flow-tour">
              <el-icon :size="16">
                <MapLocation /> </el-icon
              >{{ t("appLayout.replayFlowTour") }}
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <span v-if="projectName" class="project-indicator">
        {{ projectName }}
        <el-tag v-if="isModified" size="small" type="warning" effect="plain">{{
          t("appLayout.unsaved")
        }}</el-tag>
      </span>
      <el-dropdown
        trigger="click"
        placement="bottom-end"
        @command="handleUserCommand"
      >
        <button class="header-btn user-btn" data-testid="header-user-menu">
          <el-avatar :size="24" class="user-avatar">
            {{ avatarText }}
          </el-avatar>
          <span class="user-name">{{
            authStore.user?.username || t("appLayout.guest")
          }}</span>
          <el-icon :size="12">
            <ArrowDown />
          </el-icon>
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item disabled class="user-info-item">
              <span class="user-role-tag">{{ roleLabel }}</span>
            </el-dropdown-item>
            <el-dropdown-item divided command="logout">
              <el-icon :size="16">
                <SwitchButton /> </el-icon
              >{{ t("appLayout.logout") }}
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { useI18n } from "vue-i18n";
import { useRouter } from "vue-router";
import { ElMessageBox } from "element-plus";
import {
  Refresh,
  Bell,
  Folder,
  DocumentAdd,
  FolderOpened,
  Document,
  CopyDocument,
  Download,
  Upload,
  DocumentCopy,
  SwitchButton,
  ArrowDown,
  QuestionFilled,
  Guide,
  MapLocation,
} from "@element-plus/icons-vue";
import BackendStatusIndicator from "@/components/BackendStatusIndicator.vue";
import HeaderSearch from "@/components/layout/HeaderSearch.vue";
import { useAuthStore } from "@/stores/auth";
import http from "@/utils/http";
import { API_CONFIG, buildApiPath } from "@/config/api";
import { extractErrorMessage } from "@/utils/error-handler";

const { t } = useI18n();
const router = useRouter();
const authStore = useAuthStore();

const emit = defineEmits<{
  (e: "file-command", cmd: string): void;
  (e: "refresh"): void;
}>();

defineProps<{
  projectName?: string;
  isModified?: boolean;
}>();

const avatarText = computed(() =>
  (authStore.user?.username || "?").slice(0, 1).toUpperCase(),
);

// 角色文案走 i18n；未知角色（后端新增枚举未跟上文案）回退显示原始标识
const roleLabel = computed(() => {
  const key = `appLayout.roles.${authStore.userRole}`;
  const translated = t(key);
  return translated === key ? authStore.userRole : translated;
});

async function handleUserCommand(cmd: string) {
  if (cmd !== "logout") return;
  try {
    await ElMessageBox.confirm(
      t("appLayout.logoutConfirm"),
      t("appLayout.logoutTitle"),
      {
        confirmButtonText: t("appLayout.logout"),
        cancelButtonText: t("common.cancel"),
        type: "warning",
      },
    );
    authStore.logout();
    router.replace("/login");
  } catch {
    // 用户取消退出
  }
}

const notifications = ref<
  Array<{ id: number; text: string; time: string; type: string; read: boolean }>
>([]);

const unreadCount = computed(
  () => notifications.value.filter((n) => !n.read).length,
);

function markRead(notification: { read: boolean }) {
  notification.read = true;
}

// 通知轮询间隔：长会话也能收到新通知（页面保持挂载，轮询随组件卸载停止）
const NOTIFICATION_POLL_INTERVAL_MS = 60_000;
let notificationPollTimer: number | null = null;
let notificationFetchInFlight = false;

async function fetchNotifications() {
  // 轮询触发时上一请求尚未返回则跳过本轮，避免请求堆积
  if (notificationFetchInFlight) return;
  notificationFetchInFlight = true;
  try {
    const resp = await http.get(buildApiPath(API_CONFIG.V1, "/notifications"));
    if (resp.data.code === 0 && resp.data.data) {
      notifications.value = resp.data.data.map(
        (
          item: {
            notification_id: string;
            title: string;
            created_at: number;
            priority: string;
          },
          index: number,
        ) => ({
          id: index + 1,
          text: item.title,
          time: formatTime(item.created_at),
          type: mapPriorityToType(item.priority),
          read: false,
        }),
      );
    }
  } catch (error) {
    console.warn("获取通知失败:", extractErrorMessage(error));
  } finally {
    notificationFetchInFlight = false;
  }
}

function formatTime(timestamp: number): string {
  const now = Date.now();
  const diff = now - timestamp * 1000;
  const minutes = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);

  if (minutes < 60) return t("home.timeMinutesAgo", { n: minutes });
  if (hours < 24) return t("home.timeHoursAgo", { n: hours });
  return t("home.timeDaysAgo", { n: days });
}

function mapPriorityToType(priority: string): string {
  const priorityMap: Record<string, string> = {
    critical: "error",
    high: "warning",
    medium: "info",
    low: "success",
  };
  return priorityMap[priority] || "info";
}

onMounted(() => {
  fetchNotifications();
  notificationPollTimer = window.setInterval(
    fetchNotifications,
    NOTIFICATION_POLL_INTERVAL_MS,
  );
});

onBeforeUnmount(() => {
  if (notificationPollTimer !== null) {
    window.clearInterval(notificationPollTimer);
    notificationPollTimer = null;
  }
});

function markAllRead() {
  notifications.value.forEach((n) => (n.read = true));
}

function handleFileCommand(cmd: string) {
  emit("file-command", cmd);
}

// 帮助菜单：通过 window 事件把重放请求转给 App.vue 的 Tour 实例（W10.2）
function handleHelpCommand(cmd: string) {
  if (cmd === "replay-tour" || cmd === "replay-flow-tour") {
    window.dispatchEvent(new Event(cmd));
  }
}
</script>

<style scoped>
.layout-header {
  height: var(--header-height);
  padding: 0 var(--page-padding);
  display: flex;
  align-items: center;
  justify-content: space-between;
  background-color: var(--bg-header);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border-light);
  position: sticky;
  top: 0;
  z-index: 50;
  flex-shrink: 0;
  gap: 16px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.header-btn {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: var(--text-500);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
  position: relative;
}

.header-btn:hover {
  background-color: var(--bg-200);
  color: var(--text-primary);
}

.notification-btn {
  position: relative;
}

.notification-dot {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 8px;
  height: 8px;
  background-color: var(--error);
  border-radius: 50%;
  border: 2px solid var(--bg-primary);
}

.file-btn {
  width: 36px;
  height: 36px;
}

.project-indicator {
  font-size: 0.8rem;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-left: 4px;
}

/* ===== User Menu ===== */
.user-btn {
  gap: 6px;
  width: auto;
  padding: 0 8px;
}

.user-avatar {
  background-color: var(--accent-primary);
  color: #fff;
  font-size: 0.75rem;
  font-weight: 600;
  flex-shrink: 0;
}

.user-name {
  font-size: 0.8rem;
  color: var(--text-primary);
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-info-item {
  cursor: default;
}

.user-role-tag {
  font-size: 0.75rem;
  color: var(--accent-primary);
  padding: 2px 8px;
  background-color: var(--accent-bg, rgba(0, 122, 255, 0.08));
  border-radius: var(--radius-sm);
}

/* ===== Notification Dropdown ===== */
.notification-dropdown {
  width: 320px;
}

.notification-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px 4px;
}

.notification-title {
  font-size: 0.9rem;
  font-weight: 600;
  color: var(--text-primary);
}

.notification-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 16px;
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.notification-item:hover {
  background-color: var(--bg-100);
}

.notification-item.unread {
  background-color: var(--info-bg);
}

.notification-dot-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-top: 5px;
  flex-shrink: 0;
}

.notification-dot-indicator.error {
  background-color: var(--error);
}
.notification-dot-indicator.success {
  background-color: var(--success);
}
.notification-dot-indicator.info {
  background-color: var(--info);
}
.notification-dot-indicator.warning {
  background-color: var(--warning);
}

.notification-content {
  flex: 1;
  min-width: 0;
}

.notification-text {
  display: block;
  font-size: 0.8rem;
  color: var(--text-primary);
  line-height: 1.4;
}

.notification-time {
  display: block;
  font-size: 0.7rem;
  color: var(--text-400);
  margin-top: 2px;
}

.notification-empty {
  padding: 20px 16px;
  text-align: center;
  font-size: 0.8rem;
  color: var(--text-400);
}
</style>
