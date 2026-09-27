"""ExplainabilityService —— 组合式可解释性服务（精简版）.

从原 ``explainability_service.py``（1423 行）拆分而来。本文件仅保留
``ExplainabilityService`` 单例外壳与 4 个 generate_xxx / 4 个 CRUD 方法，
所有底层逻辑已委托到 5 个独立模块：

- ``_projection.ProjectorCache``        —— PCA/t-SNE/UMAP 降维（线程安全缓存）
- ``_predictor_loader.PredictorLoader`` —— LNNPredictor LRU 缓存加载
- ``_payload_store.PayloadStore``       —— payload JSON 文件 IO
- ``_record_repo.ExplanationRecordRepo``—— 解释记录 ORM 仓储（async DB）
- ``_analytics``（10 个纯函数）         —— 采集 / 构建 / 分析 / 差异

设计原则
--------
- ``__init__`` 仅做组合，所有依赖通过构造函数注入
- ``_get_session`` 继承自 ``BaseSingletonService``，作为 ``SessionFactory``
  传给 ``ExplanationRecordRepo``
- 所有 ``generate_xxx`` 方法遵循 ``collect → build → persist`` 三段式
- 保留原 ``_persist_and_create_record`` 统一收尾逻辑
"""

from __future__ import annotations

import logging
from datetime import datetime
import os
from typing import Any, cast

import numpy as np

from app.config import config
from app.contracts.explainability import (
    ComparisonMismatchError,
    ComparisonType,
    ConfidenceExplanation,
    CounterfactualExplanation,
    ExplainabilityError,
    ExplanationComparison,
    ExplanationLookupError,
    ExplanationRecord,
    ExplanationRequest,
    ExplanationType,
    ExplanationValidationError,
    GateDynamicsExplanation,
    ProjectionError,
    ProjectionMethod,
    SamplingError,
)
from app.database.models.explainability import (
    _gen_comparison_id,
    _gen_explanation_id,
)
from app.services._shared.service_base import BaseSingletonService
from app.services.explainability._analytics import (
    build_confidence_distribution,
    build_hidden_state_explanation,
    build_perturbation_range,
    collect_gate_intermediates,
    collect_hidden_state_intermediates,
    collect_mc_dropout_samples,
    compute_counterfactual_metrics,
    compute_diff,
    compute_gate_anomalies,
    scan_counterfactual_outputs,
)
from app.services.explainability._payload_store import PayloadStore
from app.services.explainability._predictor_loader import PredictorLoader
from app.services.explainability._projection import ProjectorCache
from app.services.explainability._record_repo import ExplanationRecordRepo

logger = logging.getLogger(__name__)


# 单例工厂（向后兼容原模块级 API）


def get_explainability_service() -> "ExplainabilityService":
    """获取 ExplainabilityService 单例（委托给 ``ExplainabilityService.get_instance``）."""
    return ExplainabilityService.get_instance()  # type: ignore[return-value]


def reset_explainability_service() -> None:
    """重置单例（仅供测试，委托给 ``ExplainabilityService.reset_instance``）."""
    ExplainabilityService.reset_instance()


# 服务实现


