from typing import Union

from pydantic import BaseModel, Field

from app.contracts.task import TaskType

# [P0-17] 公共约束：dict 字段允许的标量值类型
# 用于限制 dict 字段的内容，防止注入任意嵌套结构
_ScalarValue = Union[str, int, float, bool, None]


class KnowledgeAddRequest(BaseModel):
    document: str = Field(..., description="知识文档内容")
    # [P0-17] 限制元数据键值数量与值类型，防止注入任意结构
    metadata: dict[str, _ScalarValue] | None = Field(
        default=None,
        max_length=50,
        description="元数据键值对，最多50项，值仅支持标量",
    )
    doc_id: str | None = Field(default=None, description="文档ID（为空则自动生成）")


class KnowledgeDeleteRequest(BaseModel):
    doc_id: str = Field(..., description="要删除的文档ID")


class KnowledgeQueryRequest(BaseModel):
    query_text: str = Field(..., description="查询文本")
    n_results: int = Field(default=5, description="返回结果数量", ge=1, le=20)


class ProcessPlanRequest(BaseModel):
    user_input: str = Field(..., description="用户需求描述")


class CadQueryRequest(BaseModel):
    material: str = Field(default="", max_length=64, description="材料类型")
    # [P0-17] 限制尺寸参数键值数量与值类型，下游按 float 读取
    dimensions: dict[str, Union[float, int]] | None = Field(
        default=None,
        max_length=20,
        description="尺寸参数键值对，最多20项，值为数值",
    )
    description: str = Field(default="", max_length=2000, description="加工描述")
    script: str = Field(default="", max_length=50000, description="CadQuery脚本")
    output_format: str = Field(default="stl", max_length=10, description="输出格式")


class CreateTaskRequest(BaseModel):
    task_type: TaskType = Field(..., description="任务类型")
    # [P0-17] 限制任务参数键值数量与值类型
    params: dict[str, _ScalarValue] | None = Field(
        default=None,
        max_length=50,
        description="任务参数键值对，最多50项，值仅支持标量",
    )
    timeout: float | None = Field(default=None, description="超时时间(秒)")


class AIStatusResponse(BaseModel):
    mode: str = Field(default="local", description="AI模式")
    available: bool = Field(default=False, description="AI是否可用")
    model: str = Field(default="", description="使用的模型")


class HealthResponse(BaseModel):
    status: str = Field(default="healthy", description="服务状态")
    version: str = Field(default="", description="版本号")
    ai_status: AIStatusResponse | None = Field(default=None, description="AI状态")


class LNNHyperparameters(BaseModel):
    learning_rate: float = Field(
        ...,
        description="学习率",
        gt=0,
        lt=1,
    )
    epochs: int = Field(
        ...,
        description="训练轮数",
        ge=1,
    )
    batch_size: int = Field(
        ...,
        description="批次大小",
        ge=1,
    )
    optimizer: str = Field(
        ...,
        description="优化器类型",
        pattern="^(adam|sgd|rmsprop)$",
    )


class LNNPredictRequest(BaseModel):
    input_data: list[float] = Field(
        ...,
        description="预测输入数据数组",
    )
    model_name: str = Field(
        ...,
        description="要使用的模型名称",
        min_length=1,
    )
    return_confidence: bool = Field(
        default=False,
        description="是否返回预测置信度",
    )


class LNNTrainRequest(BaseModel):
    model_name: str = Field(
        ...,
        description="训练模型的名称",
        min_length=1,
    )
    data_path: str = Field(
        ...,
        description="训练数据集的存储路径",
        min_length=1,
    )
    hyperparameters: LNNHyperparameters = Field(
        ...,
        description="训练超参数集合",
    )
    device: str = Field(
        default="auto",
        description="训练设备 (auto/gpu/cpu)",
        pattern="^(auto|gpu|cuda|cpu)$",
    )


class LNNDevicePreference(BaseModel):
    device: str = Field(
        default="auto",
        description="训练设备偏好 (auto/gpu/cpu)",
        pattern="^(auto|gpu|cuda|cpu)$",
    )
    use_amp: bool = Field(
        default=True,
        description="是否启用混合精度训练",
    )


