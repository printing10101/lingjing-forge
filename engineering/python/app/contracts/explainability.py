"""可解释性可视化契约：定义 LTC 隐状态/门控/反事实/置信度解释的数据结构.

对应 ADR-016（可解释性可视化）。本文件只定义数据结构与接口契约，
实现见：

- ``app/services/explainability_service.py``：服务层（降维投影 / 反事实扫描 /
  MC dropout 采样 / 载荷持久化）
- ``app/api/v1/explainability.py``：路由层（8 个 REST 端点）
- ``app/database/models/explainability.py``：ORM 持久化（2 张表）
- ``app/ai/lnn/inference/predictor.py``：``LNNPredictor.predict_with_intermediates``
  新增方法，非侵入式捕获隐状态与门控值

契约稳定性：Stable（v1.0.0），向后兼容扩展。

设计要点
--------
1. **非侵入式采样**：解释结果优先消费 ``PredictionResult.model_info`` 已有字段，
   不修改主推理路径；仅在需要门控值/隐状态时通过 ``predict_with_intermediates``
   主动快照，确保推理性能不受解释功能拖累。
2. **4 类解释结果**对应 LTC 网络的 4 个可解释维度：
   - 隐状态投影（``hidden_state``）：2D/3D 可视化帧间状态演化
   - 门控动力学（``gate_dynamics``）：LTC dt 门控值与时间常数τ的时序曲线
   - 反事实解释（``counterfactual``）：扰动单输入特征的输出敏感性扫描
   - 置信度分布（``confidence``）：MC dropout 多次采样的认知不确定性分布
3. **降维方法**：PCA（默认，线性、快）/ t-SNE（非线性，≤5000 样本）/ UMAP（可选）。
   降维器序列化到 ``<output_dir>/explainability/reducers/<model_uri>.pkl`` 复用，
   确保同一模型的多次解释使用一致的投影空间。
4. **载荷持久化**：解释结果 payload（含大型数组）以 JSON 文件存盘，
   ``ExplanationRecord.payload_path`` 记录文件路径，数据库只存元数据，
   避免 JSON 数组膨胀数据库。
5. **比较功能**：``ExplanationComparison`` 记录两次解释的差异 payload，
   支持跨模型版本/跨输入的对比分析。
6. **权限模型**：``explainability:read``（查询/列表/对比）、
   ``explainability:write``（生成/删除）。
7. **异常层级**：``ExplainabilityError`` 基类 → ``LookupError`` /
   ``ValueError`` / ``ProjectionError`` / ``SamplingError`` 子类，
   与现有服务层异常风格一致（参考 ``project_package_service.py``）。
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.utils.time import utcnow


# 解释类型常量


class ExplanationType:
    """解释类型常量：对应 LTC 网络的 4 个可解释维度.

    - ``HIDDEN_STATE``：隐状态投影。从 ``PagedHiddenStateCache`` 提取关键帧
      隐向量，降维到 2D/3D 可视化帧间状态演化轨迹。用于理解模型"看到"了什么。
    - ``GATE_DYNAMICS``：门控动力学。LTC 的 ``dt`` 门控值与时间常数 ``τ`` 的
      时序曲线，揭示模型在不同时间步的"记忆更新速率"。用于诊断长时序漂移。
    - ``COUNTERFACTUAL``：反事实解释。扰动单个输入特征（如主轴转速 +5%），
      扫描输出敏感性曲线。用于回答"如果改变 X，结果会如何变化"。
    - ``CONFIDENCE``：置信度分布。MC dropout 多次随机前向采样的输出分布，
      分离认知不确定性（epistemic）与偶然不确定性（aleatoric）。
    """

    HIDDEN_STATE = "hidden_state"
    GATE_DYNAMICS = "gate_dynamics"
    COUNTERFACTUAL = "counterfactual"
    CONFIDENCE = "confidence"

    @classmethod
    def all(cls) -> list[str]:
        """返回所有解释类型."""
        return [
            cls.HIDDEN_STATE,
            cls.GATE_DYNAMICS,
            cls.COUNTERFACTUAL,
            cls.CONFIDENCE,
        ]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """判断解释类型是否合法."""
        return value in cls.all()


class ProjectionMethod:
    """降维方法常量：将高维隐向量投影到 2D/3D 可视化空间.

    - ``PCA``：主成分分析（默认）。线性方法，速度快，保留全局结构，
      适合 >5000 样本场景。降维器可序列化复用。
    - ``TSNE``：t-SNE 非线性降维。保留局部邻域结构，适合发现簇，
      但 ``O(n^2)`` 复杂度限制样本数 ≤5000。
    - ``UMAP``：UMAP 非线性降维。兼顾局部与全局结构，速度优于 t-SNE，
      需可选依赖 ``umap-learn``。
    """

    PCA = "pca"
    TSNE = "tsne"
    UMAP = "umap"

    @classmethod
    def all(cls) -> list[str]:
        """返回所有降维方法."""
        return [cls.PCA, cls.TSNE, cls.UMAP]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """判断降维方法是否合法."""
        return value in cls.all()

    @classmethod
    def default(cls) -> str:
        """返回默认降维方法."""
        return cls.PCA


class ComparisonType:
    """解释对比类型常量."""

    SAME_MODEL_DIFF_INPUT = "same_model_diff_input"  # 同模型不同输入
    DIFF_MODEL_SAME_INPUT = "diff_model_same_input"  # 不同模型同输入
    DIFF_MODEL_DIFF_INPUT = "diff_model_diff_input"  # 不同模型不同输入

    @classmethod
    def all(cls) -> list[str]:
        """返回所有对比类型."""
        return [
            cls.SAME_MODEL_DIFF_INPUT,
            cls.DIFF_MODEL_SAME_INPUT,
            cls.DIFF_MODEL_DIFF_INPUT,
        ]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """判断对比类型是否合法."""
        return value in cls.all()


# 解释结果数据结构


@dataclass
class HiddenStateExplanation:
    """隐状态投影解释结果.

    从 ``PagedHiddenStateCache`` 提取关键帧隐向量，降维到 2D/3D 后的可视化数据。
    前端用此数据绘制散点图（颜色编码关键帧/能量），展示帧间状态演化轨迹。

    Attributes
    ----------
    frame_ids : list[int]
        帧 ID 序列（与 ``projections`` 一一对应）。
    projections : list[list[float]]
        降维后的坐标。每项长度为 ``projection_dim``（2 或 3）。
    energies : list[float]
        每帧信号能量（L2 范数平方均值），用于颜色编码。
    keyframe_flags : list[bool]
        每帧是否为关键帧（来自 ``KeyframeDecision.is_keyframe``）。
    projection_method : str
        实际使用的降维方法（``ProjectionMethod`` 常量）。
    projection_dim : int
        投影维度（2 或 3）。
    hidden_dim : int
        原始隐向量维度（降维前）。
    sample_count : int
        样本（帧）数量。
    model_uri : str
        解释所用模型 URI。
    """

    frame_ids: list[int]
    projections: list[list[float]]
    energies: list[float]
    keyframe_flags: list[bool]
    projection_method: str
    projection_dim: int
    hidden_dim: int
    sample_count: int
    model_uri: str

    def __post_init__(self) -> None:
        if not self.frame_ids:
            raise ValueError("HiddenStateExplanation.frame_ids 不能为空")
        if len(self.frame_ids) != len(self.projections):
            raise ValueError(
                f"frame_ids 长度 ({len(self.frame_ids)}) 与 projections 长度 ({len(self.projections)}) 不一致"
            )
        if len(self.energies) != len(self.frame_ids):
            raise ValueError("energies 长度必须与 frame_ids 一致")
        if len(self.keyframe_flags) != len(self.frame_ids):
            raise ValueError("keyframe_flags 长度必须与 frame_ids 一致")
        if not ProjectionMethod.is_valid(self.projection_method):
            raise ValueError(f"projection_method 不合法: {self.projection_method}")
        if self.projection_dim not in (2, 3):
            raise ValueError(f"projection_dim 必须为 2 或 3，当前: {self.projection_dim}")
        if self.sample_count != len(self.frame_ids):
            raise ValueError(f"sample_count ({self.sample_count}) 与 frame_ids 长度 ({len(self.frame_ids)}) 不一致")

    def to_payload(self) -> dict[str, Any]:
        """序列化为可持久化的 payload 字典."""
        return {
            "explanation_type": ExplanationType.HIDDEN_STATE,
            "frame_ids": self.frame_ids,
            "projections": self.projections,
            "energies": self.energies,
            "keyframe_flags": self.keyframe_flags,
            "projection_method": self.projection_method,
            "projection_dim": self.projection_dim,
            "hidden_dim": self.hidden_dim,
            "sample_count": self.sample_count,
            "model_uri": self.model_uri,
        }


@dataclass
class GateDynamicsExplanation:
    """门控动力学解释结果.

    LTC 的 ``dt`` 门控值与时间常数 ``τ`` 的时序曲线，揭示模型在不同时间步的
    "记忆更新速率"。``dt`` 大表示快速遗忘旧状态，``dt`` 小表示长时间记忆。
    颤振前兆帧通常伴随 ``dt`` 异常增大。

    Attributes
    ----------
    frame_ids : list[int]
        帧 ID 序列。
    gate_values : list[list[float]]
        每帧每特征的门控值（``shape=[N, hidden_dim]``）。
    time_constants : list[list[float]]
        每帧每特征的时间常数 ``τ``（``shape=[N, hidden_dim]``）。
        ``τ = 1 / dt``，单位与帧间隔一致。
    mean_gate_per_feature : list[float]
        每个特征的全局平均门控值（``shape=[hidden_dim]``），用于识别
        哪些特征是"快记忆"哪些是"慢记忆"。
    anomaly_frames : list[int]
        异常帧 ID 列表（门控值超过 ``mean ± 2σ`` 的帧），用于异常诊断。
    model_uri : str
        解释所用模型 URI。
    """

    frame_ids: list[int]
    gate_values: list[list[float]]
    time_constants: list[list[float]]
    mean_gate_per_feature: list[float]
    anomaly_frames: list[int]
    model_uri: str

    def __post_init__(self) -> None:
        if not self.frame_ids:
            raise ValueError("GateDynamicsExplanation.frame_ids 不能为空")
        n = len(self.frame_ids)
        if len(self.gate_values) != n:
            raise ValueError("gate_values 长度必须与 frame_ids 一致")
        if len(self.time_constants) != n:
            raise ValueError("time_constants 长度必须与 frame_ids 一致")

    def to_payload(self) -> dict[str, Any]:
        """序列化为可持久化的 payload 字典."""
        return {
            "explanation_type": ExplanationType.GATE_DYNAMICS,
            "frame_ids": self.frame_ids,
            "gate_values": self.gate_values,
            "time_constants": self.time_constants,
            "mean_gate_per_feature": self.mean_gate_per_feature,
            "anomaly_frames": self.anomaly_frames,
            "model_uri": self.model_uri,
        }


@dataclass
class CounterfactualExplanation:
    """反事实解释结果.

    扰动单个输入特征（如主轴转速 +5%），扫描输出敏感性曲线。
    用于回答"如果改变 X，结果会如何变化"，帮助工程师理解模型的决策依据。

    Attributes
    ----------
    base_input : dict[str, float]
        基准输入（特征名 → 值）。
    perturbed_feature : str
        被扰动的特征名。
    perturbation_range : list[float]
        扰动值序列（如 ``[-10, -5, 0, 5, 10]`` 表示相对基准 ±10% 的 5 个点）。
    outputs : list[float]
        每个扰动点对应的模型输出（颤振概率 / 刀具磨损等）。
    sensitivity : float
        敏感度系数（输出对扰动的一阶导数均值），绝对值越大表示该特征
        对模型决策影响越大。
    critical_points : list[dict[str, Any]]
        临界点列表（输出突变点），每项含 ``perturbation`` / ``output`` /
        ``delta``（与前一点的差分）。
    model_uri : str
        解释所用模型 URI。
    """

    base_input: dict[str, float]
    perturbed_feature: str
    perturbation_range: list[float]
    outputs: list[float]
    sensitivity: float
    critical_points: list[dict[str, Any]]
    model_uri: str

    def __post_init__(self) -> None:
        if not self.base_input:
            raise ValueError("CounterfactualExplanation.base_input 不能为空")
        if not self.perturbed_feature:
            raise ValueError("perturbed_feature 不能为空")
        if len(self.perturbation_range) != len(self.outputs):
            raise ValueError("perturbation_range 长度必须与 outputs 一致")

    def to_payload(self) -> dict[str, Any]:
        """序列化为可持久化的 payload 字典."""
        return {
            "explanation_type": ExplanationType.COUNTERFACTUAL,
            "base_input": self.base_input,
            "perturbed_feature": self.perturbed_feature,
            "perturbation_range": self.perturbation_range,
            "outputs": self.outputs,
            "sensitivity": self.sensitivity,
            "critical_points": self.critical_points,
            "model_uri": self.model_uri,
        }


@dataclass
class ConfidenceExplanation:
    """置信度分布解释结果.

    MC dropout 多次随机前向采样的输出分布，分离认知不确定性（epistemic，
    可通过增加数据降低）与偶然不确定性（aleatoric，数据本身噪声）。

    Attributes
    ----------
    sample_count : int
        MC dropout 采样次数（默认 30）。
    mean : float
        输出均值（最终预测值）。
    std : float
        输出标准差（总不确定性）。
    percentiles : dict[str, float]
        输出分位数（p5/p25/p50/p75/p95），用于箱线图。
    histogram : dict[str, Any]
        直方图数据（``bins`` + ``counts``），用于分布可视化。
    epistemic : float
        认知不确定性（多次采样的标准差，可由数据降低）。
    aleatoric : float
        偶然不确定性（数据噪声估计，无法由数据降低）。
    anomaly_score : float
        异常分数（``std / (|mean| + ε)``），高值表示模型对该输入不确定。
    model_uri : str
        解释所用模型 URI。
    """

    sample_count: int
    mean: float
    std: float
    percentiles: dict[str, float]
    histogram: dict[str, Any]
    epistemic: float
    aleatoric: float
    anomaly_score: float
    model_uri: str

    def __post_init__(self) -> None:
        if self.sample_count <= 0:
            raise ValueError(f"sample_count 必须为正数，当前: {self.sample_count}")
        if self.std < 0:
            raise ValueError(f"std 不能为负数: {self.std}")
        if self.epistemic < 0:
            raise ValueError(f"epistemic 不能为负数: {self.epistemic}")
        if self.aleatoric < 0:
            raise ValueError(f"aleatoric 不能为负数: {self.aleatoric}")

    def to_payload(self) -> dict[str, Any]:
        """序列化为可持久化的 payload 字典."""
        return {
            "explanation_type": ExplanationType.CONFIDENCE,
            "sample_count": self.sample_count,
            "mean": self.mean,
            "std": self.std,
            "percentiles": self.percentiles,
            "histogram": self.histogram,
            "epistemic": self.epistemic,
            "aleatoric": self.aleatoric,
            "anomaly_score": self.anomaly_score,
            "model_uri": self.model_uri,
        }


# 解释请求与记录


@dataclass
class ExplanationRequest:
    """解释生成请求.

    Attributes
    ----------
    explanation_type : str
        解释类型（``ExplanationType`` 常量）。
    model_uri : str
        解释所用模型 URI（如 ``model://LTC-ChatterPredictor/1.0.0``）。
    source_snapshot_id : Optional[str]
        关联的实验快照 ID（来自 ``ExperimentSnapshot``），用于追溯解释对应的
        推理上下文。可为空（手动触发的解释）。
    input_data : Optional[dict[str, Any]]
        输入数据（反事实解释/置信度解释需要）。结构由具体模型定义。
    options : dict[str, Any]
        解释选项，由具体解释类型决定：
        - ``hidden_state``：``projection_method`` / ``projection_dim`` / ``max_frames``
        - ``gate_dynamics``：``anomaly_sigma``（异常检测阈值，默认 2.0）
        - ``counterfactual``：``perturbed_feature`` / ``perturbation_range`` /
          ``perturbation_step``
        - ``confidence``：``sample_count``（MC dropout 采样次数，默认 30）
    created_by : Optional[str]
        创建者 user_id 或 plugin_id。
    """

    explanation_type: str
    model_uri: str
    source_snapshot_id: str | None = None
    input_data: dict[str, Any] | None = None
    options: dict[str, Any] = field(default_factory=dict)
    created_by: str | None = None

    def __post_init__(self) -> None:
        if not ExplanationType.is_valid(self.explanation_type):
            raise ValueError(f"explanation_type 不合法: {self.explanation_type}")
        if not self.model_uri:
            raise ValueError("model_uri 不能为空")

    def input_signature(self) -> str:
        """计算输入签名（用于去重与缓存）.

        Returns
        -------
        str
            输入数据的 sha256 签名（前 16 字符）。相同输入 + 相同模型 +
            相同解释类型可复用历史解释结果。
        """
        sig_source = {
            "explanation_type": self.explanation_type,
            "model_uri": self.model_uri,
            "input_data": self.input_data,
            "options": self.options,
        }
        try:
            raw = json.dumps(sig_source, sort_keys=True, default=str)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"输入签名计算失败: {exc}") from exc
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class ExplanationRecord:
    """解释记录（持久化元数据）.

    数据库表 ``explanation_records`` 的契约投影。payload（含大型数组）以
    JSON 文件存盘，数据库只存元数据与 ``payload_path``。

    Attributes
    ----------
    id : str
        记录 ID（``exp_`` 前缀 + uuid）。
    explanation_type : str
        解释类型。
    model_uri : str
        模型 URI。
    source_snapshot_id : Optional[str]
        关联快照 ID。
    input_signature : str
        输入签名（去重用）。
    payload_path : str
        payload JSON 文件绝对路径。
    payload_size_bytes : int
        payload 文件大小。
    metadata_json : dict[str, Any]
        附加元数据（如降维方法、采样次数、异常帧数等）。
    created_by : Optional[str]
        创建者。
    created_at : datetime
        创建时间。
    expires_at : Optional[datetime]
        过期时间（过期后由清理任务删除 payload 文件）。
    """

    id: str
    explanation_type: str
    model_uri: str
    source_snapshot_id: str | None
    input_signature: str
    payload_path: str
    payload_size_bytes: int
    metadata_json: dict[str, Any] = field(default_factory=dict)
    created_by: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("ExplanationRecord.id 不能为空")
        if not ExplanationType.is_valid(self.explanation_type):
            raise ValueError(f"explanation_type 不合法: {self.explanation_type}")
        if not self.model_uri:
            raise ValueError("model_uri 不能为空")
        if not self.payload_path:
            raise ValueError("payload_path 不能为空")
        if self.payload_size_bytes < 0:
            raise ValueError(f"payload_size_bytes 不能为负数: {self.payload_size_bytes}")

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（API 响应）."""
        return {
            "id": self.id,
            "explanation_type": self.explanation_type,
            "model_uri": self.model_uri,
            "source_snapshot_id": self.source_snapshot_id,
            "input_signature": self.input_signature,
            "payload_path": self.payload_path,
            "payload_size_bytes": self.payload_size_bytes,
            "metadata": self.metadata_json,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


@dataclass
class ExplanationComparison:
    """解释对比记录.

    数据库表 ``explanation_comparisons`` 的契约投影。记录两次解释的差异
    payload，支持跨模型版本/跨输入的对比分析。

    Attributes
    ----------
    id : str
        对比记录 ID（``cmp_`` 前缀 + uuid）。
    base_explanation_id : str
        基准解释记录 ID。
    compared_explanation_id : str
        对比解释记录 ID。
    comparison_type : str
        对比类型（``ComparisonType`` 常量）。
    diff_payload_path : str
        差异 payload JSON 文件路径。
    created_by : Optional[str]
        创建者。
    created_at : datetime
        创建时间。
    """

    id: str
    base_explanation_id: str
    compared_explanation_id: str
    comparison_type: str
    diff_payload_path: str
    created_by: str | None = None
    created_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("ExplanationComparison.id 不能为空")
        if not self.base_explanation_id:
            raise ValueError("base_explanation_id 不能为空")
        if not self.compared_explanation_id:
            raise ValueError("compared_explanation_id 不能为空")
        if self.base_explanation_id == self.compared_explanation_id:
            raise ValueError("base 与 compared 不能相同")
        if not ComparisonType.is_valid(self.comparison_type):
            raise ValueError(f"comparison_type 不合法: {self.comparison_type}")
        if not self.diff_payload_path:
            raise ValueError("diff_payload_path 不能为空")

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（API 响应）."""
        return {
            "id": self.id,
            "base_explanation_id": self.base_explanation_id,
            "compared_explanation_id": self.compared_explanation_id,
            "comparison_type": self.comparison_type,
            "diff_payload_path": self.diff_payload_path,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# 异常层级


class ExplainabilityError(RuntimeError):
    """可解释性服务基类异常."""

    def __init__(self, message: str, *, code: str = "EXPLAINABILITY_ERROR") -> None:
        super().__init__(message)
        self.code = code


class ExplanationLookupError(ExplainabilityError):
    """解释记录未找到."""

    def __init__(self, explanation_id: str) -> None:
        super().__init__(
            f"解释记录不存在: {explanation_id}",
            code="EXPLANATION_NOT_FOUND",
        )
        self.explanation_id = explanation_id


class ExplanationValidationError(ExplainabilityError):
    """解释请求参数校验失败."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="EXPLANATION_VALIDATION_ERROR")


class ProjectionError(ExplainabilityError):
    """降维投影失败（如样本数不足、维度不匹配）."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="PROJECTION_ERROR")


class SamplingError(ExplainabilityError):
    """MC dropout 采样失败."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="SAMPLING_ERROR")


class ComparisonMismatchError(ExplainabilityError):
    """对比的两条解释类型不一致或模型不匹配."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="COMPARISON_MISMATCH")


# 服务接口契约


class IExplainabilityService(ABC):
    """可解释性服务接口契约.

    实现见 ``app/services/explainability_service.py``。单例模式，
    通过 ``get_explainability_service()`` 获取。
    """

    @abstractmethod
    async def generate_hidden_state_explanation(
        self,
        model_uri: str,
        *,
        source_snapshot_id: str | None = None,
        projection_method: str = ProjectionMethod.PCA,
        projection_dim: int = 2,
        max_frames: int = 1000,
        created_by: str | None = None,
    ) -> ExplanationRecord:
        """生成隐状态投影解释.

        Args:
            model_uri: 模型 URI。
            source_snapshot_id: 关联快照 ID（可选）。
            projection_method: 降维方法（``ProjectionMethod`` 常量）。
            projection_dim: 投影维度（2 或 3）。
            max_frames: 最大帧数（超过则均匀采样）。
            created_by: 创建者。

        Returns
        -------
        ExplanationRecord
            解释记录（含 payload_path）。
        """

    @abstractmethod
    async def generate_gate_dynamics_explanation(
        self,
        model_uri: str,
        *,
        source_snapshot_id: str | None = None,
        anomaly_sigma: float = 2.0,
        created_by: str | None = None,
    ) -> ExplanationRecord:
        """生成门控动力学解释.

        Args:
            model_uri: 模型 URI。
            source_snapshot_id: 关联快照 ID（可选）。
            anomaly_sigma: 异常检测阈值（门控值超过 ``mean ± sigma*std`` 的帧）。
            created_by: 创建者。

        Returns
        -------
        ExplanationRecord
            解释记录。
        """

    @abstractmethod
    async def generate_counterfactual_explanation(
        self,
        model_uri: str,
        *,
        base_input: dict[str, float],
        perturbed_feature: str,
        perturbation_range: list[float] | None = None,
        perturbation_step: float = 0.05,
        source_snapshot_id: str | None = None,
        created_by: str | None = None,
    ) -> ExplanationRecord:
        """生成反事实解释.

        Args:
            model_uri: 模型 URI。
            base_input: 基准输入（特征名 → 值）。
            perturbed_feature: 被扰动的特征名。
            perturbation_range: 扰动值序列（如为空则按 ``perturbation_step`` 生成）。
            perturbation_step: 扰动步长（相对基准值的比例，默认 0.05 即 5%）。
            source_snapshot_id: 关联快照 ID（可选）。
            created_by: 创建者。

        Returns
        -------
        ExplanationRecord
            解释记录。
        """

    @abstractmethod
    async def generate_confidence_explanation(
        self,
        model_uri: str,
        *,
        input_data: dict[str, Any],
        sample_count: int = 30,
        source_snapshot_id: str | None = None,
        created_by: str | None = None,
    ) -> ExplanationRecord:
        """生成置信度分布解释（MC dropout 采样）.

        Args:
            model_uri: 模型 URI。
            input_data: 输入数据。
            sample_count: MC dropout 采样次数（默认 30）。
            source_snapshot_id: 关联快照 ID（可选）。
            created_by: 创建者。

        Returns
        -------
        ExplanationRecord
            解释记录。
        """

    @abstractmethod
    async def get_explanation(self, explanation_id: str, *, include_payload: bool = False) -> dict[str, Any]:
        """查询解释结果.

        Args:
            explanation_id: 解释记录 ID。
            include_payload: 是否在返回中包含 payload 内容（默认仅元数据）。

        Returns
        -------
        dict[str, Any]
            解释记录字典（含 payload 时附加 ``payload`` 字段）。

        Raises
        ------
        ExplanationLookupError
            记录不存在。
        """

    @abstractmethod
    async def list_explanations(
        self,
        *,
        explanation_type: str | None = None,
        model_uri: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ExplanationRecord], int]:
        """列出历史解释记录.

        Args:
            explanation_type: 按解释类型过滤（可选）。
            model_uri: 按模型 URI 过滤（可选）。
            limit: 分页大小。
            offset: 分页偏移。

        Returns
        -------
        tuple[list[ExplanationRecord], int]
            (记录列表, 总数)。
        """

    @abstractmethod
    async def delete_explanation(self, explanation_id: str) -> bool:
        """删除解释记录（同时删除 payload 文件）.

        Args:
            explanation_id: 解释记录 ID。

        Returns
        -------
        bool
            True 表示删除成功。

        Raises
        ------
        ExplanationLookupError
            记录不存在。
        """

    @abstractmethod
    async def compare_explanations(
        self,
        base_explanation_id: str,
        compared_explanation_id: str,
        *,
        comparison_type: str = ComparisonType.SAME_MODEL_DIFF_INPUT,
        created_by: str | None = None,
    ) -> ExplanationComparison:
        """对比两个解释.

        Args:
            base_explanation_id: 基准解释 ID。
            compared_explanation_id: 对比解释 ID。
            comparison_type: 对比类型（``ComparisonType`` 常量）。
            created_by: 创建者。

        Returns
        -------
        ExplanationComparison
            对比记录（含 diff_payload_path）。

        Raises
        ------
        ExplanationLookupError
            任一解释记录不存在。
        ComparisonMismatchError
            两条解释类型不一致。
        """
