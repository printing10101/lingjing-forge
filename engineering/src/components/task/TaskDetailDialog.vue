<template>
  <transition name="slide-panel">
    <div v-if="visible" class="detail-overlay" @click.self="handleClose">
      <div class="detail-panel">
        <div class="detail-header">
          <h3 class="detail-title">
            {{ t("taskBoard.detailTitle") }}
          </h3>
          <el-button :icon="Close" text @click="handleClose" />
        </div>

        <template v-if="task">
          <div class="detail-body">
            <div class="detail-field">
              <label class="field-label">{{
                t("taskBoard.detailJobId")
              }}</label>
              <div class="field-value mono">
                {{ task.job_id }}
              </div>
            </div>

            <div class="detail-field">
              <label class="field-label">{{
                t("taskBoard.detailTaskType")
              }}</label>
              <div class="field-value">
                {{ task.task_type }}
              </div>
            </div>

            <div class="detail-field-row">
              <div class="detail-field">
                <label class="field-label">{{
                  t("taskBoard.detailStatus")
                }}</label>
                <el-tag :type="statusTagType(task.status)" effect="light">
                  {{ statusLabel(task.status) }}
                </el-tag>
              </div>
              <div class="detail-field">
                <label class="field-label">{{
                  t("taskBoard.detailProgress")
                }}</label>
                <el-progress
                  :percentage="Math.round(task.progress)"
                  :stroke-width="10"
                  :show-text="true"
                  style="width: 100%"
                />
              </div>
            </div>

            <div class="detail-field">
              <label class="field-label">{{
                t("taskBoard.detailAssignee")
              }}</label>
              <div class="field-value">
                <div
                  class="avatar"
                  :style="{ backgroundColor: avatarColor(task.owner_id || '') }"
                >
                  {{ (task.owner_id || "?").charAt(0).toUpperCase() }}
                </div>
                <span style="margin-left: 8px">{{ task.owner_id || "-" }}</span>
              </div>
            </div>

            <div class="detail-field">
              <label class="field-label">{{
                t("taskBoard.detailCreatedAt")
              }}</label>
              <div class="field-value">
                {{ formatDate(task.created_at) }}
              </div>
            </div>

            <div v-if="task.duration_seconds != null" class="detail-field">
              <label class="field-label">{{
                t("taskBoard.detailDuration")
              }}</label>
              <div class="field-value">
                {{ formatDuration(task.duration_seconds) }}
              </div>
            </div>

            <div class="detail-field">
              <label class="field-label">{{
                t("taskBoard.detailParams")
              }}</label>
              <div class="field-value code-block">
                <pre>{{ JSON.stringify(task.params || {}, null, 2) }}</pre>
              </div>
            </div>

            <div v-if="task.result" class="detail-field">
              <label class="field-label">{{
                t("taskBoard.detailResult")
              }}</label>
              <div class="field-value code-block">
                <pre>{{ JSON.stringify(task.result, null, 2) }}</pre>
              </div>
            </div>

            <div
              v-if="task.metrics && Object.keys(task.metrics).length > 0"
              class="detail-field"
            >
              <label class="field-label">{{
                t("taskBoard.detailMetrics")
              }}</label>
              <div class="field-value code-block">
                <pre>{{ JSON.stringify(task.metrics, null, 2) }}</pre>
              </div>
            </div>

            <div v-if="task.error" class="detail-field">
              <label class="field-label">{{
                t("taskBoard.detailError")
              }}</label>
              <div class="field-value error-text">
                {{ task.error }}
              </div>
            </div>
          </div>

          <div class="detail-footer">
            <el-button
              v-if="
                task.status === 'running' ||
                task.status === 'queued' ||
                task.status === 'pending'
              "
              type="danger"
              text
              size="small"
              @click="handleCancel"
            >
              {{ t("taskBoard.btnCancelTask") }}
            </el-button>
            <el-button size="small" @click="handleClose">
              {{ t("taskBoard.btnClose") }}
            </el-button>
          </div>
        </template>
      </div>
    </div>
  </transition>