class LNNModelInfo(BaseModel):
    name: str = Field(..., description="模型名称")
    version: str = Field(..., description="模型版本")
    last_updated: str = Field(..., description="最后更新时间，ISO 8601格式")


class LNNPredictResponse(BaseModel):
    value: float | list[float] = Field(
        ...,
        description="预测结果值",
    )
    confidence: float | None = Field(
        default=None,
        description="预测置信度，范围[0, 1]",
        ge=0,
        le=1,
    )
    inference_time: float = Field(
        ...,
        description="推理耗时，单位毫秒",
    )
    model_info: LNNModelInfo = Field(
        ...,
        description="模型信息",
    )


class LNNTrainMetrics(BaseModel):
    accuracy: float = Field(..., description="准确率", ge=0, le=1)
    loss: float = Field(..., description="损失值", ge=0)
    training_time: float = Field(..., description="训练总耗时，单位秒", ge=0)
    epochs_completed: int = Field(..., description="实际完成的训练轮数", ge=0)


class LNNTrainResponse(BaseModel):
    status: str = Field(
        ...,
        description="训练状态",
        pattern="^(success|failed|in_progress)$",
    )
    message: str = Field(..., description="训练状态描述信息")
    metrics: LNNTrainMetrics | None = Field(
        default=None,
        description="训练指标，仅当status为success时返回",
    )


class LNNQuantizeRequest(BaseModel):
    quantization_type: str = Field(
        ...,
        description="量化类型",
        pattern="^(dynamic|static)$",
    )
    calibration_data_path: str | None = Field(
        default=None,
        description="校准数据集路径（仅静态量化需要）",
    )


class LNNModelSizeResponse(BaseModel):
    original_size_bytes: int = Field(..., description="原始模型大小")
    quantized_size_bytes: int | None = Field(default=None, description="量化模型大小")
    original_size_human: str = Field(..., description="原始模型大小（人类可读）")
    quantized_size_human: str | None = Field(default=None, description="量化模型大小（人类可读）")
    size_reduction_bytes: int | None = Field(default=None, description="减少的大小")
    size_reduction_percent: float | None = Field(default=None, description="减少的百分比")


class AlternativePlan(BaseModel):
    plan_id: str = Field(..., description="备选方案ID")
    # [P0-17] 限制方案参数键值数量与值类型
    parameters: dict[str, _ScalarValue] = Field(
        ...,
        max_length=30,
        description="方案参数配置，最多30项，值仅支持标量",
    )
    expected_outcome: str = Field(..., description="预期效果说明")
    confidence: float = Field(..., description="方案置信度", ge=0, le=1)
    reasoning: str = Field(..., description="推理过程说明")


class LNNPredictResponseExtended(BaseModel):
    value: float | list[float] = Field(..., description="预测结果值")
    confidence: float | None = Field(default=None, description="预测置信度", ge=0, le=1)
    reasoning: str | None = Field(default=None, description="AI推理过程")
    inference_time: float = Field(..., description="推理耗时，单位毫秒")
    model_info: LNNModelInfo = Field(..., description="模型信息")
    alternatives: list[AlternativePlan] | None = Field(default=None, description="备选方案列表")


class LNNTrainDryRunRequest(BaseModel):
    model_name: str = Field(..., description="训练模型的名称", min_length=1)
    data_path: str = Field(..., description="训练数据集的存储路径", min_length=1)
    hyperparameters: LNNHyperparameters = Field(..., description="训练超参数集合")
    device: str = Field(default="auto", description="训练设备", pattern="^(auto|gpu|cuda|cpu)$")


class TrainingPlanSummary(BaseModel):
    estimated_duration_minutes: float = Field(..., description="预估训练时长（分钟）")
    estimated_memory_mb: float = Field(..., description="预估内存占用（MB）")
    estimated_gpu_memory_mb: float | None = Field(default=None, description="预估GPU显存占用（MB）")
    dataset_samples: int = Field(..., description="数据集样本数")
    # [P0-17] 限制训练集/验证集划分比例的键值数量与值类型
    train_val_split: dict[str, Union[float, int]] = Field(
        ...,
        max_length=10,
        description="训练集/验证集划分比例，值为数值",
    )
    potential_risks: list[str] = Field(default=[], description="潜在风险提示")
    recommendations: list[str] = Field(default=[], description="训练建议")


