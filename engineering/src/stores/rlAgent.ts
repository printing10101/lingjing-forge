/**
 * RL Agent Pinia Store（ADR-017 阶段 8 p8-6：决策推理 + 训练控制）
 *
 * 对接后端 `python/app/api/v1/rl_agent.py` 6 个端点：
 *   - GET    /api/v1/rl-agent/versions                 列出 RL 策略版本（分页 + algorithm/active 过滤）
 *   - GET    /api/v1/rl-agent/versions/{version}       查询策略版本详情
 *   - POST   /api/v1/rl-agent/act                      直接决策（不走工作流）
 *   - GET    /api/v1/rl-agent/training/status          查询训练状态
 *   - POST   /api/v1/rl-agent/training/start           启动训练 Workflow
 *   - POST   /api/v1/rl-agent/training/stop            停止训练
 *
 * 设计要点：
 *   1. **离线 RL 优先**：v1 仅支持基于历史数据 + 仿真环境的离线 RL
 *   2. **SafetyShield 硬约束**：强制过滤违反安全约束的动作，不可被 RL 策略覆盖
 *   3. **PPO 算法**：默认策略算法，clipped objective + GAE 优势估计
 *   4. **动作向量约定**：4 维 delta（主轴转速/进给/切深/切宽），取值 [-1, 1]
 *   5. POST /act 不持久化决策结果，前端如需保存可走工作流 rl_act 任务类型
 *   6. 训练控制端点为"指令式"：start 创建 RUNNING 记录，stop 置为 STOPPING，
 *      实际训练循环由后台 worker 异步执行
 *   7. recommended_action.reasoning 显式提示"本动作仅供 CAM 验证层参考，
 *      实际加工需经持证操作员审核"
 *   8. 错误统一通过 extractErrorMessage 提取，存入 error state
 */
import { defineStore } from "pinia";
import { ref, computed } from "vue";
import http from "@/utils/http";
import { extractErrorMessage } from "@/utils/error-handler";
import { unwrap } from "@/utils/response";
import { API_CONFIG, buildApiPath } from "@/config/api";
import {
  type PolicyVersion,
  type RLActRequest,
  type RLActResponse,
  type TrainingStatusInfo,
  type TrainingStartRequest,
  type ListPolicyVersionsParams,
  type ListPolicyVersionsResponse,
  type TrainingStatus,
} from "@/contracts/rl_agent";
import {
  TRAINING_STATUS,
  isTerminalTrainingStatus,
} from "@/contracts/rl_agent";

// Store 局部类型（派生 / UI 辅助）

/** 策略版本列表分页信息。 */
interface PolicyVersionPaginationState {
  total: number;
  limit: number;
  offset: number;
}

/** fetchVersions 入参（与后端 Query 参数对齐）。 */
interface FetchPolicyVersionsParams extends ListPolicyVersionsParams {}

// Store 定义

