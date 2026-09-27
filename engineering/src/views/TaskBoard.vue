<template>
  <div class="task-board-page">
    <!-- ===== Page Header ===== -->
    <TaskBoardHeader
      :view-mode="viewMode"
      @update:view-mode="viewMode = $event"
      @toggle-filter="filterVisible = !filterVisible"
      @create="handleCreate"
    />

    <!-- ===== Filter Panel (collapsible, inline below header) ===== -->
    <TaskFilters
      :priority="filters.priority"
      :assignee="filters.assignee"
      :date-range="filters.dateRange"
      :task-type="filters.taskType"
      :assignee-options="assigneeOptions"
      :task-type-options="taskTypeOptions"
      :filter-visible="filterVisible"
      @update:priority="filters.priority = $event"
      @update:assignee="filters.assignee = $event"
      @update:date-range="filters.dateRange = $event"
      @update:task-type="filters.taskType = $event"
    />

    <!-- ===== Loading State ===== -->
    <div
      v-if="tasksStore.loading && allTasks.length === 0"
      class="board-loading"
    >
      <el-skeleton :rows="6" animated />
    </div>

    <!-- ===== Error Banner (non-blocking) ===== -->
    <TaskBoardErrorBanner
      v-else-if="fetchFailed && allTasks.length === 0"
      @retry="retryFetch"
    />

    <!-- ===== Empty State ===== -->
    <div
      v-else-if="!tasksStore.loading && allTasks.length === 0"
      class="empty-state"
    >
      <el-empty :description="t('taskBoard.emptyNoTasks')" />
    </div>

    <!-- ===== Kanban View ===== -->
    <TaskBoardKanban
      v-else-if="viewMode === 'kanban'"
      :columns="filteredColumns"
      @open-detail="openDetail"
    />

    <!-- ===== List View ===== -->
    <TaskBoardListView
      v-else-if="viewMode === 'list'"
      :tasks="flatFilteredTasks"
      @open-detail="openDetail"
      @cancel="handleCancel"
      @rerun="handleRerun"
    />

    <!-- ===== Task Detail Side Panel ===== -->
    <TaskDetailDialog
      :visible="detailVisible"
      :task="detailTask"
      @update:visible="detailVisible = $event"
      @close="closeDetail"
      @cancel="handleCancel($event)"
    />

    <!-- ===== 新建任务弹窗 ===== -->
    <TaskBoardCreateDialog
      :visible="createDialogVisible"
      :submitting="createSubmitting"
      @update:visible="createDialogVisible = $event"
      @submit="handleCreateSubmit"
    />
  </div>
</template>