</template>

<script setup lang="ts">
import { Close } from "@element-plus/icons-vue";
import { useI18n } from "vue-i18n";
import type { TaskInfo } from "@/stores/tasks";
import type { TagType } from "@/utils/statusHelpers";

const { t } = useI18n();

const props = defineProps<{
  visible: boolean;
  task: TaskInfo | null;
}>();

const emit = defineEmits<{
  "update:visible": [value: boolean];
  close: [];
  cancel: [jobId: string];
}>();

function handleClose() {
  emit("update:visible", false);
  emit("close");
}

function handleCancel() {
  if (props.task) {
    emit("cancel", props.task.job_id);
  }
  handleClose();
}

/* ------------------------------------------------------------------ */
/*  Helpers — status / formatting                                     */
/* ------------------------------------------------------------------ */
function statusLabel(status: TaskInfo["status"]): string {
  const map: Record<TaskInfo["status"], string> = {
    pending: t("taskBoard.statusPending"),
    queued: t("taskBoard.statusQueued"),
    running: t("taskBoard.statusRunning"),
    completed: t("taskBoard.statusCompleted"),
    failed: t("taskBoard.statusFailed"),
    cancelled: t("taskBoard.statusCancelled"),
  };
  return map[status] || status;
}

function statusTagType(status: TaskInfo["status"]): TagType {
  const map: Record<TaskInfo["status"], TagType> = {
    pending: "info",
    queued: "warning",
    running: "primary",
    completed: "success",
    failed: "danger",
    cancelled: "info",
  };
  return map[status] || "info";
}

function formatDate(iso: string): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const h = String(d.getHours()).padStart(2, "0");
  const min = String(d.getMinutes()).padStart(2, "0");
  return `${y}-${m}-${day} ${h}:${min}`;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  if (m < 60) return `${m}m ${s}s`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return `${h}h ${rm}m`;
}

const avatarColorMap: Record<string, string> = {
  [t("taskBoard.userZhangSan")]: "var(--brand-500)",
  [t("taskBoard.userLiSi")]: "var(--success)",
  [t("taskBoard.userWangWu")]: "var(--warning)",
  [t("taskBoard.userZhaoLiu")]: "var(--purple)",
};

function avatarColor(name: string): string {
  return avatarColorMap[name] || "var(--text-400)";
}
</script>

<style scoped>
.detail-overlay {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  left: 0;
  z-index: 1000;
  background: var(--bg-overlay-light);
  display: flex;
  justify-content: flex-end;
}

.detail-panel {
  width: 400px;
  max-width: 100vw;
  height: 100vh;
  background: var(--bg-card);
  box-shadow: var(--shadow-xl);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.detail-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px 16px;
  border-bottom: 1px solid var(--border-light);
  flex-shrink: 0;
}

.detail-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.detail-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.detail-field-row {
  display: flex;
  gap: 16px;
}

.detail-field-row .detail-field {
  flex: 1;
}

.detail-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

.field-value {
  font-size: 14px;
  color: var(--text-primary);
  line-height: 1.5;
  display: flex;
  align-items: center;
}

.field-value.mono {
  font-family: var(--font-mono);
  font-size: 12px;
  word-break: break-all;
}

.field-value.code-block {
  background: var(--bg-secondary);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  overflow-x: auto;
}

.field-value.code-block pre {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}

.field-value.error-text {
  color: var(--error);
}

.detail-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 16px 24px 20px;
  border-top: 1px solid var(--border-light);
  flex-shrink: 0;
}

.avatar {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-white);
  flex-shrink: 0;
}

/* panel slide animation */
.slide-panel-enter-active,
.slide-panel-leave-active {
  transition: all 0.3s ease;
}
.slide-panel-enter-from .detail-panel,
.slide-panel-leave-to .detail-panel {
  transform: translateX(100%);
}
.slide-panel-enter-from,
.slide-panel-leave-to {
  opacity: 0;
}
</style>
