"""RL Agent API - 强化学习决策与训练控制 REST 接口.

对应 ADR-017（世界模型与 RL 模块）第 8 节：RL Agent REST API 端点。

端点总览（prefix: ``/api/v1/rl-agent``）：
    GET    /versions                 列出 RL 策略版本（分页 + algorithm/active 过滤）
    GET    /versions/{version}       查询策略版本详情
    POST   /act                      直接决策（不走工作流，调用 RLAgentService.act）
    GET    /training/status          查询训练状态
    POST   /training/start           启动训练 Workflow
    POST   /training/stop            停止训练

权限模型：
    rl_agent:read   —— 列出策略版本 / 查询版本详情 / 查询训练状态
    rl_agent:write  —— 直接决策 / 启动训练 / 停止训练

设计说明
--------
    - ``POST /act`` 不持久化决策结果，仅返回推荐动作 + 候选评估 + 策略元信息；
      前端如需持久化可走工作流 ``rl_act`` 任务类型
    - 训练控制端点为"指令式"：``start`` 创建 RUNNING 记录，``stop`` 将其置为
      STOPPING，实际训练循环由后台 worker 异步执行
    - 安全约束 ``SafetyConstraintsSpec`` 通过请求体传入，未传则使用默认值
    - 服务层异常通过 ``_handle_service_exception`` 统一映射为 API 错误响应
    - 所有决策响应包含 ``reasoning`` 字段（自然语言推荐理由），强调
      "本动作仅供 CAM 验证层参考，实际加工需经持证操作员审核"
"""

from __future__ import annotations

import logging


from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.auth.permissions import require_permission
from app.core.response import ErrorCode, error, success
from app.dependencies import get_rl_agent_service
from app.dependencies import get_sovereignty_policy
from app.contracts.rl_agent import (
    OptimizationTarget,
    PolicyAlgorithm,
    PolicyError,
    PolicyNotFoundError,
    RLActError,
    RLActRequest,
    SafetyConstraintsSpec,
    SafetyViolationError,
    TrainingAlreadyRunningError,
    TrainingError,
    TrainingStartRequest,
    TrainingStatus,
)

logger = logging.getLogger(__name__)

# 注意：本 router 尚未接入（main/router_registry 未引用本文件），路由未挂载。
router = APIRouter(prefix="/api/v1/rl-agent", tags=["RL Agent"])


# Pydantic 请求模型


class SafetyConstraintsModel(BaseModel):
    """安全约束规格（与 ``SafetyConstraintsSpec`` 对齐）."""

    max_chatter_probability: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="最大允许颤振概率 [0, 1]，默认 0.3",
    )
    max_tool_wear_increment: float = Field(
        default=0.01,
        gt=0.0,
        description="最大允许刀具磨损增量 (mm/步)，默认 0.01",
    )
    min_surface_quality: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="最小表面质量 [0, 1]，默认 0.8",
    )

    def to_spec(self) -> SafetyConstraintsSpec:
        """转换为契约层 dataclass."""
        return SafetyConstraintsSpec(
            max_chatter_probability=self.max_chatter_probability,
            max_tool_wear_increment=self.max_tool_wear_increment,
            min_surface_quality=self.min_surface_quality,
        )


class RLActRequestModel(BaseModel):
    """RL 决策请求体.

    与 ``app.contracts.rl_agent.RLActRequest`` 对齐，但使用 Pydantic
    以获得自动校验和 OpenAPI 文档。
    """

    current_state: dict[str, float] = Field(
        ...,
        description="当前加工状态（字段名见 StateField，至少包含全部 8 个状态字段）",
    )
    candidate_actions: list[dict[str, float]] = Field(
        ...,
        min_length=1,
        description="候选动作集（至少 1 个，每个动作含 4 个 delta 字段）",
    )
    optimization_target: str = Field(
        default=OptimizationTarget.BALANCE,
        description=f"优化目标（{OptimizationTarget.all()}，默认 balance）",
    )
    safety_constraints: SafetyConstraintsModel | None = Field(
        default=None,
        description="安全约束规格（为空则使用默认值）",
    )
    model_uri: str = Field(
        default="model://rl_agent/1.0.0",
        min_length=1,
        max_length=256,
        description="RL 策略模型 URI",
    )


