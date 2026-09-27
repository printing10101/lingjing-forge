<script setup lang="ts">
// 原 /update-center 页面内容，现挂载于模板中心"更新中心"Tab
import { ref, onMounted, computed } from "vue";
import { useI18n } from "vue-i18n";
import { formatSecondsTimestamp } from "@/utils/formatters";
import http from "@/utils/http";
import { API_CONFIG, buildApiPath } from "@/config/api";
import { extractErrorMessage } from "@/utils/error-handler";
import { useProjectStore } from "@/stores/project";
import type { TagType } from "@/utils/statusHelpers";

const { t } = useI18n();
const notifications = ref<NotificationItem[]>([]);
const loading = ref(false);
const statusFilter = ref("pending");
const previewDialog = ref(false);
const previewData = ref<NotificationPreview | null>(null);
const projectStore = useProjectStore();

interface NotificationItem {
  notification_id: string;
  title: string;
  description: string;
  priority: string;
  status: string;
  expected_impact?: Record<string, number | string>;
  created_at: number;
}

interface NotificationPreview {
  title: string;
  description: string;
  change_preview: unknown;
  expected_impact: unknown;
}

const filteredNotifications = computed(() => {
  if (statusFilter.value === "all") return notifications.value;
  return notifications.value.filter((n) => n.status === statusFilter.value);
});

function priorityTag(priority: string): { text: string; type: TagType } {
  const map: Record<string, { text: string; type: TagType }> = {
    optional: { text: t("updateCenter.priorityOptional"), type: "info" },
    recommended: {
      text: t("updateCenter.priorityRecommended"),
      type: "primary",
    },
    critical: { text: t("updateCenter.priorityCritical"), type: "danger" },
  };
  return map[priority] || { text: priority, type: "info" };
}

function statusTag(status: string): { text: string; type: TagType } {
  const map: Record<string, { text: string; type: TagType }> = {
    pending: { text: t("updateCenter.statusPending"), type: "warning" },
    applied: { text: t("updateCenter.statusApplied"), type: "success" },
    dismissed: { text: t("updateCenter.statusDismissed"), type: "info" },
  };
  return map[status] || { text: status, type: "info" };
}

async function fetchNotifications() {
  loading.value = true;
  try {
    const res = await http.get(
      buildApiPath(
        API_CONFIG.V1,
        `/templates/updates/${projectStore.projectId}`,
      ),
      {
        params: {
          status: statusFilter.value === "all" ? undefined : statusFilter.value,
        },
      },
    );
    if (res.data.code === "SUCCESS") notifications.value = res.data.data;
  } catch (error) {
    console.warn(
      t("updateCenter.errorFetchFailed"),
      extractErrorMessage(error),
    );
  } finally {
    loading.value = false;
  }
}

async function applyUpdate(id: string) {
  try {
    await http.post(
      buildApiPath(API_CONFIG.V1, `/templates/updates/apply/${id}`),
    );
    fetchNotifications();
  } catch (error) {
    console.warn(
      t("updateCenter.errorApplyFailed"),
      extractErrorMessage(error),
    );
  }
}

async function dismissUpdate(id: string) {
  try {
    await http.post(
      buildApiPath(API_CONFIG.V1, `/templates/updates/dismiss/${id}`),
    );
    fetchNotifications();
  } catch (error) {
    console.warn(
      t("updateCenter.errorDismissFailed"),
      extractErrorMessage(error),
    );
  }
}

async function showPreview(id: string) {
  try {
    const res = await http.get(
      buildApiPath(API_CONFIG.V1, `/templates/updates/preview/${id}`),
    );
    if (res.data.code === "SUCCESS") {
      previewData.value = res.data.data;
      previewDialog.value = true;
    }
  } catch (error) {
    console.warn(
      t("updateCenter.errorPreviewFailed"),
      extractErrorMessage(error),
    );
  }
}

onMounted(fetchNotifications);
</script>