class ExplainabilityService(BaseSingletonService):
    """可解释性服务：实现 ``IExplainabilityService`` 接口.

    内部组合 5 个模块（降维器缓存 / predictor 加载 / payload 文件 / DB 仓储 /
    纯函数算法），自身仅负责参数校验、流程编排与统一收尾。

    设计原则
    --------
    - 读操作（get/list）无锁
    - 写操作（generate/delete/compare）通过 DB 事务保证原子性
    - payload 文件以 JSON 存盘，数据库只存元数据 + payload_path
    - 降维器序列化复用，确保同一模型多次解释投影空间一致
    - 每个 generate_xxx 拆为 collect / build / persist 三段，便于维护
    """

    def __init__(self) -> None:
        # payload 存储根目录：<output_dir>/explainability/payloads/
        payloads_root = os.path.join(
            os.path.abspath(config.storage.output_dir),
            "explainability",
            "payloads",
        )
        # 降维器存储目录：<output_dir>/explainability/reducers/
        reducers_root = os.path.join(
            os.path.abspath(config.storage.output_dir),
            "explainability",
            "reducers",
        )

        # 组合 5 个底层模块
        self._projector = ProjectorCache(reducers_root)
        self._predictor_loader = PredictorLoader(cache_limit=4)
        self._payload_store = PayloadStore(payloads_root)
        # _get_session 继承自 BaseSingletonService，作为 SessionFactory 传入
        self._record_repo = ExplanationRecordRepo(self._get_session)

    # ── 内部辅助：统一收尾 ────────────────────────────────────────────

    async def _persist_and_create_record(
        self,
        *,
        explanation: Any,
        explanation_type: str,
        model_uri: str,
        source_snapshot_id: str | None,
        request: ExplanationRequest,
        metadata: dict[str, Any],
        created_by: str | None,
    ) -> ExplanationRecord:
        """统一的「持久化 payload + 写入 DB 记录」收尾逻辑.

        所有 generate_xxx 方法在构造好 explanation + request 后调用本方法，
        避免重复样板代码。payload 文件名使用独立的 explanation_id，
        DB 记录的 id 由 ``_record_repo.create_record`` 内部生成。
        """
        record_id = _gen_explanation_id()
        payload_path, payload_size = self._payload_store.persist(explanation.to_payload(), record_id)
        return await self._record_repo.create_record(
            explanation_type=explanation_type,
            model_uri=model_uri,
            source_snapshot_id=source_snapshot_id,
            input_signature=request.input_signature(),
            payload_path=payload_path,
            payload_size_bytes=payload_size,
            metadata=metadata,
            created_by=created_by,
        )

    # IExplainabilityService 实现 —— 4 个 generate_xxx

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
        """生成隐状态投影解释."""
        if not ProjectionMethod.is_valid(projection_method):
            raise ExplanationValidationError(f"projection_method 不合法: {projection_method}")
        if projection_dim not in (2, 3):
            raise ExplanationValidationError(f"projection_dim 必须为 2 或 3，当前: {projection_dim}")
        if max_frames < 1:
            raise ExplanationValidationError(f"max_frames 必须 >= 1，当前: {max_frames}")

        predictor = self._predictor_loader.get(model_uri)
        intermediates, hidden_array = collect_hidden_state_intermediates(predictor, max_frames=max_frames)
        projections = self._projector.project(projection_method, hidden_array, projection_dim, model_uri)
        explanation = build_hidden_state_explanation(
            hidden_array,
            projections,
            projection_method=projection_method,
            projection_dim=projection_dim,
            model_uri=model_uri,
        )

        request = ExplanationRequest(
            explanation_type=ExplanationType.HIDDEN_STATE,
            model_uri=model_uri,
            source_snapshot_id=source_snapshot_id,
            options={
                "projection_method": projection_method,
                "projection_dim": projection_dim,
                "max_frames": max_frames,
            },
            created_by=created_by,
        )
        return await self._persist_and_create_record(
            explanation=explanation,
            explanation_type=ExplanationType.HIDDEN_STATE,
            model_uri=model_uri,
            source_snapshot_id=source_snapshot_id,
            request=request,
            metadata={
                "projection_method": projection_method,
                "projection_dim": projection_dim,
                "sample_count": len(hidden_array),
                "hidden_dim": int(hidden_array.shape[1]),
                "capture_mode": intermediates.get("capture_mode", "disabled"),
            },
            created_by=created_by,
        )

    async def generate_gate_dynamics_explanation(
        self,
        model_uri: str,
        *,
        source_snapshot_id: str | None = None,
        anomaly_sigma: float = 2.0,
        created_by: str | None = None,
    ) -> ExplanationRecord:
        """生成门控动力学解释."""
        if anomaly_sigma <= 0:
            raise ExplanationValidationError(f"anomaly_sigma 必须为正数，当前: {anomaly_sigma}")

        predictor = self._predictor_loader.get(model_uri)
        intermediates, gate_values_raw, time_constants_raw = collect_gate_intermediates(predictor)
        gate_array = np.asarray(gate_values_raw, dtype=np.float32)
        mean_gate_per_feature, anomaly_frames = compute_gate_anomalies(gate_array, anomaly_sigma)

        explanation = GateDynamicsExplanation(
            frame_ids=list(range(len(gate_values_raw))),
            gate_values=gate_values_raw,
            time_constants=time_constants_raw,
            mean_gate_per_feature=mean_gate_per_feature,
            anomaly_frames=anomaly_frames,
            model_uri=model_uri,
        )

        request = ExplanationRequest(
            explanation_type=ExplanationType.GATE_DYNAMICS,
            model_uri=model_uri,
            source_snapshot_id=source_snapshot_id,
            options={"anomaly_sigma": anomaly_sigma},
            created_by=created_by,
        )
        return await self._persist_and_create_record(
            explanation=explanation,
            explanation_type=ExplanationType.GATE_DYNAMICS,
            model_uri=model_uri,
            source_snapshot_id=source_snapshot_id,
            request=request,
            metadata={
                "anomaly_sigma": anomaly_sigma,
                "frame_count": len(gate_values_raw),
                "anomaly_frame_count": len(anomaly_frames),
                "capture_mode": intermediates.get("capture_mode", "disabled"),
            },
            created_by=created_by,
        )

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
        """生成反事实解释."""
        if not base_input:
            raise ExplanationValidationError("base_input 不能为空")
        if not perturbed_feature:
            raise ExplanationValidationError("perturbed_feature 不能为空")
        if perturbed_feature not in base_input:
            raise ExplanationValidationError(f"perturbed_feature '{perturbed_feature}' 不在 base_input 中")
        if perturbation_step <= 0:
            raise ExplanationValidationError(f"perturbation_step 必须为正数，当前: {perturbation_step}")

        perturbation_range = build_perturbation_range(perturbation_range, perturbation_step)

        predictor = self._predictor_loader.get(model_uri)
        base_value = float(base_input[perturbed_feature])
        outputs = scan_counterfactual_outputs(predictor, base_input, perturbed_feature, base_value, perturbation_range)
        sensitivity, critical_points = compute_counterfactual_metrics(outputs, perturbation_range)

        explanation = CounterfactualExplanation(
            base_input=dict(base_input),
            perturbed_feature=perturbed_feature,
            perturbation_range=list(perturbation_range),
            outputs=outputs,
            sensitivity=sensitivity,
            critical_points=critical_points,
            model_uri=model_uri,
        )

        request = ExplanationRequest(
            explanation_type=ExplanationType.COUNTERFACTUAL,
            model_uri=model_uri,
            source_snapshot_id=source_snapshot_id,
            input_data={
                "base_input": base_input,
                "perturbed_feature": perturbed_feature,
            },
            options={
                "perturbation_step": perturbation_step,
                "perturbation_range": perturbation_range,
            },
            created_by=created_by,
        )
        return await self._persist_and_create_record(
            explanation=explanation,
            explanation_type=ExplanationType.COUNTERFACTUAL,
            model_uri=model_uri,
            source_snapshot_id=source_snapshot_id,
            request=request,
            metadata={
                "perturbed_feature": perturbed_feature,
                "sensitivity": sensitivity,
                "critical_point_count": len(critical_points),
                "perturbation_point_count": len(perturbation_range),
            },
            created_by=created_by,
        )

    async def generate_confidence_explanation(
        self,
        model_uri: str,
        *,
        input_data: dict[str, Any],
        sample_count: int = 30,
        source_snapshot_id: str | None = None,
        created_by: str | None = None,
    ) -> ExplanationRecord:
        """生成置信度分布解释（MC dropout 采样）."""
        if not input_data:
            raise ExplanationValidationError("input_data 不能为空")
        if sample_count <= 0:
            raise ExplanationValidationError(f"sample_count 必须为正数，当前: {sample_count}")
        if sample_count > 200:
            raise ExplanationValidationError(f"sample_count 上限 200，当前: {sample_count}")

        predictor = self._predictor_loader.get(model_uri)

        # 构造输入向量
        try:
            input_vector = np.array(list(input_data.values()), dtype=np.float32).reshape(1, -1)
        except (ValueError, TypeError) as exc:
            raise ExplanationValidationError(f"input_data 无法转换为数值向量: {exc}") from exc

        mc_mean, mc_std = collect_mc_dropout_samples(predictor, input_vector, sample_count)
        percentiles, histogram = build_confidence_distribution(mc_mean, mc_std, sample_count)

        # 认知不确定性 = std（可由数据降低）
        epistemic = mc_std
        # 偶然不确定性：v1 简化为 0（需要多次输入扰动估计，留待 v2）
        aleatoric = 0.0
        # 异常分数
        anomaly_score = mc_std / (abs(mc_mean) + 1e-8)

        explanation = ConfidenceExplanation(
            sample_count=sample_count,
            mean=mc_mean,
            std=mc_std,
            percentiles=percentiles,
            histogram=histogram,
            epistemic=epistemic,
            aleatoric=aleatoric,
            anomaly_score=anomaly_score,
            model_uri=model_uri,
        )

        request = ExplanationRequest(
            explanation_type=ExplanationType.CONFIDENCE,
            model_uri=model_uri,
            source_snapshot_id=source_snapshot_id,
            input_data=input_data,
            options={"sample_count": sample_count},
            created_by=created_by,
        )
        return await self._persist_and_create_record(
            explanation=explanation,
            explanation_type=ExplanationType.CONFIDENCE,
            model_uri=model_uri,
            source_snapshot_id=source_snapshot_id,
            request=request,
            metadata={
                "sample_count": sample_count,
                "mean": mc_mean,
                "std": mc_std,
                "anomaly_score": anomaly_score,
            },
            created_by=created_by,
        )

    # IExplainabilityService 实现 —— 4 个 CRUD

    async def get_explanation(self, explanation_id: str, *, include_payload: bool = False) -> dict[str, Any]:
        """查询解释结果."""
        record_orm = await self._record_repo.find_record_orm(explanation_id)
        result = record_orm.to_dict()
        if include_payload:
            try:
                result["payload"] = self._payload_store.load(str(record_orm.payload_path))
            except ProjectionError as exc:
                logger.warning(
                    "读取 payload 失败 explanation_id=%s: %s",
                    explanation_id,
                    exc,
                )
                result["payload"] = None
                result["payload_error"] = str(exc)
        return result

    async def list_explanations(
        self,
        *,
        explanation_type: str | None = None,
        model_uri: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ExplanationRecord], int]:
        """列出历史解释记录."""
        return await self._record_repo.list_records(
            explanation_type=explanation_type,
            model_uri=model_uri,
            limit=limit,
            offset=offset,
        )

    async def delete_explanation(self, explanation_id: str) -> bool:
        """删除解释记录（同时删除 payload 文件）."""
        record_orm = await self._record_repo.find_record_orm(explanation_id)
        payload_path = str(record_orm.payload_path)
        await self._record_repo.delete_record(record_orm)
        # 删除 payload 文件
        self._payload_store.delete(payload_path)
        return True

    async def compare_explanations(
        self,
        base_explanation_id: str,
        compared_explanation_id: str,
        *,
        comparison_type: str = ComparisonType.SAME_MODEL_DIFF_INPUT,
        created_by: str | None = None,
    ) -> ExplanationComparison:
        """对比两个解释."""
        if not ComparisonType.is_valid(comparison_type):
            raise ExplanationValidationError(f"comparison_type 不合法: {comparison_type}")
        if base_explanation_id == compared_explanation_id:
            raise ExplanationValidationError("base 与 compared 不能为相同解释")

        # 查询两条记录
        base_orm = await self._record_repo.find_record_orm(base_explanation_id)
        compared_orm = await self._record_repo.find_record_orm(compared_explanation_id)

        # 类型一致性校验
        if base_orm.explanation_type != compared_orm.explanation_type:
            raise ComparisonMismatchError(
                f"解释类型不一致: base={base_orm.explanation_type} compared={compared_orm.explanation_type}"
            )

        # 加载 payload
        base_payload = self._payload_store.load(str(base_orm.payload_path))
        compared_payload = self._payload_store.load(str(compared_orm.payload_path))

        # 计算差异 payload
        diff_payload = compute_diff(base_payload, compared_payload, str(base_orm.explanation_type))

        # 持久化差异 payload
        comparison_id = _gen_comparison_id()
        diff_path = self._payload_store.persist_diff(diff_payload, comparison_id)

        # 写入数据库
        comparison_orm = await self._record_repo.create_comparison(
            base_explanation_id=base_explanation_id,
            compared_explanation_id=compared_explanation_id,
            comparison_type=comparison_type,
            diff_payload_path=diff_path,
            created_by=created_by,
        )

        return ExplanationComparison(
            id=str(comparison_orm.id),
            base_explanation_id=str(comparison_orm.base_explanation_id),
            compared_explanation_id=str(comparison_orm.compared_explanation_id),
            comparison_type=str(comparison_orm.comparison_type),
            diff_payload_path=str(comparison_orm.diff_payload_path),
            created_by=str(comparison_orm.created_by) if comparison_orm.created_by else None,
            created_at=cast(datetime, comparison_orm.created_at),  # ORM nullable=False
        )


__all__ = [
    "ExplainabilityService",
    "get_explainability_service",
    "reset_explainability_service",
    # 异常类（re-export 供路由层统一导入，与 project_package_service 风格一致）
    "ExplainabilityError",
    "ExplanationLookupError",
    "ExplanationValidationError",
    "ProjectionError",
    "SamplingError",
    "ComparisonMismatchError",
]