class TrainingStartRequestModel(BaseModel):
    """启动训练请求体.

    与 ``app.contracts.rl_agent.TrainingStartRequest`` 对齐。
    """

    max_steps: int = Field(
        default=100000,
        ge=1000,
        le=1_000_000,
        description="最大训练步数（1000 ~ 1000000，默认 100000）",
    )
    seed: int | None = Field(
        default=None,
        ge=0,
        le=2**31 - 1,
        description="随机种子（为空则使用训练器默认 42）",
    )
    algorithm: str = Field(
        default=PolicyAlgorithm.PPO,
        description=f"策略算法（{PolicyAlgorithm.all()}，默认 ppo）",
    )
    optimization_target: str = Field(
        default=OptimizationTarget.BALANCE,
        description=f"优化目标（{OptimizationTarget.all()}，默认 balance）",
    )


# 辅助函数


def _handle_service_exception(e: Exception, *, action: str):
    """统一处理服务层异常 → API 错误响应.

    风格与 explainability.py / world_model.py 对齐。

    Args:
        e: 服务层抛出的异常
        action: 当前操作描述（用于日志）

    Returns:
        error() 响应对象
    """
    if isinstance(e, PolicyNotFoundError):
        return error(
            code=ErrorCode.NOT_FOUND,
            message=str(e),
            suggestion="请确认 model_uri 正确，或通过 GET /versions 查看可用策略版本",
        )
    if isinstance(e, SafetyViolationError):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
            suggestion="所有候选动作均违反安全约束，请放宽 safety_constraints 或提供更多候选动作",
            recoverable=True,
        )
    if isinstance(e, TrainingAlreadyRunningError):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
            suggestion="已有训练任务运行中，请先调用 POST /training/stop 停止当前训练",
            recoverable=True,
        )
    if isinstance(e, PolicyError):
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=str(e),
            suggestion="策略推理失败：模型权重可能未加载或维度不匹配，请检查 model_uri",
        )
    if isinstance(e, TrainingError):
        return error(
            code=ErrorCode.INTERNAL_ERROR,
            message=str(e),
            suggestion="训练控制失败：请检查训练配置或查看后端日志",
        )
    if isinstance(e, ValueError):
        return error(code=ErrorCode.INVALID_REQUEST, message=str(e))
    if isinstance(e, RLActError):
        logger.error("RLAgent error during %s: %s", action, e, exc_info=True)
        return error(code=ErrorCode.INTERNAL_ERROR, message=str(e))
    # 兜底：未识别的异常
    logger.error("Unexpected error during %s: %s", action, e, exc_info=True)
    return error(
        code=ErrorCode.INTERNAL_ERROR,
        message=f"{action} 失败",
        detail=str(e),
    )


# 端点 1: GET /versions —— 列出 RL 策略版本


@router.get("/versions")
async def list_versions(
    active_only: bool = Query(False, description="为 true 时仅返回当前激活版本"),
    algorithm: str | None = Query(
        None,
        description=f"按策略算法过滤（{PolicyAlgorithm.all()}）",
    ),
    limit: int = Query(50, ge=1, le=500, description="每页数量（1-500，默认 50）"),
    offset: int = Query(0, ge=0, description="偏移量"),
):
    """分页列出 RL 策略版本.

    返回字段：
        - items: list[dict]（每个版本记录的 to_dict()）
        - total / limit / offset

    权限：``rl_agent:read``
    """
    # 前置校验：algorithm 合法性
    if algorithm is not None and not PolicyAlgorithm.is_valid(algorithm):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"algorithm 不支持: {algorithm}（支持: {PolicyAlgorithm.all()}）",
        )

    service = get_rl_agent_service()
    try:
        versions, total = await service.list_versions(
            active_only=active_only,
            algorithm=algorithm,
            limit=limit,
            offset=offset,
        )
    except Exception as e:
        return _handle_service_exception(e, action="列出 RL 策略版本")

    items = [v.to_dict() for v in versions]
    return success(
        data={
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        },
        message=f"RL 策略版本列表已获取（{len(items)} 条）",
    )