<template>
  <div class="update-panel">
    <div class="panel-toolbar">
      <el-radio-group v-model="statusFilter" @change="fetchNotifications">
        <el-radio-button value="pending">
          {{ t("updateCenter.filterPending") }}
        </el-radio-button>
        <el-radio-button value="applied">
          {{ t("updateCenter.filterApplied") }}
        </el-radio-button>
        <el-radio-button value="dismissed">
          {{ t("updateCenter.filterDismissed") }}
        </el-radio-button>
        <el-radio-button value="all">
          {{ t("updateCenter.filterAll") }}
        </el-radio-button>
      </el-radio-group>
      <el-button
        type="primary"
        style="margin-left: 12px"
        @click="fetchNotifications"
      >
        {{ t("updateCenter.btnRefresh") }}
      </el-button>
    </div>

    <div v-if="loading" class="loading">
      {{ t("updateCenter.loading") }}
    </div>

    <el-card
      v-for="notif in filteredNotifications"
      :key="notif.notification_id"
      class="notif-card"
      shadow="hover"
    >
      <template #header>
        <div class="notif-header">
          <span class="notif-title">{{ notif.title }}</span>
          <div class="notif-tags">
            <el-tag :type="priorityTag(notif.priority).type" size="small">
              {{ priorityTag(notif.priority).text }}
            </el-tag>
            <el-tag :type="statusTag(notif.status).type" size="small">
              {{ statusTag(notif.status).text }}
            </el-tag>
          </div>
        </div>
      </template>
      <div class="notif-body">
        <p>{{ notif.description }}</p>
        <div
          v-if="
            notif.expected_impact &&
            Object.keys(notif.expected_impact).length > 0
          "
          class="impact-section"
        >
          <h4>{{ t("updateCenter.expectedImpact") }}</h4>
          <div
            v-for="(val, key) in notif.expected_impact"
            :key="key"
            class="impact-item"
          >
            <span class="impact-label">{{ key }}:</span>
            <span class="impact-value">{{
              typeof val === "number" ? (val * 100).toFixed(1) + "%" : val
            }}</span>
          </div>
        </div>
        <div class="notif-time">
          {{ formatSecondsTimestamp(notif.created_at) }}
        </div>
      </div>
      <template #footer>
        <div class="notif-actions">
          <el-button size="small" @click="showPreview(notif.notification_id)">
            {{ t("updateCenter.btnPreview") }}
          </el-button>
          <el-button
            v-if="notif.status === 'pending'"
            type="success"
            size="small"
            @click="applyUpdate(notif.notification_id)"
          >
            {{ t("updateCenter.btnApply") }}
          </el-button>
          <el-button
            v-if="notif.status === 'pending'"
            type="info"
            size="small"
            @click="dismissUpdate(notif.notification_id)"
          >
            {{ t("updateCenter.btnDismiss") }}
          </el-button>
        </div>
      </template>
    </el-card>

    <el-empty
      v-if="!loading && filteredNotifications.length === 0"
      :description="t('updateCenter.emptyNoNotifications')"
    />

    <el-dialog
      v-model="previewDialog"
      :title="t('updateCenter.dialogTitle')"
      width="600px"
    >
      <div v-if="previewData">
        <h3>{{ previewData.title }}</h3>
        <p>{{ previewData.description }}</p>
        <h4>{{ t("updateCenter.sectionChanges") }}</h4>
        <pre class="change-preview">{{
          JSON.stringify(previewData.change_preview, null, 2)
        }}</pre>
        <h4>{{ t("updateCenter.sectionExpectedEffect") }}</h4>
        <pre class="impact-preview">{{
          JSON.stringify(previewData.expected_impact, null, 2)
        }}</pre>
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.panel-toolbar {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}
.notif-card {
  margin-bottom: 12px;
}
.notif-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.notif-title {
  font-weight: 600;
  font-size: 15px;
}
.notif-tags {
  display: flex;
  gap: 8px;
}
.notif-body {
  font-size: 14px;
  color: var(--text-secondary);
}
.impact-section {
  margin-top: 12px;
  padding: 8px 12px;
  background: var(--bg-secondary);
  border-radius: var(--radius-sm);
}
.impact-section h4 {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--text-secondary);
}
.impact-item {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  margin-bottom: 4px;
}
.impact-label {
  color: var(--text-tertiary);
}
.impact-value {
  font-weight: 600;
  color: var(--accent-primary);
}
.notif-time {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-tertiary);
}
.notif-actions {
  display: flex;
  gap: 8px;
}
.loading {
  text-align: center;
  padding: 40px;
  color: var(--text-tertiary);
}
.change-preview,
.impact-preview {
  background: var(--bg-secondary);
  padding: 12px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  max-height: 200px;
  overflow: auto;
}
</style>
