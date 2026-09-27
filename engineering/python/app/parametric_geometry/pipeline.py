"""参数化几何输出流水线编排器：串联 feature_to_brep → assembly_builder → step_writer。

执行顺序
========
1. 创建任务（PENDING）：从阶段 2 confirmed_features.json 加载 ReviewedFeatureRef 列表
2. 异步触发执行：
   a. 加载 confirmed_features.json → ReviewedFeatureRef 列表
   b. feature_to_brep.convert_features_to_brep() → BrepShape 列表
   c. assembly_builder.build_assembly_plan() → AssemblyPlan
   d. step_writer.write_step_with_fallback() → STEP 文件
   e. 持久化 assembly_plan.json + brep_shapes.json 供工程师审核回溯
   f. 状态置为 STEP_GENERATED（等待工程师审核 STEP 中的特征表达）
3. 工程师审核：逐条 confirmed / rejected / edited
   - 全部审核完毕 → REVIEWED
4. 基于审核结果重新生成最终 STEP → SUCCEEDED

工业硬约束（项目记忆）：
- mesh → 参数化 CAD 自动转换未解决，本模块输出「算法建议 STEP」
- 工程师必须审核每个特征在 STEP 中的表达（confirmed / rejected / edited）
- 即便审核通过，最终 STEP 必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床
- 本系统定位为「工程师助手」，非「全自动生产线」

精度继承链：
- 阶段 1 image_to_3d.precision_tier → 阶段 2 feature_extraction.precision_tier → 阶段 3
- 本模块不引入新的精度档位，全程继承上游告知
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from app.parametric_geometry._review_state_machine import (
    all_features_reviewed,
    can_execute,
    can_finalize,
    can_review,
    is_valid_review_status,
    next_status_after_review,
)
from app.parametric_geometry.assembly_builder import (
    AssemblyPlan,
    build_assembly_plan,
    get_assembly_summary,
)
from app.parametric_geometry.feature_to_brep import (
    FeatureToBrepResult,
    convert_features_to_brep,
)
from app.parametric_geometry.step_disclaimer import (
    StepDisclaimer,
    build_step_disclaimer,
)
from app.parametric_geometry.step_store import (
    ParametricGeometryTask,
    ParametricGeometryTaskStatus,
    ReviewedFeatureRef,
    StepReviewStatus,
    generate_task_id,
    get_task_store,
)
from app.parametric_geometry.step_writer import write_step_with_fallback
from app.core.safe_errors import safe_error_message

if TYPE_CHECKING:
    from app.config import ParametricGeometryConfig

logger = logging.getLogger(__name__)

__all__ = [
    "ParametricGeometryPipeline",
    "ParametricGeometryResult",
    "ParametricGeometryError",
    "StepReviewError",
    "FeaturesLoadError",
]


# 异常类


class ParametricGeometryError(Exception):
    """参数化几何通用异常。"""


class FeaturesLoadError(ParametricGeometryError):
    """阶段 2 confirmed_features.json 加载失败。"""


class StepReviewError(ParametricGeometryError):
    """工程师审核操作失败。"""


# 结果数据类


@dataclass
class ParametricGeometryResult:
    """参数化几何任务结果摘要，用于 API 响应。"""

    task_id: str
    status: str
    source_feature_extraction_task_id: str
    feature_count: int
    brep_shape_count: int
    engine_used: str | None
    step_output_path: str | None
    final_step_path: str | None
    precision_tier: str
    mesh_calibrated: bool
    error_message: str | None = None
    assembly_summary: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "source_feature_extraction_task_id": self.source_feature_extraction_task_id,
            "feature_count": self.feature_count,
            "brep_shape_count": self.brep_shape_count,
            "engine_used": self.engine_used,
            "step_output_path": self.step_output_path,
            "final_step_path": self.final_step_path,
            "precision_tier": self.precision_tier,
            "mesh_calibrated": self.mesh_calibrated,
            "error_message": self.error_message,
            "assembly_summary": self.assembly_summary,
        }


# 流水线


class ParametricGeometryPipeline:
    """参数化几何输出流水线编排器。

    串联 feature_to_brep → assembly_builder → step_writer 三个阶段，
    并管理工程师审核状态机。
    """

    def __init__(self, cfg: "ParametricGeometryConfig") -> None:
        """初始化流水线。

        Args:
            cfg: ParametricGeometryConfig 实例
        """
        self._cfg = cfg
        self._store = get_task_store()

    # 创建任务

    def create_task(
        self,
        source_feature_extraction_task_id: str,
        input_features_path: str,
        precision_tier: str = "standard",
        mesh_calibrated: bool = False,
    ) -> ParametricGeometryTask:
        """创建参数化几何任务。

        Args:
            source_feature_extraction_task_id: 阶段 2 任务 ID（用于追溯）
            input_features_path: 阶段 2 导出的 confirmed_features.json 路径
            precision_tier: 精度档位（继承自阶段 1/2）
            mesh_calibrated: 上游 mesh 是否已标定（继承自阶段 1）

        Returns:
            ParametricGeometryTask（状态为 PENDING）
        """
        task_id = generate_task_id()
        workspace_dir = Path(self._cfg.output_dir) / task_id
        workspace_dir.mkdir(parents=True, exist_ok=True)

        task = ParametricGeometryTask(
            task_id=task_id,
            source_feature_extraction_task_id=source_feature_extraction_task_id,
            input_features_path=input_features_path,
            precision_tier=precision_tier,
            mesh_calibrated=mesh_calibrated,
            workspace_dir=str(workspace_dir),
        )
        self._store.create(task)
        logger.info(
            "创建参数化几何任务 task_id=%s source_fe_task_id=%s precision_tier=%s",
            task_id,
            source_feature_extraction_task_id,
            precision_tier,
        )
        return task

    # 执行流水线（异步）

    async def run_pipeline(self, task_id: str) -> ParametricGeometryResult:
        """异步执行特征→B-rep→装配→STEP 流水线。

        Args:
            task_id: 任务 ID

        Returns:
            ParametricGeometryResult

        Raises:
            ParametricGeometryError: 任务不存在 / 状态不允许执行
        """
        task = self._store.get(task_id)
        if task is None:
            raise ParametricGeometryError(f"任务不存在: {task_id}")

        if not can_execute(task.status):
            raise ParametricGeometryError(f"任务状态不允许执行: {task.status}（仅 pending/failed 可执行）")

        # 标记为 RUNNING
        self._store.update(
            task_id,
            status=ParametricGeometryTaskStatus.RUNNING.value,
            error_message=None,
        )

        try:
            # 1. 加载阶段 2 confirmed_features.json
            features = self._load_input_features(task.input_features_path)
            self._store.update(task_id, input_features=features)

            if not features:
                raise FeaturesLoadError(f"阶段 2 confirmed_features.json 中无任何特征: {task.input_features_path}")

            # 2. 特征 BrepShape
            brep_result = convert_features_to_brep(features)
            if not brep_result.shapes:
                raise ParametricGeometryError(
                    f"特征→B-rep 转换后无可用形状。"
                    f"skipped={len(brep_result.skipped_features)}, "
                    f"errors={len(brep_result.conversion_errors)}"
                )

            # 3. 装配
            assembly_plan = build_assembly_plan(
                brep_result.shapes,
                blank_margin_mm=self._cfg.blank_margin_mm,
            )
            assembly_summary = get_assembly_summary(assembly_plan)

            # 4. 写入 STEP（write_step_with_fallback 自动选择引擎）
            assert task.workspace_dir is not None  # 任务创建时必设工作目录（见 184 行）
            step_path = Path(task.workspace_dir) / f"{task_id}.step"
            write_result = write_step_with_fallback(
                shapes=brep_result.shapes,
                output_path=step_path,
            )

            if not write_result.success:
                raise ParametricGeometryError(f"STEP 写入失败: {write_result.error_message}")

            # 5. 持久化装配信息（用于工程师审核回溯）
            self._persist_assembly_info(task_id, assembly_plan, brep_result)

            # 6. 状态置为 STEP_GENERATED（等待工程师审核）
            self._store.update(
                task_id,
                status=ParametricGeometryTaskStatus.STEP_GENERATED.value,
                step_output_path=write_result.output_path,
                engine_used=write_result.engine_used,
            )

            logger.info(
                "任务 %s STEP 生成完成 engine=%s shapes=%d path=%s",
                task_id,
                write_result.engine_used,
                write_result.shape_count,
                write_result.output_path,
            )

            return ParametricGeometryResult(
                task_id=task_id,
                status=ParametricGeometryTaskStatus.STEP_GENERATED.value,
                source_feature_extraction_task_id=task.source_feature_extraction_task_id,
                feature_count=len(features),
                brep_shape_count=len(brep_result.shapes),
                engine_used=write_result.engine_used,
                step_output_path=write_result.output_path,
                final_step_path=None,
                precision_tier=task.precision_tier,
                mesh_calibrated=task.mesh_calibrated,
                assembly_summary=assembly_summary,
            )

        except Exception as e:
            safe = safe_error_message(e, context="parametric_geometry.run_pipeline")
            self._store.update(
                task_id,
                status=ParametricGeometryTaskStatus.FAILED.value,
                error_message=safe.get("message"),
            )
            logger.error(
                "任务 %s 执行失败 error_id=%s message=%s",
                task_id,
                safe.get("error_id"),
                safe.get("message"),
            )
            return ParametricGeometryResult(
                task_id=task_id,
                status=ParametricGeometryTaskStatus.FAILED.value,
                source_feature_extraction_task_id=task.source_feature_extraction_task_id,
                feature_count=0,
                brep_shape_count=0,
                engine_used=None,
                step_output_path=None,
                final_step_path=None,
                precision_tier=task.precision_tier,
                mesh_calibrated=task.mesh_calibrated,
                error_message=safe.get("message"),
            )

    # 工程师审核

    def review_step_feature(
        self,
        task_id: str,
        feature_id: str,
        review_status: str,
        edited_params: dict[str, Any] | None = None,
        engineer_notes: str | None = None,
        reviewed_by: str = "engineer",
    ) -> ReviewedFeatureRef:
        """工程师审核单个特征在 STEP 中的表达。

        Args:
            task_id: 任务 ID
            feature_id: 阶段 2 特征 ID
            review_status: pending / confirmed / rejected / edited
            edited_params: 仅当 review_status=edited 时填充（覆盖 source_params）
            engineer_notes: 工程师审核备注
            reviewed_by: 审核人

        Returns:
            更新后的 ReviewedFeatureRef

        Raises:
            StepReviewError: 任务不存在 / 状态不允许审核 / 特征不存在
        """
        task = self._store.get(task_id)
        if task is None:
            raise StepReviewError(f"任务不存在: {task_id}")

        if not can_review(task.status):
            raise StepReviewError(f"任务状态不允许审核: {task.status}（仅 step_generated 可审核）")

        # 验证 review_status 合法
        if not is_valid_review_status(review_status):
            valid_statuses = {s.value for s in StepReviewStatus}
            raise StepReviewError(f"非法 review_status: {review_status}（合法值: {valid_statuses}）")

        # 验证 edited 模式必须有 edited_params
        if review_status == StepReviewStatus.EDITED.value and not edited_params:
            raise StepReviewError("review_status=edited 时必须提供 edited_params")

        # 找到对应的 ReviewedFeatureRef
        target: ReviewedFeatureRef | None = None
        for f in task.input_features:
            if f.feature_id == feature_id:
                target = f
                break
        if target is None:
            raise StepReviewError(f"特征不存在: {feature_id}（task_id={task_id}）")

        # 更新审核字段（直接修改对象，update 触发持久化）
        target.review_status = review_status
        target.edited_params = edited_params
        target.engineer_notes = engineer_notes
        target.reviewed_by = reviewed_by
        target.reviewed_at = time.time()

        # 检查是否所有特征都已审核（不再有 pending）
        all_reviewed = all_features_reviewed([f.review_status for f in task.input_features])
        new_status = next_status_after_review(all_reviewed, task.status)

        self._store.update(
            task_id,
            input_features=task.input_features,
            status=new_status,
        )

        logger.info(
            "任务 %s 特征 %s 审核为 %s（all_reviewed=%s）",
            task_id,
            feature_id,
            review_status,
            all_reviewed,
        )
        return target

    # 最终化 STEP（基于审核结果重新生成）

    async def finalize_step(self, task_id: str) -> ParametricGeometryResult:
        """基于工程师审核结果重新生成最终 STEP 文件。

        ReviewedFeatureRef.effective_params() 自动合并 edited_params，
        所以本方法直接调用 convert_features_to_brep 即可获得审核后的形状。
        rejected 的特征会被 convert_features_to_brep 自动跳过。

        Args:
            task_id: 任务 ID

        Returns:
            ParametricGeometryResult

        Raises:
            ParametricGeometryError: 任务不存在 / 状态不允许 / 重新生成失败
        """
        task = self._store.get(task_id)
        if task is None:
            raise ParametricGeometryError(f"任务不存在: {task_id}")

        if not can_finalize(task.status):
            raise ParametricGeometryError(f"任务状态不允许最终化: {task.status}（仅 reviewed 可最终化）")

        try:
            # 1. 基于审核后的 effective_params 重新转换
            # （rejected 特征会被 convert_features_to_brep 跳过）
            brep_result = convert_features_to_brep(task.input_features)
            if not brep_result.shapes:
                raise ParametricGeometryError("审核后无可用形状（可能全部特征被 rejected）")

            # 2. 重新装配
            assembly_plan = build_assembly_plan(
                brep_result.shapes,
                blank_margin_mm=self._cfg.blank_margin_mm,
            )

            # 3. 写入最终 STEP
            assert task.workspace_dir is not None  # 任务创建时必设工作目录
            final_step_path = Path(task.workspace_dir) / f"{task_id}_final.step"
            write_result = write_step_with_fallback(
                shapes=brep_result.shapes,
                output_path=final_step_path,
            )

            if not write_result.success:
                raise ParametricGeometryError(f"最终 STEP 写入失败: {write_result.error_message}")

            # 4. 状态置为 SUCCEEDED
            self._store.update(
                task_id,
                status=ParametricGeometryTaskStatus.SUCCEEDED.value,
                final_step_path=write_result.output_path,
                engine_used=write_result.engine_used,
                error_message=None,
            )

            logger.info(
                "任务 %s 最终 STEP 生成完成 path=%s",
                task_id,
                write_result.output_path,
            )

            return ParametricGeometryResult(
                task_id=task_id,
                status=ParametricGeometryTaskStatus.SUCCEEDED.value,
                source_feature_extraction_task_id=task.source_feature_extraction_task_id,
                feature_count=len(task.input_features),
                brep_shape_count=len(brep_result.shapes),
                engine_used=write_result.engine_used,
                step_output_path=task.step_output_path,
                final_step_path=write_result.output_path,
                precision_tier=task.precision_tier,
                mesh_calibrated=task.mesh_calibrated,
                assembly_summary=get_assembly_summary(assembly_plan),
            )
        except Exception as e:
            safe = safe_error_message(e, context="parametric_geometry.finalize_step")
            self._store.update(
                task_id,
                status=ParametricGeometryTaskStatus.FAILED.value,
                error_message=safe.get("message"),
            )
            logger.error(
                "任务 %s 最终化失败 error_id=%s message=%s",
                task_id,
                safe.get("error_id"),
                safe.get("message"),
            )
            raise ParametricGeometryError(safe.get("message", "未知错误")) from e

    # 取消任务

    def cancel_task(self, task_id: str) -> ParametricGeometryTask:
        """取消任务。

        Args:
            task_id: 任务 ID

        Raises:
            ParametricGeometryError: 任务不存在 / 已终态
        """
        task = self._store.get(task_id)
        if task is None:
            raise ParametricGeometryError(f"任务不存在: {task_id}")

        terminal_states = {
            ParametricGeometryTaskStatus.SUCCEEDED.value,
            ParametricGeometryTaskStatus.FAILED.value,
            ParametricGeometryTaskStatus.CANCELLED.value,
        }
        if task.status in terminal_states:
            raise ParametricGeometryError(f"任务已终态，无法取消: {task.status}")

        self._store.update(
            task_id,
            status=ParametricGeometryTaskStatus.CANCELLED.value,
        )
        logger.info("任务 %s 已取消", task_id)
        updated = self._store.get(task_id)
        assert updated is not None, "刚 update 完任务不应消失"
        return updated

    # 查询

    def get_task(self, task_id: str) -> ParametricGeometryTask | None:
        return self._store.get(task_id)

    def list_tasks(self, limit: int = 50) -> list[ParametricGeometryTask]:
        return self._store.list_tasks(limit)

    def get_result_summary(self, task_id: str) -> ParametricGeometryResult:
        """构造任务结果摘要（用于 API 响应）。"""
        task = self._store.get(task_id)
        if task is None:
            raise ParametricGeometryError(f"任务不存在: {task_id}")

        return ParametricGeometryResult(
            task_id=task.task_id,
            status=task.status,
            source_feature_extraction_task_id=task.source_feature_extraction_task_id,
            feature_count=len(task.input_features),
            brep_shape_count=0,  # 历史任务无法回溯，置 0
            engine_used=task.engine_used,
            step_output_path=task.step_output_path,
            final_step_path=task.final_step_path,
            precision_tier=task.precision_tier,
            mesh_calibrated=task.mesh_calibrated,
            error_message=task.error_message,
        )

    def get_disclaimer(
        self,
        task: ParametricGeometryTask | None = None,
    ) -> StepDisclaimer:
        """构造 step_disclaimer。

        Args:
            task: 任务实例（None 时用默认值，用于 precision_info 端点）
        """
        engine_used = (task.engine_used if task else None) or "unavailable"
        mesh_calibrated = task.mesh_calibrated if task else False
        feature_source = task.source_feature_extraction_task_id if task else "external_upload"
        precision_tier = task.precision_tier if task else "standard"

        return build_step_disclaimer(
            cfg=self._cfg,
            mesh_calibrated=mesh_calibrated,
            feature_source=feature_source,
            precision_tier=precision_tier,
            engine_used=engine_used,
        )

    # 内部工具

    def _load_input_features(self, input_features_path: str) -> list[ReviewedFeatureRef]:
        """加载阶段 2 confirmed_features.json。

        阶段 2 导出的 features 中 params 字段已是 effective_params（包含工程师编辑），
        阶段 3 把它作为 source_params，并把 review_status 重置为 pending
        （阶段 3 是新一轮审核：审核 STEP 中的特征表达，不是审核特征参数本身）。

        Args:
            input_features_path: JSON 文件路径

        Returns:
            ReviewedFeatureRef 列表

        Raises:
            FeaturesLoadError: 文件不存在 / 解析失败
        """
        path = Path(input_features_path)
        if not path.exists():
            raise FeaturesLoadError(f"阶段 2 confirmed_features.json 不存在: {input_features_path}")

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise FeaturesLoadError(f"阶段 2 confirmed_features.json 解析失败: {e}") from e

        features_data = data.get("features", [])
        if not isinstance(features_data, list):
            raise FeaturesLoadError(
                f"confirmed_features.json 中 features 字段不是 list: {type(features_data).__name__}"
            )

        features: list[ReviewedFeatureRef] = []
        for f in features_data:
            if not isinstance(f, dict):
                logger.warning("跳过非法特征记录（非 dict）: %r", f)
                continue
            try:
                features.append(
                    ReviewedFeatureRef(
                        feature_id=f["feature_id"],
                        feature_type=f["feature_type"],
                        source_params=f.get("params", {}),
                        # 阶段 3 重置为 pending，等待工程师新一轮审核
                        review_status=StepReviewStatus.PENDING.value,
                    )
                )
            except KeyError as e:
                logger.warning(
                    "跳过非法特征记录（缺字段 %s）: %r",
                    e,
                    f,
                )

        return features

    def _persist_assembly_info(
        self,
        task_id: str,
        assembly_plan: AssemblyPlan,
        brep_result: FeatureToBrepResult,
    ) -> None:
        """持久化装配信息到任务工作目录（供工程师审核回溯）。

        Args:
            task_id: 任务 ID
            assembly_plan: 装配计划
            brep_result: feature_to_brep 结果
        """
        task = self._store.get(task_id)
        if task is None or not task.workspace_dir:
            return

        try:
            workspace = Path(task.workspace_dir)
            workspace.mkdir(parents=True, exist_ok=True)

            # 装配计划
            assembly_path = workspace / "assembly_plan.json"
            assembly_path.write_text(
                json.dumps(
                    assembly_plan.to_dict(),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            # BrepShape 列表 + 转换日志
            brep_path = workspace / "brep_shapes.json"
            brep_path.write_text(
                json.dumps(
                    {
                        "shapes": [s.to_dict() for s in brep_result.shapes],
                        "skipped_features": brep_result.skipped_features,
                        "conversion_errors": brep_result.conversion_errors,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as e:
            safe = safe_error_message(e, context="parametric_geometry._persist_assembly_info")
            logger.warning(
                "持久化装配信息失败 task_id=%s error_id=%s message=%s",
                task_id,
                safe.get("error_id"),
                safe.get("message"),
            )