class LNNTrainDryRunResponse(BaseModel):
    is_dry_run: bool = Field(default=True, description="是否为dry_run模式")
    training_plan: TrainingPlanSummary = Field(..., description="训练计划概要")
    confidence: float = Field(..., description="训练成功置信度", ge=0, le=1)
    reasoning: str = Field(..., description="训练计划推理说明")


class AuditLogQueryRequest(BaseModel):
    start_time: int | None = Field(default=None, description="开始时间戳（毫秒）")
    end_time: int | None = Field(default=None, description="结束时间戳（毫秒）")
    ai_module: str | None = Field(default=None, description="AI模块过滤")
    user_decision: str | None = Field(default=None, description="用户决策过滤")
    limit: int = Field(default=100, description="返回数量", ge=1, le=1000)
    offset: int = Field(default=0, description="偏移量", ge=0)


class AuditLogSearchRequest(BaseModel):
    keyword: str = Field(..., description="搜索关键词", min_length=1)
    limit: int = Field(default=50, description="返回数量", ge=1, le=500)


class AuditLogExportRequest(BaseModel):
    format: str = Field(default="json", description="导出格式", pattern="^(json|csv)$")
    start_time: int | None = Field(default=None, description="开始时间戳（毫秒）")
    end_time: int | None = Field(default=None, description="结束时间戳（毫秒）")
    ai_module: str | None = Field(default=None, description="AI模块过滤")


class UserSovereigntySettings(BaseModel):
    ai_autonomy_level: int = Field(
        default=2,
        description="AI自主度等级（0-4）：0=完全手动, 1=建议需确认, 2=推荐（默认）, 3=半自动, 4=全自动",
        ge=0,
        le=4,
    )
    require_confirmation_for_predict: bool = Field(
        default=False,
        description="预测是否需要确认",
    )
    require_confirmation_for_train: bool = Field(
        default=True,
        description="训练是否需要确认",
    )
    show_confidence_indicator: bool = Field(
        default=True,
        description="是否显示置信度指示器",
    )
    show_alternatives: bool = Field(
        default=True,
        description="是否显示备选方案",
    )
    show_reasoning: bool = Field(
        default=True,
        description="是否显示推理过程",
    )


class AgentTokenCreateRequest(BaseModel):
    scopes: list[str] = Field(
        ...,
        description="权限范围集合（R/W/B/N/C/T 的任意组合）",
        min_length=1,
    )
    expires_in: int | None = Field(
        default=None,
        description="Token有效期（秒），None表示永不过期",
        ge=3600,
    )
    paper_only: bool = Field(
        default=True,
        description="是否仅限模拟模式",
    )


class AgentTokenResponse(BaseModel):
    agent_id: str = Field(..., description="Agent ID")
    token: str = Field(..., description="完整Token值（仅创建时显示一次）")
    scopes: list[str] = Field(..., description="权限范围")
    created_at: float = Field(..., description="创建时间戳")
    expires_at: float | None = Field(default=None, description="过期时间戳")
    paper_only: bool = Field(..., description="是否仅限模拟模式")


class AgentTokenListItem(BaseModel):
    agent_id: str = Field(..., description="Agent ID")
    token_prefix: str = Field(..., description="Token前缀（脱敏显示）")
    scopes: list[str] = Field(..., description="权限范围")
    created_at: float = Field(..., description="创建时间戳")
    expires_at: float | None = Field(default=None, description="过期时间戳")
    paper_only: bool = Field(..., description="是否仅限模拟模式")
    is_active: bool = Field(..., description="是否活跃")