<script setup lang="ts">
// TODO(P1-3): 进一步拆分方向 — 状态管理逻辑可抽取为 useTaskBoard.ts
import { ref, reactive, computed, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { useI18n } from "vue-i18n";
import { useRoute, useRouter } from "vue-router";
import http from "@/utils/http";
import { API_CONFIG, buildApiPath } from "@/config/api";
import { useTasksStore, type TaskInfo } from "@/stores/tasks";
import TaskFilters from "@/components/task/TaskFilters.vue";
import TaskDetailDialog from "@/components/task/TaskDetailDialog.vue";
import TaskBoardHeader from "@/components/task_board/TaskBoardHeader.vue";
import TaskBoardErrorBanner from "@/components/task_board/TaskBoardErrorBanner.vue";
import TaskBoardKanban from "@/components/task_board/TaskBoardKanban.vue";
import TaskBoardListView from "@/components/task_board/TaskBoardListView.vue";
import TaskBoardCreateDialog from "@/components/task_board/TaskBoardCreateDialog.vue";

/* ------------------------------------------------------------------ */
/*  i18n                                                               */
/* ------------------------------------------------------------------ */
const { t } = useI18n();
const route = useRoute();
const router = useRouter();

/* ------------------------------------------------------------------ */
/*  Store                                                              */
/* ------------------------------------------------------------------ */
const tasksStore = useTasksStore();

/* ------------------------------------------------------------------ */
/*  State                                                              */
/* ------------------------------------------------------------------ */
const viewMode = ref<"kanban" | "list">("kanban");
const filterVisible = ref(true);
const fetchFailed = ref(false);

const filters = reactive({
  priority: "" as string,
  assignee: "" as string,
  dateRange: null as [string, string] | null,
  taskType: "" as string,
});

const detailVisible = ref(false);
const detailTask = ref<TaskInfo | null>(null);

const createDialogVisible = ref(false);
const createSubmitting = ref(false);

/* ------------------------------------------------------------------ */
/*  Lifecycle                                                          */
/* ------------------------------------------------------------------ */
onMounted(async () => {
  // 深链支持：/task-history 合并进来的旧链接带 ?view=list，落在列表视图
  if (route.query.view === "list") {
    viewMode.value = "list";
  }
  try {
    await tasksStore.fetchTasks();
  } catch {
    fetchFailed.value = true;
  }
  if (tasksStore.error && tasksStore.tasks.length === 0) {
    fetchFailed.value = true;
  }
});

async function retryFetch() {
  fetchFailed.value = false;
  try {
    await tasksStore.fetchTasks();
    if (!tasksStore.error) {
      fetchFailed.value = false;
    }
  } catch {
    fetchFailed.value = true;
  }
}

/* ------------------------------------------------------------------ */
/*  Computed — task source                                             */
/* ------------------------------------------------------------------ */
const allTasks = computed<TaskInfo[]>(() => {
  return tasksStore.tasks;
});

/* ------------------------------------------------------------------ */
/*  Computed — derived filter options                                  */
/* ------------------------------------------------------------------ */
const assigneeOptions = computed(() => {
  const set = new Set<string>();
  allTasks.value.forEach((task) => {
    if (task.owner_id) set.add(task.owner_id);
  });
  return Array.from(set).sort();
});

const taskTypeOptions = computed(() => {
  const set = new Set<string>();
  allTasks.value.forEach((task) => set.add(task.task_type));
  return Array.from(set).sort();
});

/* ------------------------------------------------------------------ */
/*  Computed — kanban columns                                          */
/* ------------------------------------------------------------------ */
interface KanbanColumn {
  key: string;
  label: string;
  items: TaskInfo[];
}

const kanbanColumns = computed<KanbanColumn[]>(() => {
  const src = allTasks.value;
  return [
    {
      key: "pending",
      label: t("taskBoard.colPending"),
      items: src.filter(
        (task) => task.status === "pending" || task.status === "queued",
      ),
    },
    {
      key: "running",
      label: t("taskBoard.colRunning"),
      items: src.filter((task) => task.status === "running"),
    },
    {
      key: "review",
      label: t("taskBoard.colReview"),
      items: src.filter((task) => task.status === "completed"),
    },
    {
      key: "done",
      label: t("taskBoard.colDone"),
      items: src.filter(
        (task) => task.status === "failed" || task.status === "cancelled",
      ),
    },
  ];
});

/* ------------------------------------------------------------------ */
/*  Computed — filtered kanban columns (applies user filters)           */
/* ------------------------------------------------------------------ */
const filteredColumns = computed<KanbanColumn[]>(() => {
  return kanbanColumns.value.map((col) => ({
    ...col,
    items: col.items.filter((task) => applyFilters(task)),
  }));
});

/* flat list for table view */
const flatFilteredTasks = computed(() => {
  return allTasks.value.filter((task) => applyFilters(task));
});

function mapPriority(task: TaskInfo): "high" | "medium" | "low" {
  if (task.status === "failed") return "high";
  if (task.error) return "high";
  const p = task.params?.priority as string | undefined;
  if (p === "high" || p === "urgent") return "high";
  if (p === "low") return "low";
  if (task.status === "running") return "medium";
  return "medium";
}

function applyFilters(task: TaskInfo): boolean {
  if (filters.priority && mapPriority(task) !== filters.priority) return false;
  if (filters.assignee && task.owner_id !== filters.assignee) return false;
  if (filters.taskType && task.task_type !== filters.taskType) return false;
  if (filters.dateRange && filters.dateRange[0] && filters.dateRange[1]) {
    const d = task.created_at.slice(0, 10);
    if (d < filters.dateRange[0] || d > filters.dateRange[1]) return false;
  }
  return true;
}

/* ------------------------------------------------------------------ */
/*  Actions                                                            */
/* ------------------------------------------------------------------ */
function openDetail(task: TaskInfo) {
  detailTask.value = { ...task };
  detailVisible.value = true;
}

function closeDetail() {
  detailVisible.value = false;
  detailTask.value = null;
}

async function handleCancel(jobId: string) {
  await tasksStore.cancelTask(jobId);
  ElMessage.success(t("taskBoard.msgTaskCancelled"));
}

/* ------------------------------------------------------------------ */
/*  重跑（原 TaskHistory 能力：按任务类型重放训练/批量推理）              */
/* ------------------------------------------------------------------ */
async function handleRerun(task: TaskInfo) {
  if (!task.params) {
    ElMessage.warning(t("taskBoard.msgCannotRerun"));
    return;
  }

  try {
    let res;
    if (task.task_type === "lnn_training") {
      res = await http.post(buildApiPath(API_CONFIG.LNN, "/train"), {
        model_name: task.params.model_name,
        data_path: task.params.data_path,
        hyperparameters: task.params.hyperparameters,
        device: task.params.device,
      });
    } else if (task.task_type === "lnn_batch_inference") {
      res = await http.post(buildApiPath(API_CONFIG.LNN, "/batch-inference"), {
        model_name: task.params.model_name,
        input_data: task.params.input_data,
        batch_size: task.params.batch_size,
      });
    }

    const newJobId = res?.data.data?.job_id;
    if (!newJobId) {
      ElMessage.error(t("taskBoard.msgNoNewJobId"));
      return;
    }

    ElMessage.success(t("taskBoard.msgNewJobStarted", { jobId: newJobId }));
    router.push({
      name: "workspace",
      query: { tab: "train", jobId: newJobId },
    });
  } catch (e: unknown) {
    const errorMsg = e instanceof Error ? e.message : String(e);
    ElMessage.error(errorMsg || t("taskBoard.msgRerunFailed"));
  }
}

/* ------------------------------------------------------------------ */
/*  新建任务                                                            */
/* ------------------------------------------------------------------ */
function handleCreate() {
  createDialogVisible.value = true;
}

async function handleCreateSubmit(data: { name: string; task_type: string }) {
  if (!data.name.trim()) {
    ElMessage.warning(t("taskBoard.msgNameRequired"));
    return;
  }
  if (!data.task_type) {
    ElMessage.warning(t("taskBoard.msgTypeRequired"));
    return;
  }
  createSubmitting.value = true;
  try {
    const res = await http.post(API_CONFIG.JOBS, {
      name: data.name.trim(),
      task_type: data.task_type,
      params: {},
    });
    if (res.data.code === 0) {
      ElMessage.success(t("taskBoard.msgCreateSuccess"));
      createDialogVisible.value = false;
      tasksStore.fetchTasks();
    } else {
      ElMessage.error(res.data.message || t("taskBoard.msgCreateFailed"));
    }
  } catch (e: unknown) {
    console.warn("[TaskBoard] create task failed:", e);
    ElMessage.error(t("taskBoard.msgCreateFailed"));
  } finally {
    createSubmitting.value = false;
  }
}
</script>

<style scoped>
/* ==================== Page Layout ==================== */
.task-board-page {
  padding: var(--page-padding);
  max-width: var(--content-max-width);
  margin: 0 auto;
}

/* ==================== Board Loading ==================== */
.board-loading {
  padding: 40px 0;
}

/* ==================== Empty State ==================== */
.empty-state {
  padding: 60px 0;
}

/* ==================== Responsive ==================== */
@media (max-width: 768px) {
  .task-board-page {
    padding: 16px;
  }
}
</style>
