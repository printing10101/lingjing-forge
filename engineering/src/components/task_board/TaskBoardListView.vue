<template>
  <div class="content-card">
    <div class="content-card__body">
      <el-table :data="pagedTasks" stripe style="width: 100%">
        <el-table-column
          prop="job_id"
          :label="t('taskBoard.colJobId')"
          min-width="180"
          show-overflow-tooltip
        />
        <el-table-column
          prop="task_type"
          :label="t('taskBoard.colType')"
          width="130"
        />
        <el-table-column :label="t('taskBoard.colStatus')" width="110">
          <template #default="{ row }">
            <el-tag
              :type="statusTagType(row.status)"
              size="small"
              effect="light"
            >
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column :label="t('taskBoard.colProgress')" width="140">
          <template #default="{ row }">
            <el-progress
              :percentage="Math.round(row.progress)"
              :stroke-width="8"
              :show-text="true"
            />
          </template>
        </el-table-column>
        <el-table-column
          prop="owner_id"
          :label="t('taskBoard.colAssignee')"
          width="100"
          show-overflow-tooltip
        />
        <el-table-column :label="t('taskBoard.colCreatedAt')" width="170">
          <template #default="{ row }">
            {{ formatDate(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column
          :label="t('taskBoard.colActions')"
          width="150"
          fixed="right"
        >
          <template #default="{ row }">
            <el-button
              v-if="
                row.status === 'running' ||
                row.status === 'queued' ||
                row.status === 'pending'
              "
              text
              type="danger"
              size="small"
              @click.stop="$emit('cancel', row.job_id)"
            >
              {{ t("taskBoard.btnCancel") }}
            </el-button>
            <el-button
              text
              type="success"
              size="small"
              :disabled="row.status !== 'completed' && row.status !== 'failed'"
              @click.stop="$emit('rerun', row as TaskInfo)"
            >
              {{ t("taskBoard.btnRerun") }}
            </el-button>
            <el-button
              text
              type="primary"
              size="small"
              @click.stop="$emit('openDetail', row as TaskInfo)"
            >
              {{ t("taskBoard.btnDetail") }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        v-if="tasks.length > pageSize"
        :current-page="currentPage"
        :page-size="pageSize"
        :total="tasks.length"
        layout="prev, pager, next"
        class="list-pagination"
        @current-change="handlePageChange"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useI18n } from "vue-i18n";
import type { TaskInfo } from "@/stores/tasks";
import type { TagType } from "@/utils/statusHelpers";

const { t } = useI18n();

const props = defineProps<{
  tasks: TaskInfo[];
}>();

defineEmits<{
  openDetail: [task: TaskInfo];
  cancel: [jobId: string];
  rerun: [task: TaskInfo];
}>();

/* 客户端分页：数据源与看板同一路径（store 全量拉取），筛选变化时回到第一页 */
const pageSize = 20;
const currentPage = ref(1);

const pagedTasks = computed(() => {
  const start = (currentPage.value - 1) * pageSize;
  return props.tasks.slice(start, start + pageSize);
});

watch(
  () => props.tasks.length,
  () => {
    currentPage.value = 1;
  },
);

function handlePageChange(page: number) {
  currentPage.value = page;
}

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
</script>

<style scoped>
.content-card {
  background: var(--bg-card);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}

.content-card__body {
  padding: 0;
}

.list-pagination {
  margin: 16px 0;
  justify-content: center;
}
</style>
