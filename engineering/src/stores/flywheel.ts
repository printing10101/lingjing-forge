/**
 * 数据飞轮 Pinia Store（ADR-005 阶段 4 p4-6b）
 *
 * 对接后端 `python/app/api/v1/flywheel.py` 4 个端点：
 *   - GET /api/v1/flywheel/status       飞轮当前状态
 *   - GET /api/v1/flywheel/metrics      指标详情（含历史）
 *   - GET /api/v1/flywheel/report/weekly  周报
 *   - GET /api/v1/flywheel/definitions   指标定义
 *
 * 飞轮指标 5 个维度全部来自真实数据源（IDatasetStore / ISnapshotStore）：
 *   - data_volume       加工记录数
 *   - model_quality     模型质量
 *   - adoption_rate     用户采纳率（从 0% 开始，随使用增长）
 *   - uncertainty_mean  不确定性均值
 *   - feedback_delay    回灌延迟
 *
 * 模型热更新部署记录通过 tasks API 提交 action=list_deployments 查询。
 */
import { defineStore } from "pinia";
import { ref, computed } from "vue";
import http from "@/utils/http";
import { extractErrorMessage } from "@/utils/error-handler";
import { formatDateTimeSafe } from "@/utils/formatters";
import { API_CONFIG, buildApiPath } from "@/config/api";

// 类型定义（与后端 python/app/api/v1/flywheel.py Pydantic schema 对齐）

/** 飞轮健康状态。 */
export type FlywheelHealthStatus =
  | "healthy"
  | "warning"
  | "critical"
  | "unknown";

/** 飞轮当前状态响应（对应 FlywheelStatusResponse）。 */
export interface FlywheelStatus {
  /** 飞轮状态: healthy / warning / critical */
  status: FlywheelHealthStatus;
  /** 加工记录数（条） */
  data_volume: number;
  /** 模型质量（%，0-100） */
  model_quality: number;
  /** 用户采纳率（%，0-100） */
  adoption_rate: number;
  /** 不确定性均值（0-1） */
  uncertainty_mean: number;
  /** 回灌延迟（分钟） */
  feedback_delay: number;
  /** 健康分数（0-100） */
  health_score: number;
  /** 采集时间（ISO 8601） */
  timestamp: string;
}

/** 单个时间点的飞轮指标快照（历史数据点）。 */
export interface FlywheelMetricPoint {
  timestamp: string;
  data_volume: number;
  model_quality: number;
  adoption_rate: number;
  uncertainty_mean: number;
  feedback_delay: number;
}

/** /metrics 端点响应。 */
export interface FlywheelMetricsResponse {
  current: FlywheelMetricPoint;
  historical: FlywheelMetricPoint[];
  period_days: number;
}

/** 单个指标的定义说明。 */
export interface MetricDefinition {
  name: string;
  description: string;
  unit: string;
  range: string;
  calculation: string;
}

/** /definitions 端点响应。 */
export interface MetricDefinitionsResponse {
  metrics: MetricDefinition[];
}

/** 周报响应（对应 FlywheelReportResponse）。 */
export interface FlywheelWeeklyReport {
  report_type: string;
  generated_at: string;
  period: Record<string, string>;
  current_metrics: Record<string, unknown>;
  trends: Record<string, unknown>;
  summary: Record<string, unknown>;
  /** 保存到文件时返回的路径（可选）。 */
  saved_to?: string;
}

/** 模型热更新部署状态（对应后端 DeploymentStatus）。 */
export type DeploymentStatus =
  | "deploying"
  | "observing"
  | "promoted"
  | "rolled_back"
  | "failed";

/** 模型热更新部署记录（对应后端 DeploymentRecord.to_dict()）。 */
export interface DeploymentRecord {
  deployment_id: string;
  model_name: string;
  new_model_uri: string;
  baseline_model_uri: string;
  status: DeploymentStatus;
  canary_ratio: number;
  observation_hours: number;
  rollback_on_failure: boolean;
  rollback_metric_drop: number;
  eval_metric: string;
  eval_metrics: Record<string, number>;
  baseline_metrics?: Record<string, number> | null;
  canary_metrics?: Record<string, number> | null;
  decision?: string | null;
  reason?: string | null;
  created_at: string;
  updated_at: string;
  metadata?: Record<string, unknown> | null;
}

/** 反馈统计（从 status 派生）。 */
export interface FeedbackStats {
  /** 加工记录总数。 */
  dataVolume: number;
  /** 用户采纳率（%）。 */
  adoptionRate: number;
  /** 回灌延迟（分钟）。 */
  feedbackDelay: number;
  /** 健康分数。 */
  healthScore: number;
}

// Store 定义