# 端点 2: GET /versions/{version} —— 查询策略版本详情


@router.get("/versions/{version}")
async def get_version(version: str):
    """查询 RL 策略版本详情.

    权限：``rl_agent:read``
    """
    service = get_rl_agent_service()
    try:
        version_record = await service.get_version(version)
    except Exception as e:
        return _handle_service_exception(e, action="查询 RL 策略版本详情")

    return success(
        data=version_record.to_dict(),
        message="RL 策略版本详情已获取",
    )


# 端点 3: POST /act —— 直接决策（不走工作流）


@router.post(
    "/act",
    dependencies=[Depends(require_permission("rl_agent:write"))],
)
async def act(request: RLActRequestModel):
    """执行 RL 决策（不走工作流，直接调用服务层）.

    流程：
        1. Pydantic 自动校验 candidate_actions 非空 / optimization_target 合法
        2. 构造契约层 ``RLActRequest``（再次校验，与 Pydantic 互补）
        3. 调用 ``RLAgentService.act()`` 执行策略前向 + 安全过滤 + 候选评估
        4. 返回推荐动作 + 候选评估列表 + 策略元信息

    权限：``rl_agent:write``（触发模型推理，消耗资源）

    工程约束
    --------
        返回的 ``recommended_action.reasoning`` 字段会显式提示：
        "本动作仅供 CAM 验证层参考，实际加工需经持证操作员审核"
    """
    # 前置校验：optimization_target 合法性
    if not OptimizationTarget.is_valid(request.optimization_target):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"optimization_target 不支持: {request.optimization_target}（支持: {OptimizationTarget.all()}）",
        )

    # 解析安全约束（为空则使用默认值）
    if request.safety_constraints is not None:
        try:
            safety_spec = request.safety_constraints.to_spec()
        except ValueError as e:
            return error(
                code=ErrorCode.INVALID_REQUEST,
                message=f"safety_constraints 不合法: {e}",
            )
    else:
        safety_spec = SafetyConstraintsSpec()

    # 构造契约层 dataclass
    try:
        contract_req = RLActRequest(
            current_state=request.current_state,
            candidate_actions=request.candidate_actions,
            optimization_target=request.optimization_target,
            safety_constraints=safety_spec,
            model_uri=request.model_uri,
        )
    except ValueError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
            suggestion="请检查 current_state / candidate_actions / optimization_target 字段",
        )

    service = get_rl_agent_service()
    try:
        response = await service.act(contract_req)
    except Exception as e:
        return _handle_service_exception(e, action="RL 决策")

    payload = response.to_dict()
    # W9.1 主权决策：告知调用方该推荐动作在当前自主等级下能否自动执行。
    # 注意 /act 本身只产出建议不执行；真正落点（工作流应用动作）必须消费
    # requires_confirmation，需确认时走审批流而非直接应用。
    decision = get_sovereignty_policy().evaluate_action("agent_action")
    payload["sovereignty"] = decision.to_dict()
    return success(
        data=payload,
        message=(
            f"RL 决策完成: 策略={response.policy_info.algorithm}，"
            f"候选数={len(response.action_evaluation)}，"
            f"推荐动作={'安全通过' if '违反' not in response.recommended_action.reasoning else '已回退'}"
            + ("；主权策略要求人工确认后方可应用" if decision.requires_confirmation else "")
        ),
    )


# 端点 4: GET /training/status —— 查询训练状态


@router.get("/training/status")
async def get_training_status():
    """查询当前 RL 训练状态.

    返回字段：
        - status: 训练状态（idle / running / paused / completed / failed / stopping）
        - current_step / max_steps / current_episode
        - metrics: 最新训练指标快照（仅 RUNNING 时返回）
        - started_at / finished_at / error_message

    若无训练记录，返回 status=idle.

    权限：``rl_agent:read``
    """
    service = get_rl_agent_service()
    try:
        status_info = await service.get_training_status()
    except Exception as e:
        return _handle_service_exception(e, action="查询 RL 训练状态")

    return success(
        data=status_info.to_dict(),
        message=f"训练状态: {status_info.status}",
    )