class AgentPredictRequest(BaseModel):
    # P2-批次2 修复：model_name 限制为合法标识符（字母/数字/下划线/连字符），
    # 防止注入特殊字符到 registry 查找键、日志、审计记录。
    model_name: str = Field(
        ...,
        description="模型名称",
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    input_data: list[float] = Field(..., description="输入数据")
    return_confidence: bool = Field(default=False, description="是否返回置信度")


class AgentTrainRequest(BaseModel):
    # P2-批次2 修复：与 AgentPredictRequest 一致，限制 model_name 格式。
    model_name: str = Field(
        ...,
        description="模型名称",
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    data_path: str = Field(..., description="训练数据路径", min_length=1)
    hyperparameters: LNNHyperparameters = Field(..., description="超参数")
    device: str = Field(default="auto", description="设备", pattern="^(auto|gpu|cuda|cpu)$")


class AgentExecuteRequest(BaseModel):
    machine_id: str = Field(..., description="机床ID", min_length=1)
    # [P0-17] 限制工艺参数键值数量与值类型
    parameters: dict[str, _ScalarValue] = Field(
        ...,
        max_length=50,
        description="工艺参数键值对，最多50项，值仅支持标量",
    )
    simulate: bool = Field(default=True, description="是否模拟执行")
    # [F-P0-4] 双因子确认 + 机床安全前置校验
    # 实模式（simulate=False 且 LNN_LIVE_EXECUTION_ENABLED=true）必须显式传入
    supervisor_confirmed: bool = Field(
        default=False,
        description="班长双因子确认（实模式必填，Paper-Only 模式可忽略）",
    )
    # [P0-17] 限制机床安全状态字典键值数量
    machine_safety_status: dict[str, bool] | None = Field(
        default=None,
        max_length=20,
        description=(
            "机床安全状态字典（实模式必填），包含："
            "emergency_stop_active / guard_door_closed / "
            "light_curtain_clear / operator_present"
        ),
    )


class AgentPipelineRequest(BaseModel):
    """Agent 管线执行请求"""

    # P2-批次2 修复：pipeline_type 限制为 orchestrator 实际支持的枚举值，
    # 防止未知值导致 _get_pipeline_steps 返回空 steps 列表造成"空成功"误导。
    # 实际支持值参见 app.agent.orchestrator.AgentOrchestrator._get_pipeline_steps。
    pipeline_type: str = Field(
        ...,
        description="管线类型（dxf_to_gcode/process_plan）",
        min_length=1,
        max_length=50,
        pattern=r"^(dxf_to_gcode|process_plan)$",
    )
    # [P0-17] 限制管线输入数据键值数量与值类型，防止注入任意嵌套结构
    # 不同管线类型的输入结构不同，保留 dict 灵活性但约束规模与值类型
    input_data: dict[str, _ScalarValue] = Field(
        ...,
        max_length=50,
        description="管线输入数据，最多50项键值对，值仅支持标量",
    )
    mode: str = Field(
        default="sequential",
        description="执行模式（sequential/conditional）",
        pattern="^(sequential|conditional)$",
    )
    agent_id: str | None = Field(default=None, description="Agent ID（用于审计）")


class AgentAuditLogQueryRequest(BaseModel):
    agent_id: str | None = Field(default=None, description="Agent ID过滤")
    permission_class: str | None = Field(default=None, description="权限类别过滤")
    limit: int = Field(default=100, description="返回数量", ge=1, le=1000)
    offset: int = Field(default=0, description="偏移量", ge=0)


class LNNBatchInferenceRequest(BaseModel):
    model_name: str = Field(..., description="模型名称", min_length=1)
    input_data: list[list[float]] = Field(..., description="批量输入数据")
    batch_size: int = Field(default=32, description="批次大小", ge=1)


class LNNStreamingConfig(BaseModel):
    """流式推理配置（对应 :class:`app.ai.lnn.inference.streaming.StreamingConfig`）。

    借鉴 lingbot-map GCT 思想：关键帧间隔 + 锚点漂移修正 + 轨迹记忆约束 + 窗口化推理。
    所有字段可选，缺省时使用 ``StreamingConfig`` 默认值。
    """

    keyframe_interval: int = Field(default=1, description="关键帧间隔（每 N 帧一个关键帧）", ge=1)
    keyframe_mode: str = Field(
        default="hybrid",
        description="关键帧判定策略：interval / energy / hybrid",
        pattern="^(interval|energy|hybrid)$",
    )
    energy_threshold: float = Field(default=1.5, description="能量关键帧触发阈值（相对能量增益）", gt=0)
    max_cache_pages: int = Field(default=320, description="长期隐状态缓存最大页数（LRU 淘汰）", ge=1)
    anchor_enabled: bool = Field(default=True, description="是否启用锚点漂移修正")
    anchor_update_rate: float = Field(default=0.01, description="锚点 EMA 更新速率", gt=0, lt=1)
    anchor_correction_strength: float = Field(default=0.1, description="锚点漂移修正强度 [0, 1]", ge=0, le=1)
    trajectory_memory_size: int = Field(default=64, description="轨迹记忆窗口大小", ge=1)
    trajectory_correction_strength: float = Field(default=0.1, description="轨迹一致性约束强度 [0, 1]", ge=0, le=1)
    window_size: int | None = Field(default=None, description="窗口化推理窗口大小（None 表示不启用）")
    overlap_keyframes: int = Field(default=2, description="窗口间重叠关键帧数，用于隐状态传递", ge=0)


class LNNStreamPredictRequest(BaseModel):
    """流式长时序推理请求（POST /api/v1/lnn/predict_stream）。

    对每一帧逐次推理，通过关键帧缓存 + 锚点漂移修正保持长时序一致性。
    响应为 NDJSON 流（``application/x-ndjson``），每行一帧的推理结果。
    """

    model_name: str = Field(..., description="模型名称", min_length=1)
    frames: list[list[float]] = Field(
        ...,
        description="帧序列数据，每个内层列表为一帧输入",
    )
    config: LNNStreamingConfig | None = Field(
        default=None,
        description="流式推理配置，缺省时使用默认 StreamingConfig",
    )


class LNNWindowedPredictRequest(BaseModel):
    """窗口化超长序列推理请求（POST /api/v1/lnn/predict_windowed）。

    将超长序列切分为多个窗口，窗口间通过 ``overlap_keyframes`` 传递隐状态，
    避免每次窗口都从零初始化。适用于跨工序连续切削、万帧以上颤振监控等场景。
    响应为一次性 JSON 数组，包含完整序列的推理结果。
    """

    model_name: str = Field(..., description="模型名称", min_length=1)
    frames: list[list[float]] = Field(
        ...,
        description="完整序列数据，每个内层列表为一帧输入",
    )
    window_size: int | None = Field(
        default=None,
        description="窗口大小，缺省时使用 config.window_size",
        ge=1,
    )
    overlap_keyframes: int | None = Field(
        default=None,
        description="窗口间重叠关键帧数，缺省时使用 config.overlap_keyframes",
        ge=0,
    )
    config: LNNStreamingConfig | None = Field(
        default=None,
        description="流式推理配置，缺省时使用默认 StreamingConfig",
    )


class PermissionCheckResult(BaseModel):
    has_permission: bool = Field(..., description="是否拥有权限")
    user_permissions: list[str] = Field(default_factory=list, description="用户拥有的权限列表")


class UserListItem(BaseModel):
    username: str = Field(..., description="用户名")
    role: str = Field(..., description="用户角色")
    is_active: bool = Field(..., description="是否启用")
    created_at: str = Field(..., description="创建时间")
    last_login: str | None = Field(default=None, description="最后登录时间")


class UserListResponse(BaseModel):
    total: int = Field(..., description="用户总数")
    users: list[UserListItem] = Field(default_factory=list, description="用户列表")


class RoleAssignRequest(BaseModel):
    role_code: str = Field(..., description="角色代码", min_length=1)


class UncertaintyResponse(BaseModel):
    prediction: float = Field(..., description="预测结果值")
    uncertainty: float = Field(..., description="预测不确定性度量（标准差）", ge=0)
    confidence: float = Field(..., description="置信度，范围[0, 1]", ge=0, le=1)


class UserStatusRequest(BaseModel):
    is_active: bool = Field(..., description="是否启用用户")