export const useRlAgentStore = defineStore("rlAgent", () => {
  // State：策略版本列表

  /** RL 策略版本列表（list 端点返回的 items）。 */
  const versions = ref<PolicyVersion[]>([]);
  /** 策略版本列表分页信息。 */
  const versionPagination = ref<PolicyVersionPaginationState>({
    total: 0,
    limit: 50,
    offset: 0,
  });

  // State：当前版本详情

  /** 当前查看的 RL 策略版本详情。 */
  const currentVersion = ref<PolicyVersion | null>(null);

  // State：最近一次决策结果

  /** 最近一次 RL 决策响应（含推荐动作 + 候选评估 + 策略元信息）。 */
  const lastAction = ref<RLActResponse | null>(null);

  // State：训练状态

  /** 当前 RL 训练状态（status / 进度 / 指标 / 时间戳）。 */
  const trainingStatus = ref<TrainingStatusInfo | null>(null);

  // Loading 标志

  const versionsLoading = ref(false); // GET /versions
  const versionLoading = ref(false); // GET /versions/{version}
  const acting = ref(false); // POST /act
  const trainingStatusLoading = ref(false); // GET /training/status
  const startingTraining = ref(false); // POST /training/start
  const stoppingTraining = ref(false); // POST /training/stop
  const error = ref<string | null>(null);

  // Computed

  /** 是否有 RL 策略版本。 */
  const hasVersions = computed<boolean>(() => versions.value.length > 0);

  /** 当前激活的策略版本（is_active=true）。 */
  const activeVersion = computed<PolicyVersion | null>(
    () => versions.value.find((v) => v.is_active) ?? null,
  );

  /** 版本列表当前页码（1-based）。 */
  const currentPage = computed<number>(() =>
    versionPagination.value.limit > 0
      ? Math.floor(
          versionPagination.value.offset / versionPagination.value.limit,
        ) + 1
      : 1,
  );

  /** 版本列表总页数。 */
  const totalPages = computed<number>(() =>
    versionPagination.value.limit > 0
      ? Math.ceil(versionPagination.value.total / versionPagination.value.limit)
      : 1,
  );

  /** 是否有任何加载操作进行中。 */
  const anyLoading = computed<boolean>(
    () =>
      versionsLoading.value ||
      versionLoading.value ||
      acting.value ||
      trainingStatusLoading.value ||
      startingTraining.value ||
      stoppingTraining.value,
  );

  /** 当前训练状态字符串（无记录时为 idle）。 */
  const currentTrainingStatus = computed<TrainingStatus>(
    () => trainingStatus.value?.status ?? TRAINING_STATUS.IDLE,
  );

  /** 是否正在训练中（RUNNING / STOPPING 均视为占用训练槽位）。 */
  const isTraining = computed<boolean>(
    () =>
      currentTrainingStatus.value === TRAINING_STATUS.RUNNING ||
      currentTrainingStatus.value === TRAINING_STATUS.STOPPING,
  );

  /** 训练是否处于终态（COMPLETED / FAILED）。 */
  const isTrainingTerminal = computed<boolean>(() =>
    isTerminalTrainingStatus(currentTrainingStatus.value),
  );

  /** 训练进度百分比 [0, 100]。 */
  const trainingProgress = computed<number>(() => {
    const info = trainingStatus.value;
    if (!info || info.max_steps <= 0) return 0;
    return Math.min(
      100,
      Math.round((info.current_step / info.max_steps) * 100),
    );
  });

  /** 最近一次决策推荐的动作是否安全通过（无安全违反）。 */
  const lastActionIsSafe = computed<boolean>(() => {
    const action = lastAction.value?.recommended_action?.reasoning ?? "";
    return !action.includes("违反") && !action.includes("已回退");
  });

  // unwrap() 已提取到 @/utils/response.ts（消除 6 个 Store 的重复定义）

  // Actions：版本管理

  /**
   * 列出 RL 策略版本（分页 + algorithm/active 过滤）.
   * 对应 GET /api/v1/rl-agent/versions
   *
   * @param params - 查询参数（active_only / algorithm / limit / offset）
   * @returns 策略版本列表响应，失败返回 null
   */
  async function fetchVersions(
    params: FetchPolicyVersionsParams = {},
  ): Promise<ListPolicyVersionsResponse | null> {
    versionsLoading.value = true;
    error.value = null;
    try {
      const response = await http.get(
        buildApiPath(API_CONFIG.RL_AGENT, "/versions"),
        {
          params: {
            active_only: params.active_only ?? false,
            algorithm: params.algorithm ?? undefined,
            limit: params.limit ?? 50,
            offset: params.offset ?? 0,
          },
        },
      );
      const payload = unwrap<ListPolicyVersionsResponse>(response);
      versions.value = payload.items ?? [];
      versionPagination.value = {
        total: payload.total ?? 0,
        limit: payload.limit ?? 50,
        offset: payload.offset ?? 0,
      };
      return payload;
    } catch (e) {
      error.value = extractErrorMessage(e, "获取 RL 策略版本列表失败");
      return null;
    } finally {
      versionsLoading.value = false;
    }
  }

  /**
   * 查询 RL 策略版本详情.
   * 对应 GET /api/v1/rl-agent/versions/{version}
   *
   * @param version - 版本号（semver）
   * @returns 策略版本详情，失败返回 null
   */
  async function fetchVersion(version: string): Promise<PolicyVersion | null> {
    versionLoading.value = true;
    error.value = null;
    try {
      const response = await http.get(
        buildApiPath(
          API_CONFIG.RL_AGENT,
          `/versions/${encodeURIComponent(version)}`,
        ),
      );
      const payload = unwrap<PolicyVersion>(response);
      currentVersion.value = payload;
      return payload;
    } catch (e) {
      error.value = extractErrorMessage(e, "获取 RL 策略版本详情失败");
      return null;
    } finally {
      versionLoading.value = false;
    }
  }

  // Actions：决策推理

  /**
   * 执行 RL 决策（不走工作流，直接调用服务层）.
   * 对应 POST /api/v1/rl-agent/act
   *
   * 流程：
   *   1. 提交当前状态 + 候选动作集 + 优化目标 + 安全约束
   *   2. 服务层执行策略前向 + SafetyShield 硬约束过滤 + 候选评估
   *   3. 返回推荐动作 + 候选评估列表 + 策略元信息
   *
   * 工程约束：
   *   - candidate_actions 至少 1 个，每个含 4 个 delta 字段
   *   - safety_constraints 为空时后端使用默认值
   *   - 推荐动作的 reasoning 显式提示"仅供 CAM 验证层参考，
   *     实际加工需经持证操作员审核"
   *   - 决策结果不持久化，如需保存走工作流 rl_act 任务类型
   *
   * @param request - 决策请求（含 current_state / candidate_actions / optimization_target / safety_constraints / model_uri）
   * @returns 决策响应（含推荐动作 + 候选评估 + 策略元信息），失败返回 null
   */
  async function act(request: RLActRequest): Promise<RLActResponse | null> {
    acting.value = true;
    error.value = null;
    try {
      const response = await http.post(
        buildApiPath(API_CONFIG.RL_AGENT, "/act"),
        request,
      );
      const payload = unwrap<RLActResponse>(response);
      lastAction.value = payload;
      return payload;
    } catch (e) {
      error.value = extractErrorMessage(e, "RL 决策失败");
      return null;
    } finally {
      acting.value = false;
    }
  }

  // Actions：训练控制

  /**
   * 查询当前 RL 训练状态.
   * 对应 GET /api/v1/rl-agent/training/status
   *
   * 返回字段：
   *   - status: 训练状态（idle / running / paused / completed / failed / stopping）
   *   - current_step / max_steps / current_episode
   *   - metrics: 最新训练指标快照（仅 RUNNING 时返回）
   *   - started_at / finished_at / error_message
   *
   * 若无训练记录，返回 status=idle.
   *
   * @returns 训练状态信息，失败返回 null
   */
  async function fetchTrainingStatus(): Promise<TrainingStatusInfo | null> {
    trainingStatusLoading.value = true;
    error.value = null;
    try {
      const response = await http.get(
        buildApiPath(API_CONFIG.RL_AGENT, "/training/status"),
      );
      const payload = unwrap<TrainingStatusInfo>(response);
      trainingStatus.value = payload;
      return payload;
    } catch (e) {
      error.value = extractErrorMessage(e, "获取 RL 训练状态失败");
      return null;
    } finally {
      trainingStatusLoading.value = false;
    }
  }

  /**
   * 启动 RL 训练 Workflow.
   * 对应 POST /api/v1/rl-agent/training/start
   *
   * 流程：
   *   1. Pydantic 自动校验 max_steps / algorithm / optimization_target
   *   2. 服务层校验真实轨迹数据集（RL_AGENT_TRAINING_DATA），缺失时拒绝
   *   3. 创建 RUNNING 记录，后台线程执行 PPOTrainer 完整训练循环
   *      （ReplayOfflineEnvironment 回放真实历史轨迹），终态回写数据库
   *
   * 工程约束：
   *   - 仅离线 RL：训练数据来自真实历史轨迹回放，不连接真实机床
   *   - 物理执行需"持证操作员 + 导师签字 + 保险"，本端点不涉及
   *
   * @param request - 启动训练请求（含 max_steps / seed / algorithm / optimization_target）
   * @returns 训练状态信息（RUNNING），失败返回 null
   */
  async function startTraining(
    request: TrainingStartRequest,
  ): Promise<TrainingStatusInfo | null> {
    startingTraining.value = true;
    error.value = null;
    try {
      const response = await http.post(
        buildApiPath(API_CONFIG.RL_AGENT, "/training/start"),
        request,
      );
      const payload = unwrap<TrainingStatusInfo>(response);
      trainingStatus.value = payload;
      return payload;
    } catch (e) {
      error.value = extractErrorMessage(e, "启动 RL 训练失败");
      return null;
    } finally {
      startingTraining.value = false;
    }
  }

  /**
   * 停止 RL 训练.
   * 对应 POST /api/v1/rl-agent/training/stop
   *
   * 将当前 RUNNING 训练置为 STOPPING，训练循环会在下一个 checkpoint
   * 边界退出并保存快照。
   *
   * @returns 训练状态信息（STOPPING），失败返回 null
   */
  async function stopTraining(): Promise<TrainingStatusInfo | null> {
    stoppingTraining.value = true;
    error.value = null;
    try {
      const response = await http.post(
        buildApiPath(API_CONFIG.RL_AGENT, "/training/stop"),
      );
      const payload = unwrap<TrainingStatusInfo>(response);
      trainingStatus.value = payload;
      return payload;
    } catch (e) {
      error.value = extractErrorMessage(e, "停止 RL 训练失败");
      return null;
    } finally {
      stoppingTraining.value = false;
    }
  }

  // 清理方法

  /** 清空当前版本详情。 */
  function clearCurrentVersion(): void {
    currentVersion.value = null;
  }

  /** 清空最近一次决策结果。 */
  function clearLastAction(): void {
    lastAction.value = null;
  }

  /** 清空训练状态。 */
  function clearTrainingStatus(): void {
    trainingStatus.value = null;
  }

  /** 重置 Store 到初始状态。 */
  function $reset(): void {
    versions.value = [];
    versionPagination.value = { total: 0, limit: 50, offset: 0 };
    currentVersion.value = null;
    lastAction.value = null;
    trainingStatus.value = null;
    versionsLoading.value = false;
    versionLoading.value = false;
    acting.value = false;
    trainingStatusLoading.value = false;
    startingTraining.value = false;
    stoppingTraining.value = false;
    error.value = null;
  }

  return {
    // State
    versions,
    versionPagination,
    currentVersion,
    lastAction,
    trainingStatus,
    versionsLoading,
    versionLoading,
    acting,
    trainingStatusLoading,
    startingTraining,
    stoppingTraining,
    error,
    // Computed
    hasVersions,
    activeVersion,
    currentPage,
    totalPages,
    anyLoading,
    currentTrainingStatus,
    isTraining,
    isTrainingTerminal,
    trainingProgress,
    lastActionIsSafe,
    // Actions
    fetchVersions,
    fetchVersion,
    act,
    fetchTrainingStatus,
    startTraining,
    stopTraining,
    // 清理方法
    clearCurrentVersion,
    clearLastAction,
    clearTrainingStatus,
    $reset,
  };
});