export const useFlywheelStore = defineStore("flywheel", () => {
  // State
  const status = ref<FlywheelStatus | null>(null);
  const currentMetrics = ref<FlywheelMetricPoint | null>(null);
  const historicalMetrics = ref<FlywheelMetricPoint[]>([]);
  const metricsPeriodDays = ref<number>(7);
  const weeklyReport = ref<FlywheelWeeklyReport | null>(null);
  const metricDefinitions = ref<MetricDefinition[]>([]);
  const deployments = ref<DeploymentRecord[]>([]);

  const loading = ref(false);
  const metricsLoading = ref(false);
  const reportLoading = ref(false);
  const definitionsLoading = ref(false);
  const deploymentsLoading = ref(false);
  const error = ref<string | null>(null);

  // Computed

  /** 健康状态对应的 Element Plus 标签类型。 */
  const healthTagType = computed<"success" | "warning" | "danger" | "info">(
    () => {
      const s = status.value?.status;
      if (s === "healthy") return "success";
      if (s === "warning") return "warning";
      if (s === "critical") return "danger";
      return "info";
    },
  );

  /** 健康状态中文标签。 */
  const healthStatusLabel = computed<string>(() => {
    const map: Record<FlywheelHealthStatus, string> = {
      healthy: "健康",
      warning: "警告",
      critical: "严重",
      unknown: "未知",
    };
    return map[status.value?.status ?? "unknown"] ?? "未知";
  });

  /** 反馈统计（从 status 派生）。 */
  const feedbackStats = computed<FeedbackStats>(() => ({
    dataVolume: status.value?.data_volume ?? 0,
    adoptionRate: status.value?.adoption_rate ?? 0,
    feedbackDelay: status.value?.feedback_delay ?? 0,
    healthScore: status.value?.health_score ?? 0,
  }));

  /** 当前活跃部署（observing 或 deploying 状态）。 */
  const activeDeployments = computed<DeploymentRecord[]>(() =>
    deployments.value.filter(
      (d) => d.status === "observing" || d.status === "deploying",
    ),
  );

  /** 已提升至生产的部署。 */
  const promotedDeployments = computed<DeploymentRecord[]>(() =>
    deployments.value.filter((d) => d.status === "promoted"),
  );

  /** 是否有任何指标在加载中。 */
  const anyLoading = computed<boolean>(
    () =>
      loading.value ||
      metricsLoading.value ||
      reportLoading.value ||
      definitionsLoading.value ||
      deploymentsLoading.value,
  );

  // Actions

  /**
   * 获取飞轮当前状态。
   * 对应 GET /api/v1/flywheel/status
   */
  async function fetchStatus(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      const response = await http.get(
        buildApiPath(API_CONFIG.FLYWHEEL, "/status"),
      );
      // 后端 response_model 直接返回对象（非 data 信封），但 http 拦截器
      // 已统一解包：成功时 response.data 即业务数据
      const data = response.data?.data ?? response.data;
      status.value = data as FlywheelStatus;
    } catch (e: unknown) {
      error.value = extractErrorMessage(e, "获取飞轮状态失败");
    } finally {
      loading.value = false;
    }
  }

  /**
   * 获取飞轮指标详情（含历史数据）。
   * 对应 GET /api/v1/flywheel/metrics?days={days}
   * @param days 历史数据天数范围（1-90，默认 7）
   */
  async function fetchMetrics(days: number = 7): Promise<void> {
    metricsLoading.value = true;
    error.value = null;
    try {
      const response = await http.get(
        buildApiPath(API_CONFIG.FLYWHEEL, "/metrics"),
        { params: { days } },
      );
      const data = response.data?.data ?? response.data;
      const payload = data as FlywheelMetricsResponse;
      currentMetrics.value = payload.current;
      historicalMetrics.value = payload.historical ?? [];
      metricsPeriodDays.value = payload.period_days ?? days;
    } catch (e: unknown) {
      error.value = extractErrorMessage(e, "获取飞轮指标失败");
    } finally {
      metricsLoading.value = false;
    }
  }

  /**
   * 生成每周飞轮报告。
   * 对应 GET /api/v1/flywheel/report/weekly?save={save}
   * @param save 是否同时保存报告到文件
   */
  async function fetchWeeklyReport(save: boolean = false): Promise<void> {
    reportLoading.value = true;
    error.value = null;
    try {
      const response = await http.get(
        buildApiPath(API_CONFIG.FLYWHEEL, "/report/weekly"),
        { params: { save } },
      );
      const data = response.data?.data ?? response.data;
      weeklyReport.value = data as FlywheelWeeklyReport;
    } catch (e: unknown) {
      error.value = extractErrorMessage(e, "生成飞轮周报失败");
    } finally {
      reportLoading.value = false;
    }
  }

  /**
   * 获取所有飞轮指标的定义说明。
   * 对应 GET /api/v1/flywheel/definitions
   */
  async function fetchDefinitions(): Promise<void> {
    definitionsLoading.value = true;
    error.value = null;
    try {
      const response = await http.get(
        buildApiPath(API_CONFIG.FLYWHEEL, "/definitions"),
      );
      const data = response.data?.data ?? response.data;
      metricDefinitions.value =
        (data as MetricDefinitionsResponse).metrics ?? [];
    } catch (e: unknown) {
      error.value = extractErrorMessage(e, "获取指标定义失败");
    } finally {
      definitionsLoading.value = false;
    }
  }

  /**
   * 查询模型热更新部署记录列表。
   *
   * 后端通过 GET /api/v1/flywheel/deployments 扫描本地模型存储目录
   * 返回真实部署产物记录（名称、路径、大小、更新时间）。
   * 接口不可用时降级为空列表（不影响看板其他模块）。
   *
   * @param modelName 按模型名筛选（可选）
   * @param statusFilter 按部署状态筛选（可选）
   */
  async function fetchDeployments(
    modelName?: string,
    statusFilter?: DeploymentStatus,
  ): Promise<void> {
    deploymentsLoading.value = true;
    error.value = null;
    try {
      const response = await http.get(
        buildApiPath(API_CONFIG.FLYWHEEL, "/deployments"),
      );
      const data = response.data?.data ?? response.data;
      // 后端返回 { deployments, count }，记录为模型目录扫描结果，
      // 在此映射为 DeploymentRecord 结构供看板表格渲染
      const raw =
        (data as { deployments?: Array<Record<string, unknown>> })
          ?.deployments ?? [];
      let list: DeploymentRecord[] = raw.map((r) => ({
        deployment_id: String(r.path ?? r.model_name ?? "unknown"),
        model_name: String(r.model_name ?? "unknown"),
        new_model_uri: String(r.path ?? ""),
        baseline_model_uri: "",
        status: "promoted" as DeploymentStatus,
        canary_ratio: 0,
        observation_hours: 0,
        rollback_on_failure: false,
        rollback_metric_drop: 0,
        eval_metric: "",
        eval_metrics: {},
        baseline_metrics: null,
        canary_metrics: null,
        decision: "model_scan",
        reason: "由本地模型目录扫描生成",
        created_at: String(r.updated_at ?? ""),
        updated_at: String(r.updated_at ?? ""),
        metadata: {
          size_bytes: r.size_bytes,
          size_human: r.size_human,
          version: r.version,
        },
      }));
      if (modelName) {
        list = list.filter((r) => r.model_name.includes(modelName));
      }
      if (statusFilter) {
        list = list.filter((r) => r.status === statusFilter);
      }
      deployments.value = list;
    } catch (e: unknown) {
      // 部署查询失败不阻塞看板其他模块，仅记录错误
      error.value = extractErrorMessage(e, "获取部署记录失败");
      deployments.value = [];
    } finally {
      deploymentsLoading.value = false;
    }
  }

  /**
   * 一次性刷新所有飞轮数据（看板初始化时调用）。
   * @param days 历史数据天数
   */
  async function refreshAll(days: number = 7): Promise<void> {
    await Promise.allSettled([
      fetchStatus(),
      fetchMetrics(days),
      fetchWeeklyReport(false),
      fetchDefinitions(),
      fetchDeployments(),
    ]);
  }

  /**
   * 格式化时间戳为本地时间字符串。
   * @param ts ISO 8601 字符串
   */
  const formatTime = (ts: string | null | undefined): string =>
    formatDateTimeSafe(ts);

  /**
   * 格式化百分比（0-100）。
   * @param value 0-100 的数值
   * @param digits 保留小数位数
   */
  function formatPercent(
    value: number | null | undefined,
    digits: number = 1,
  ): string {
    if (value === null || value === undefined || Number.isNaN(value))
      return "-";
    return `${value.toFixed(digits)}%`;
  }

  /**
   * 格式化数字（千分位）。
   * @param value 数值
   */
  function formatNumber(value: number | null | undefined): string {
    if (value === null || value === undefined || Number.isNaN(value))
      return "-";
    return value.toLocaleString("zh-CN");
  }

  return {
    // state
    status,
    currentMetrics,
    historicalMetrics,
    metricsPeriodDays,
    weeklyReport,
    metricDefinitions,
    deployments,
    loading,
    metricsLoading,
    reportLoading,
    definitionsLoading,
    deploymentsLoading,
    error,
    // computed
    healthTagType,
    healthStatusLabel,
    feedbackStats,
    activeDeployments,
    promotedDeployments,
    anyLoading,
    // actions
    fetchStatus,
    fetchMetrics,
    fetchWeeklyReport,
    fetchDefinitions,
    fetchDeployments,
    refreshAll,
    // helpers
    formatTime,
    formatPercent,
    formatNumber,
  };
});
