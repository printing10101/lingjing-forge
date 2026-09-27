import { defineStore } from "pinia";
import { ref, computed } from "vue";
import { extractErrorMessage } from "@/utils/error-handler";
import http from "@/utils/http";
import { API_CONFIG, buildApiPath } from "@/config/api";

/** 任务基本信息接口 */
export interface TaskInfo {
  job_id: string;
  task_type: string;
  status:
    | "pending"
    | "queued"
    | "running"
    | "completed"
    | "failed"
    | "cancelled";
  progress: number;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  duration_seconds?: number;
  owner_id?: string;
  error?: string;
  params?: Record<string, unknown>;
  result?: Record<string, unknown>;
  metrics?: Record<string, unknown>;
  progress_redis?: Record<string, unknown>;
}

/** 任务统计信息接口 */
export interface TaskStats {
  total_tasks: number;
  active_tasks: number;
  queued_tasks: number;
  completed_tasks: number;
  failed_tasks: number;
  max_concurrent: number;
  available_slots: number;
}

/** 任务管理 Store */
export const useTasksStore = defineStore("tasks", () => {
  /** 任务列表 */
  const tasks = ref<TaskInfo[]>([]);
  /** 当前任务 */
  const currentTask = ref<TaskInfo | null>(null);
  /** 任务统计 */
  const stats = ref<TaskStats>({
    total_tasks: 0,
    active_tasks: 0,
    queued_tasks: 0,
    completed_tasks: 0,
    failed_tasks: 0,
    max_concurrent: 3,
    available_slots: 3,
  });
  const loading = ref(false);
  /** 错误信息 */
  const error = ref<string | null>(null);

  const activeTasks = computed(() =>
    tasks.value.filter((t) => t.status === "running" || t.status === "queued"),
  );

  const completedTasks = computed(() =>
    tasks.value.filter((t) => t.status === "completed"),
  );

  const failedTasks = computed(() =>
    tasks.value.filter((t) => t.status === "failed"),
  );

  const cancelledTasks = computed(() =>
    tasks.value.filter((t) => t.status === "cancelled"),
  );

  /**
   * 获取任务列表
   * @param params - 查询参数：task_type、status、owner_id、limit、offset
   */
  async function fetchTasks(params?: {
    task_type?: string;
    status?: string;
    owner_id?: string;
    limit?: number;
    offset?: number;
  }) {
    loading.value = true;
    error.value = null;
    try {
      const searchParams = new URLSearchParams();
      if (params?.task_type) searchParams.set("task_type", params.task_type);
      if (params?.status) searchParams.set("status", params.status);
      if (params?.owner_id) searchParams.set("owner_id", params.owner_id);
      searchParams.set("limit", String(params?.limit ?? 50));
      searchParams.set("offset", String(params?.offset ?? 0));

      const response = await http.get(API_CONFIG.JOBS, {
        params: Object.fromEntries(searchParams),
      });
      const json = response.data;
      if (json.code === 0) {
        tasks.value = json.data.jobs || [];
      } else {
        error.value = json.message || "Failed to fetch tasks";
      }
    } catch (e: unknown) {
      error.value = extractErrorMessage(e, "获取任务列表失败");
    } finally {
      loading.value = false;
    }
  }

  /**
   * 获取单个任务详情
   * @param jobId - 任务ID
   * @returns 任务详情或null
   */
  async function fetchTask(jobId: string): Promise<TaskInfo | null> {
    loading.value = true;
    error.value = null;
    try {
      const response = await http.get(
        buildApiPath(API_CONFIG.JOBS, `/${jobId}`),
      );
      const json = response.data;
      if (json.code === 0) {
        currentTask.value = json.data;
        return json.data;
      } else {
        error.value = json.message || "Task not found";
        return null;
      }
    } catch (e: unknown) {
      error.value = extractErrorMessage(e, "获取任务详情失败");
      return null;
    } finally {
      loading.value = false;
    }
  }

  /**
   * 获取任务进度
   * @param jobId - 任务ID
   * @returns 进度数据或null
   */
  async function fetchTaskProgress(jobId: string): Promise<unknown> {
    try {
      const response = await http.get(
        buildApiPath(API_CONFIG.JOBS, `/${jobId}/progress`),
      );
      const json = response.data;
      if (json.code === 0 && currentTask.value) {
        currentTask.value.progress =
          json.data.progress_db ?? currentTask.value.progress;
        currentTask.value.progress_redis = {
          progress: json.data.progress_redis,
          message: json.data.message,
          metrics: json.data.metrics,
        };
      }
      return json;
    } catch (e: unknown) {
      error.value = extractErrorMessage(e, "获取任务进度失败");
      return null;
    }
  }

  /**
   * 取消任务
   * @param jobId - 任务ID
   * @returns API响应数据或null
   */
  async function cancelTask(jobId: string): Promise<unknown> {
    loading.value = true;
    error.value = null;
    try {
      const response = await http.post(
        buildApiPath(API_CONFIG.JOBS, `/${jobId}/cancel`),
      );
      const json = response.data;
      if (json.code === 0) {
        const idx = tasks.value.findIndex((t) => t.job_id === jobId);
        if (idx !== -1) {
          tasks.value[idx] = { ...tasks.value[idx], status: "cancelled" };
        }
        if (currentTask.value?.job_id === jobId) {
          currentTask.value = { ...currentTask.value, status: "cancelled" };
        }
      } else {
        error.value = json.message || "Failed to cancel task";
      }
      return json;
    } catch (e: unknown) {
      error.value = extractErrorMessage(e, "取消任务失败");
      return null;
    } finally {
      loading.value = false;
    }
  }

  /**
   * 获取任务统计信息
   * @returns 统计数据或null
   */
  async function fetchStats(): Promise<unknown> {
    try {
      const response = await http.get(buildApiPath(API_CONFIG.JOBS, "/stats"));
      const json = response.data;
      if (json.code === 0) {
        stats.value = json.data;
      }
      return json;
    } catch (e: unknown) {
      error.value = extractErrorMessage(e, "获取任务统计失败");
      return null;
    }
  }

  /**
   * 从 SSE 更新任务状态
   * @param jobId - 任务ID
   * @param status - 新状态
   * @param progress - 新进度
   */
  function updateTaskFromSSE(
    jobId: string,
    status: string,
    progress: number,
  ): void {
    const idx = tasks.value.findIndex((t) => t.job_id === jobId);
    if (idx !== -1) {
      tasks.value[idx] = {
        ...tasks.value[idx],
        status: status as TaskInfo["status"],
        progress,
      };
    }
    if (currentTask.value?.job_id === jobId) {
      currentTask.value = {
        ...currentTask.value,
        status: status as TaskInfo["status"],
        progress,
      };
    }
  }

  /** 重置所有任务状态 */
  function reset(): void {
    tasks.value = [];
    currentTask.value = null;
    loading.value = false;
    error.value = null;
  }

  return {
    tasks,
    currentTask,
    stats,
    loading,
    error,
    activeTasks,
    completedTasks,
    failedTasks,
    cancelledTasks,
    fetchTasks,
    fetchTask,
    fetchTaskProgress,
    cancelTask,
    fetchStats,
    updateTaskFromSSE,
    reset,
  };
});