# 端点 5: POST /training/start —— 启动训练


@router.post(
    "/training/start",
    dependencies=[Depends(require_permission("rl_agent:write"))],
)
async def start_training(request: TrainingStartRequestModel):
    """启动 RL 训练 Workflow.

    流程：
        1. Pydantic 自动校验 max_steps / algorithm / optimization_target
        2. 构造契约层 ``TrainingStartRequest``
        3. 调用 ``RLAgentService.start_training()`` —— 当前版本显式拒绝：
           训练循环未接线（全仓库无 OfflineEnvironment 实现），
           不创建假 RUNNING 记录（学术诚信要求）

    权限：``rl_agent:write``

    工程约束
    --------
        - 若已有 RUNNING 训练（历史遗留记录），抛 ``TrainingAlreadyRunningError``
        - 训练循环接入前本端点恒返回错误：不伪造"训练已启动"
        - 物理执行需"持证操作员 + 导师签字 + 保险"，本端点不涉及
    """
    # 前置校验：algorithm 合法性
    if not PolicyAlgorithm.is_valid(request.algorithm):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"algorithm 不支持: {request.algorithm}（支持: {PolicyAlgorithm.all()}）",
        )
    # 前置校验：optimization_target 合法性
    if not OptimizationTarget.is_valid(request.optimization_target):
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=f"optimization_target 不支持: {request.optimization_target}（支持: {OptimizationTarget.all()}）",
        )

    # 构造契约层 dataclass
    try:
        contract_req = TrainingStartRequest(
            max_steps=request.max_steps,
            seed=request.seed,
            algorithm=request.algorithm,
            optimization_target=request.optimization_target,
        )
    except ValueError as e:
        return error(
            code=ErrorCode.INVALID_REQUEST,
            message=str(e),
        )

    # W9.1 主权硬挂点：自主等级/用户设置要求训练需确认时，拒绝自动启动。
    # 这是后端真实行为，不再只是前端 localStorage 开关。
    sovereignty = get_sovereignty_policy().evaluate_action("train")
    if sovereignty.requires_confirmation:
        return error(
            code=ErrorCode.FORBIDDEN,
            message=f"主权策略要求训练动作需人工确认，已拒绝自动启动：{sovereignty.reason}",
            suggestion=(
                f"请在「设置 → AI 主权」调整自主等级（当前 {sovereignty.autonomy_level}，"
                f"{sovereignty.autonomy_label}），或确认 require_confirmation_for_train 后重试"
            ),
        )

    service = get_rl_agent_service()
    try:
        status_info = await service.start_training(contract_req)
    except Exception as e:
        return _handle_service_exception(e, action="启动 RL 训练")

    return success(
        data=status_info.to_dict(),
        message=(f"RL 训练已启动: algorithm={request.algorithm}，max_steps={request.max_steps}"),
    )


# 端点 6: POST /training/stop —— 停止训练


@router.post(
    "/training/stop",
    dependencies=[Depends(require_permission("rl_agent:write"))],
)
async def stop_training():
    """停止当前 RL 训练.

    流程：
        1. 查找 RUNNING 记录 → 置为 STOPPING
        2. 后台 worker 检测到 STOPPING 后保存 checkpoint 并退出
        3. 训练完成后状态变为 COMPLETED 或 FAILED

    若无 RUNNING 训练，返回当前状态（不报错）.

    权限：``rl_agent:write``
    """
    service = get_rl_agent_service()
    try:
        status_info = await service.stop_training()
    except Exception as e:
        return _handle_service_exception(e, action="停止 RL 训练")

    if status_info.status == TrainingStatus.IDLE:
        return success(
            data=status_info.to_dict(),
            message="当前无运行中的训练任务",
        )

    return success(
        data=status_info.to_dict(),
        message=f"已发送停止请求，训练状态: {status_info.status}",
    )


__all__ = ["router"]
