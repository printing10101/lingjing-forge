"""G 代码生成流水线编排器（阶段 6）。

串联 ChatterReport 加载 → OperationPlan 加载 → GeneratorAdapter 适配 → 工程师审核 → G 代码导出。

执行顺序
========
1. create_task(...)：创建 PENDING 任务（携带阶段 5 ChatterReport 路径 + 阶段 3 OperationPlan 路径）
2. run_pipeline(task_id)：PENDING → RUNNING → GENERATED（或 FAILED）
   a. ChatterReportLoader.load() 加载阶段 5 ChatterReport JSON
   b. load_operation_plan() 反序列化阶段 3 OperationPlan JSON
   c. GeneratorAdapter.adapt() 生成基础 G 代码 + 特征级安全裕度结果
   d. 若 base_result.is_valid == False（含 unstable 特征）→ FAILED（强制回阶段 5）
   e. 否则 → GENERATED（等待工程师审核）
3. review_feature(task_id, feature_id, review_status, edited_params)：
   - 工程师逐条审核 confirmed / rejected / edited
   - edited 仅记录修改意图（不触发重新生成，避免复杂状态机）
   - 全部审核完毕 → REVIEWED
4. confirm_task(task_id, reviewer)：REVIEWED → SUCCEEDED
   - 导出 G 代码文件至 {output_dir}/{task_id}.{ext}（按控制器扩展名）
   - 导出审核记录 JSON 至 {output_dir}/{task_id}.report.json（供阶段 7 CAM 校验读取）
   - SUCCEEDED 后禁止删除（gcode_store.delete_task 已实现硬约束）

工业硬约束（项目记忆）：
- 系统定位「工程师助手」，非「全自动 G 代码生成器」
- 生成的 G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后方可上机
- 系统绝不直接接口 CNC 控制器
- cam_validation_required 始终 True，不可由环境变量关闭
- SAFETY_MARGIN_RATIO=0.8，实际切深超过极限切深 80% 时发出警告
- stable == False 的特征禁止生成 G 代码（强制回阶段 5 降低切深）
- SUCCEEDED 状态禁止删除（阶段 7 CAM 校验可能已引用 G 代码产物）
- HRC52 材料 pending_calibration 时置信度强制降至 0.5（继承阶段 5）

精度继承链：
- 阶段 1 image_to_3d.precision_tier → 阶段 2 → 阶段 3 → 阶段 4 → 阶段 5 → 阶段 6（本模块）
- 本模块不引入新的精度档位，全程继承上游告知
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from app.core.safe_errors import safe_error_message
from app.gcode_generation.chatter_report_loader import (
    ChatterReportLoader,
)
from app.gcode_generation.gcode_disclaimer import (
    GCodeDisclaimer,
    build_gcode_disclaimer,
)
from app.gcode_generation.failure_case_store import (
    FailureCase,
    get_failure_case_store,
)
from app.gcode_generation.gcode_store import (
    ChatterReportLoadError,
    FeatureGCodeResult,
    GCodeGenerationError,
    GCodeGenerationPipelineError,
    GCodeGenerationTask,
    GCodeGenerationTaskStatus,
    GCodeReviewStatus,
    OperationPlanLoadError,
    generate_task_id,
    get_file_extension,
    get_task_store,
)
from app.gcode_generation.generator_adapter import (
    GeneratorAdapter,
    GeneratorAdapterError,
    load_operation_plan,
)
from app.gcode_generation.safety_validator import (
    SafetyValidator,
)

if TYPE_CHECKING:
    from app.config import GCodeGenerationConfig

logger = logging.getLogger(__name__)

__all__ = [
    "GCodeGenerationPipeline",
    "GCodeGenerationResult",
    "GCodeReviewError",
]


# 异常类


class GCodeReviewError(GCodeGenerationError):
    """工程师审核操作失败。"""


# 结果数据类


@dataclass
class GCodeGenerationResult:
    """G 代码生成任务结果摘要，用于 API 响应。"""

    task_id: str
    status: str
    source_chatter_report_path: str
    source_operation_plan_path: str
    controller_type: str
    material_name: str
    total_features: int
    stable_features: int
    unstable_features: int
    pending_calibration: bool
    prediction_method: str
    gcode_file_path: str | None
    gcode_report_path: str | None
    error_message: str | None = None
    disclaimer: GCodeDisclaimer | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "source_chatter_report_path": self.source_chatter_report_path,
            "source_operation_plan_path": self.source_operation_plan_path,
            "controller_type": self.controller_type,
            "material_name": self.material_name,
            "total_features": self.total_features,
            "stable_features": self.stable_features,
            "unstable_features": self.unstable_features,
            "pending_calibration": self.pending_calibration,
            "prediction_method": self.prediction_method,
            "gcode_file_path": self.gcode_file_path,
            "gcode_report_path": self.gcode_report_path,
            "error_message": self.error_message,
            "disclaimer": self.disclaimer.to_dict() if self.disclaimer else None,
        }


# 流水线


class GCodeGenerationPipeline:
    """G 代码生成流水线编排器。

    串联 ChatterReport 加载 → OperationPlan 加载 → GeneratorAdapter 适配
    → 工程师审核 → G 代码导出。
    """

    def __init__(
        self,
        cfg: "GCodeGenerationConfig | None" = None,
        adapter: GeneratorAdapter | None = None,
        safety_validator: SafetyValidator | None = None,
    ) -> None:
        """初始化流水线。

        Args:
            cfg: GCodeGenerationConfig 实例（可为 None，使用默认 output_dir="outputs/gcode"）
            adapter: GeneratorAdapter 实例（默认用 GeneratorAdapter()，便于测试注入）
            safety_validator: SafetyValidator 实例（默认新建；error 级问题阻断导出）
        """
        self._cfg = cfg
        self._store = get_task_store()
        self._adapter = adapter if adapter is not None else GeneratorAdapter()
        self._loader = ChatterReportLoader()
        self._safety_validator = safety_validator if safety_validator is not None else SafetyValidator()

    # 创建任务

    def create_task(
        self,
        source_chatter_report_path: str,
        source_operation_plan_path: str,
        controller_type: str = "fanuc_0i",
        material_name: str = "45#钢",
        program_number: int = 1000,
        safe_z: float = 80.0,
        stock_top_z: float = 50.0,
    ) -> GCodeGenerationTask:
        """创建 G 代码生成任务。

        Args:
            source_chatter_report_path: 阶段 5 ChatterReport JSON 路径
            source_operation_plan_path: 阶段 3 OperationPlan JSON 路径
            controller_type: 目标控制器（fanuc_0i / siemens_840d / heidenhain_tnc / xmachine_xm100）
            material_name: 材料名称（用于 G 代码注释）
            program_number: 程序号（O 号，默认 1000）
            safe_z: 安全 Z 高度 (mm)
            stock_top_z: 毛坯顶面 Z (mm)

        Returns:
            GCodeGenerationTask（状态为 PENDING）

        Raises:
            GCodeGenerationPipelineError: 输入路径非法或 workspace 创建失败
        """
        if not source_chatter_report_path:
            raise GCodeGenerationPipelineError("source_chatter_report_path 不能为空")
        if not source_operation_plan_path:
            raise GCodeGenerationPipelineError("source_operation_plan_path 不能为空")

        task_id = generate_task_id()
        output_dir = self._resolve_output_dir()
        workspace_dir = output_dir / task_id
        try:
            workspace_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise GCodeGenerationPipelineError(f"创建 workspace 失败: {e}") from e

        task = GCodeGenerationTask(
            task_id=task_id,
            source_chatter_report_path=source_chatter_report_path,
            source_operation_plan_path=source_operation_plan_path,
            controller_type=controller_type,
            material_name=material_name,
            program_number=program_number,
            safe_z=safe_z,
            stock_top_z=stock_top_z,
            status=GCodeGenerationTaskStatus.PENDING.value,
            started_at=time.time(),  # 创建时间（list_tasks 排序依据）
            workspace_dir=str(workspace_dir),
        )
        self._store.add_task(task)
        logger.info(
            "创建 G 代码生成任务 task_id=%s controller=%s material=%s chatter_report=%s op_plan=%s",
            task_id,
            controller_type,
            material_name,
            source_chatter_report_path,
            source_operation_plan_path,
        )
        return task

    # 执行流水线（异步）

    async def run_pipeline(self, task_id: str) -> GCodeGenerationResult:
        """异步执行 ChatterReport 加载 + OperationPlan 加载 + GeneratorAdapter 适配。

        Args:
            task_id: 任务 ID

        Returns:
            GCodeGenerationResult

        Raises:
            GCodeGenerationPipelineError: 任务不存在 / 状态不允许执行
        """
        try:
            task = self._store.get_task(task_id)
        except GCodeGenerationError as e:
            raise GCodeGenerationPipelineError(str(e)) from e

        if task.status not in (
            GCodeGenerationTaskStatus.PENDING.value,
            GCodeGenerationTaskStatus.FAILED.value,
        ):
            raise GCodeGenerationPipelineError(f"任务状态不允许执行: {task.status}（仅 pending/failed 可执行）")

        # 标记为 RUNNING（不覆盖 started_at，保留创建时间作为排序依据）
        task.status = GCodeGenerationTaskStatus.RUNNING.value
        task.error_message = ""
        task.errors = []
        task.warnings = []
        # H10 修复：store.update_task 是同步阻塞 I/O，转移到线程池。
        await asyncio.to_thread(self._store.update_task, task)

        try:
            # 1. 加载阶段 5 ChatterReport
            # H10 修复：loader.load 涉及文件 I/O + JSON 解析，转移到线程池。
            report = await asyncio.to_thread(self._loader.load, task.source_chatter_report_path)
            if not report.feature_results:
                raise ChatterReportLoadError(
                    f"阶段 5 ChatterReport feature_results 为空: {task.source_chatter_report_path}"
                )

            # 2. 加载阶段 3 OperationPlan
            operation_plan = await asyncio.to_thread(load_operation_plan, task.source_operation_plan_path)
            # 匹配刀具共识直径（mm）随任务落盘 → report.json → 阶段 7 体素仿真；
            # 上游未携带时保持 None（阶段 7 回退配置默认并如实标注来源）
            task.tool_diameter_mm = operation_plan.tool_diameter_mm

            # 3. 调用 GeneratorAdapter.adapt() 生成基础 G 代码 + 特征级结果
            # H10 修复：adapt 是同步 CPU 密集计算，转移到线程池。
            base_result, feature_gcode_results = await asyncio.to_thread(
                self._adapter.adapt,
                operation_plan=operation_plan,
                chatter_results=report.feature_results,
                controller_type=task.controller_type,
                material_name=task.material_name,
                program_number=task.program_number,
                safe_z=task.safe_z,
                stock_top_z=task.stock_top_z,
            )

            # 4. 检查 is_valid（含 unstable 特征时 base_result.errors 非空）
            if not base_result.is_valid:
                # stable == False 的特征存在 FAILED（强制回阶段 5）
                task.feature_gcode_results = feature_gcode_results
                task.gcode_text = base_result.program_text
                task.warnings = list(base_result.warnings)
                task.errors = list(base_result.errors)
                task.total_features = report.total_features
                task.stable_features = report.stable_features
                task.unstable_features = report.unstable_features
                task.pending_calibration = report.pending_calibration
                task.prediction_method = report.prediction_method
                task.status = GCodeGenerationTaskStatus.FAILED.value
                task.error_message = (
                    f"G 代码生成失败：含 {report.unstable_features} 个不稳定特征，"
                    "禁止导出 G 代码，请回阶段 5 降低切深或主轴转速后重新生成"
                )
                self._store.update_task(task)
                logger.warning(
                    "任务 %s 生成失败（含 unstable 特征）unstable=%d errors=%d",
                    task_id,
                    report.unstable_features,
                    len(task.errors),
                )
                self._record_outcome(
                    task,
                    outcome="failure",
                    source="unstable_features",
                    error_codes=["UNSTABLE_FEATURES"],
                    error_messages=list(base_result.errors),
                    gcode_text=base_result.program_text,
                    total_features=report.total_features,
                    unstable_features=report.unstable_features,
                )
                return self._build_result(task, error_message=task.error_message)

            # 4.5 统一多层安全门禁（SafetyValidator，借鉴 NumCraft）：
            # error 级 阻断导出（强制回上游调整）；warning 级 随任务上报
            safety_report = self._safety_validator.validate_all(
                chatter_results=report.feature_results,
                gcode_text=base_result.program_text,
                safe_z=task.safe_z,
                stock_top_z=task.stock_top_z,
                controller_type=task.controller_type,
            )
            if not safety_report.is_valid:
                task.feature_gcode_results = feature_gcode_results
                task.gcode_text = base_result.program_text
                task.warnings = list(base_result.warnings) + [w.message for w in safety_report.warnings]
                task.errors = [f"{i.code}: {i.message}" for i in safety_report.errors]
                task.status = GCodeGenerationTaskStatus.FAILED.value
                task.error_message = (
                    f"G 代码安全校验未通过（错误码 {safety_report.error_codes}）："
                    f"{safety_report.errors[0].message}。请回阶段 5 调整参数后重新生成。"
                )
                self._store.update_task(task)
                logger.warning("任务 %s 安全校验未通过 codes=%s", task_id, safety_report.error_codes)
                self._record_outcome(
                    task,
                    outcome="failure",
                    source="safety_validator",
                    error_codes=list(safety_report.error_codes),
                    error_messages=[i.message for i in safety_report.errors],
                    gcode_text=base_result.program_text,
                    total_features=report.total_features,
                    unstable_features=report.unstable_features,
                )
                return self._build_result(task, error_message=task.error_message)
            for _sw in safety_report.warnings:
                task.warnings.append(_sw.message)

            # 5. is_valid == True GENERATED
            task.feature_gcode_results = feature_gcode_results
            task.gcode_text = base_result.program_text
            task.warnings = list(base_result.warnings)
            task.errors = []
            task.total_features = report.total_features
            task.stable_features = report.stable_features
            task.unstable_features = report.unstable_features
            task.pending_calibration = report.pending_calibration
            task.prediction_method = report.prediction_method
            task.status = GCodeGenerationTaskStatus.GENERATED.value
            self._store.update_task(task)
            self._record_outcome(
                task,
                outcome="success",
                total_features=report.total_features,
                unstable_features=report.unstable_features,
            )
            self._harvest_success_cases(task)

            logger.info(
                "任务 %s G 代码生成完成 controller=%s total_features=%d stable=%d unstable=%d "
                "total_lines=%d warnings=%d",
                task_id,
                task.controller_type,
                report.total_features,
                report.stable_features,
                report.unstable_features,
                base_result.total_lines,
                len(task.warnings),
            )

            return self._build_result(task)

        except (
            ChatterReportLoadError,
            OperationPlanLoadError,
            GeneratorAdapterError,
            ValueError,
            OSError,
        ) as e:
            safe = safe_error_message(e, context="gcode_generation.run_pipeline")
            task.status = GCodeGenerationTaskStatus.FAILED.value
            task.error_message = safe.get("message", "")
            self._store.update_task(task)
            self._record_outcome(
                task,
                outcome="failure",
                source="pipeline_exception",
                error_codes=[type(e).__name__],
                error_messages=[safe.get("message", "")],
            )
            logger.error(
                "任务 %s 执行失败 error_id=%s message=%s",
                task_id,
                safe.get("error_id"),
                safe.get("message"),
            )
            return self._build_result(task, error_message=safe.get("message"))
        except Exception as e:
            safe = safe_error_message(e, context="gcode_generation.run_pipeline")
            task.status = GCodeGenerationTaskStatus.FAILED.value
            task.error_message = safe.get("message", "")
            self._store.update_task(task)
            self._record_outcome(
                task,
                outcome="failure",
                source="pipeline_exception",
                error_codes=[type(e).__name__],
                error_messages=[safe.get("message", "")],
            )
            logger.error(
                "任务 %s 执行失败（未捕获异常）error_id=%s message=%s",
                task_id,
                safe.get("error_id"),
                safe.get("message"),
            )
            return self._build_result(task, error_message=safe.get("message"))

    # 工程师审核

    def review_feature(
        self,
        task_id: str,
        feature_id: str,
        review_status: str,
        reviewed_by: str = "engineer",
        edited_params: dict[str, Any] | None = None,
        engineer_notes: str = "",
    ) -> FeatureGCodeResult:
        """工程师审核单个特征的 G 代码段。

        Args:
            task_id: 任务 ID
            feature_id: 特征 ID
            review_status: 审核状态 (confirmed / rejected / edited)
            reviewed_by: 审核人
            edited_params: 编辑后的参数（仅 review_status=edited 时使用）
                可编辑字段：axial_depth_mm / limit_depth_mm / stable (bool)
            engineer_notes: 工程师备注（写入 FeatureGCodeResult.edited_params["engineer_notes"]）

        Returns:
            审核后的 FeatureGCodeResult

        Raises:
            GCodeReviewError: 任务不存在 / 状态不允许审核 / 特征不存在 / 审核字段非法

        Note:
            edited 仅记录修改意图，不触发重新生成 G 代码。
            阶段 7 CAM 校验会读取 edited_params 作为工程师修改建议。
        """
        try:
            task = self._store.get_task(task_id)
        except GCodeGenerationError as e:
            raise GCodeReviewError(str(e)) from e

        if task.status != GCodeGenerationTaskStatus.GENERATED.value:
            raise GCodeReviewError(f"任务状态不允许审核: {task.status}（仅 generated 可审核）")

        # 校验 review_status
        valid_statuses = {
            GCodeReviewStatus.CONFIRMED.value,
            GCodeReviewStatus.REJECTED.value,
            GCodeReviewStatus.EDITED.value,
        }
        if review_status not in valid_statuses:
            raise GCodeReviewError(f"无效审核状态: {review_status}，合法值: {sorted(valid_statuses)}")

        # edited 必须提供 edited_params
        if review_status == GCodeReviewStatus.EDITED.value:
            if not edited_params:
                raise GCodeReviewError("review_status=edited 时必须提供 edited_params")

        # 加审核锁（防止并发审核冲突）
        with self._store.review_lock:
            # 重新获取任务（可能在等待锁期间状态已变）
            task = self._store.get_task(task_id)
            if task.status != GCodeGenerationTaskStatus.GENERATED.value:
                raise GCodeReviewError(f"任务状态已变更: {task.status}（并发审核冲突）")

            # 查找特征
            target: FeatureGCodeResult | None = None
            for result in task.feature_gcode_results:
                if result.feature_id == feature_id:
                    target = result
                    break
            if target is None:
                raise GCodeReviewError(f"特征 ID 不存在于 G 代码结果列表中: {feature_id}")

            # 应用审核
            target.review_status = review_status
            if review_status == GCodeReviewStatus.EDITED.value and edited_params:
                # 拷贝一份，避免外部修改
                target.edited_params = dict(edited_params)
                if engineer_notes:
                    target.edited_params["engineer_notes"] = engineer_notes
                # edited 时不重新生成 G 代码（仅记录修改意图）
                # 但同步更新 stable 字段（便于前端展示）
                if "stable" in edited_params:
                    target.stable = bool(edited_params["stable"])
            elif engineer_notes:
                # 非 edited 也允许记录备注（存入 edited_params）
                target.edited_params = {"engineer_notes": engineer_notes}

            # 检查是否全部审核完毕 REVIEWED
            all_reviewed = all(r.review_status != GCodeReviewStatus.PENDING.value for r in task.feature_gcode_results)
            if all_reviewed:
                task.status = GCodeGenerationTaskStatus.REVIEWED.value
                task.reviewed_by = reviewed_by
                task.reviewed_at = time.time()

            self._store.update_task(task)

        logger.info(
            "任务 %s 特征 %s 审核为 %s by %s",
            task_id,
            feature_id,
            review_status,
            reviewed_by,
        )
        return target

    # 确认任务 + 导出

    def confirm_task(
        self,
        task_id: str,
        reviewer: str = "engineer",
    ) -> GCodeGenerationResult:
        """确认任务：REVIEWED → SUCCEEDED + 导出 G 代码文件 + 审核记录 JSON。

        Args:
            task_id: 任务 ID
            reviewer: 审核人

        Returns:
            GCodeGenerationResult（含 gcode_file_path / gcode_report_path）

        Raises:
            GCodeGenerationPipelineError: 任务不存在 / 状态不允许确认
            GCodeReviewError: 无可导出特征（全部 rejected）

        Note:
            SUCCEEDED 后禁止删除（gcode_store.delete_task 已实现硬约束）。
        """
        try:
            task = self._store.get_task(task_id)
        except GCodeGenerationError as e:
            raise GCodeGenerationPipelineError(str(e)) from e

        if task.status != GCodeGenerationTaskStatus.REVIEWED.value:
            raise GCodeGenerationPipelineError(f"任务状态不允许确认: {task.status}（仅 reviewed 可确认）")

        # 仅导出 confirmed + edited 的特征（rejected 排除）
        exportable = [
            r
            for r in task.feature_gcode_results
            if r.review_status
            in (
                GCodeReviewStatus.CONFIRMED.value,
                GCodeReviewStatus.EDITED.value,
            )
        ]
        if not exportable:
            raise GCodeReviewError(f"任务 {task_id} 无可导出的 G 代码段（所有特征均被 rejected）")

        # 加导出锁（防止文件写入竞争）
        with self._store.export_lock:
            task = self._store.get_task(task_id)
            if task.status != GCodeGenerationTaskStatus.REVIEWED.value:
                raise GCodeGenerationPipelineError(f"任务状态已变更: {task.status}（并发确认冲突）")

            # 导出 G 代码文件
            gcode_file_path = self._export_gcode_file(task, exportable)

            # 导出审核记录 JSON
            gcode_report_path = self._export_report_json(task, exportable, reviewer)

            # 状态置为 SUCCEEDED
            task.gcode_file_path = gcode_file_path
            task.gcode_report_path = gcode_report_path
            task.completed_at = time.time()
            task.status = GCodeGenerationTaskStatus.SUCCEEDED.value
            self._store.update_task(task)

        logger.info(
            "任务 %s G 代码导出完成 gcode_file=%s report=%s features=%d",
            task_id,
            gcode_file_path,
            gcode_report_path,
            len(exportable),
        )
        return self._build_result(task)

    # 导出 G 代码（已 SUCCEEDED 任务可直接获取路径）

    def export_gcode(self, task_id: str) -> str:
        """获取已导出的 G 代码文件路径。

        Args:
            task_id: 任务 ID

        Returns:
            G 代码文件绝对路径

        Raises:
            GCodeGenerationPipelineError: 任务不存在 / 未 SUCCEEDED
        """
        try:
            task = self._store.get_task(task_id)
        except GCodeGenerationError as e:
            raise GCodeGenerationPipelineError(str(e)) from e

        if task.status != GCodeGenerationTaskStatus.SUCCEEDED.value:
            raise GCodeGenerationPipelineError(f"任务状态不允许导出: {task.status}（仅 succeeded 可获取文件路径）")
        if not task.gcode_file_path:
            raise GCodeGenerationPipelineError(f"任务 {task_id} gcode_file_path 为空（数据不一致）")
        return task.gcode_file_path

    # 删除任务（委托给 TaskStore，SUCCEEDED 禁删硬约束已实现）

    def delete_task(self, task_id: str) -> None:
        """删除任务。

        SUCCEEDED 状态禁止删除（阶段 7 CAM 校验可能已引用 G 代码产物）。
        其他状态可删（PENDING / RUNNING / GENERATED / REVIEWED / FAILED / TIMEOUT / CANCELLED）。

        Raises:
            ReviewError: SUCCEEDED 禁删
            GCodeGenerationError: 任务不存在
        """
        self._store.delete_task(task_id, allow_delete_succeeded=False)

    # 内部辅助

    def _record_outcome(
        self,
        task: GCodeGenerationTask,
        outcome: str,
        source: str = "",
        error_codes: list[str] | None = None,
        error_messages: list[str] | None = None,
        gcode_text: str = "",
        total_features: int = 0,
        unstable_features: int = 0,
    ) -> None:
        """把运行结果沉淀进失败案例库（Phase 0 自进化管道）。

        绝不影响主流程：案例库任何异常只记 warning，不向上抛。
        """
        try:
            case = FailureCase(
                task_id=task.task_id,
                outcome=outcome,
                source=source,
                controller_type=task.controller_type,
                material_name=task.material_name,
                error_codes=error_codes or [],
                error_messages=error_messages or [],
                gcode_text=gcode_text,
                total_features=total_features,
                unstable_features=unstable_features,
            )
            get_failure_case_store().record(case)
        except Exception as e:  # noqa: BLE001 - 案例库失败绝不阻断生成主流程
            logger.warning("失败案例库记录失败（不影响主流程）: %s", e)

    def _harvest_success_cases(self, task: GCodeGenerationTask) -> None:
        """把成功任务的稳定特征实证沉淀入工艺四元组库（Phase 0 · M4a）。

        只收 stable 特征，映射为 ProcessQuadruple（source=generated_validated，
        confidence=0.9）写入 RAG 工艺索引，供 recommend_process 检索复用。
        RAG 依赖在方法内延迟导入，保持 pipeline 导入轻量并便于测试隔离。
        绝不影响主流程：任何异常只记 warning，不向上抛。
        """
        try:
            from app.gcode_generation.success_case_harvester import (
                build_quadruples_from_success,
                ingest_success_cases,
            )
            from app.rag.process_quadruple import get_process_quadruple_index

            quads = build_quadruples_from_success(
                task_id=task.task_id,
                controller_type=task.controller_type,
                feature_results=task.feature_gcode_results or [],
            )
            if not quads:
                return
            added, skipped = ingest_success_cases(quads, get_process_quadruple_index())
            if added or skipped:
                logger.info(
                    "任务 %s 成功案例入工艺库：新增 %d 条，去重跳过 %d 条",
                    task.task_id,
                    added,
                    skipped,
                )
        except Exception as e:  # noqa: BLE001 - 采集失败绝不阻断生成主流程
            logger.warning("成功案例入工艺库失败（不影响主流程）: %s", e)

    def _resolve_output_dir(self) -> Path:
        """解析输出目录。cfg 为 None 时使用默认 outputs/gcode。"""
        if self._cfg is not None and hasattr(self._cfg, "output_dir"):
            return Path(self._cfg.output_dir)
        return Path("outputs/gcode")

    def _build_result(
        self,
        task: GCodeGenerationTask,
        error_message: str | None = None,
    ) -> GCodeGenerationResult:
        """构造任务结果摘要（含 disclaimer）。"""
        disclaimer = self._build_disclaimer(task, gcode_file_exported=bool(task.gcode_file_path))
        return GCodeGenerationResult(
            task_id=task.task_id,
            status=task.status,
            source_chatter_report_path=task.source_chatter_report_path,
            source_operation_plan_path=task.source_operation_plan_path,
            controller_type=task.controller_type,
            material_name=task.material_name,
            total_features=task.total_features,
            stable_features=task.stable_features,
            unstable_features=task.unstable_features,
            pending_calibration=task.pending_calibration,
            prediction_method=task.prediction_method,
            gcode_file_path=task.gcode_file_path or None,
            gcode_report_path=task.gcode_report_path or None,
            error_message=error_message,
            disclaimer=disclaimer,
        )

    def _build_disclaimer(
        self,
        task: GCodeGenerationTask,
        gcode_file_exported: bool,
    ) -> GCodeDisclaimer:
        """构造精度告知。

        项目记忆硬约束：requires_cam_validation 始终 True，不可由参数关闭。
        """
        # HRC52 材料校准状态（继承阶段 5 ChatterReport）
        material_calibration_status = "pending_calibration" if task.pending_calibration else "calibrated"
        # LTC 实验性路径：prediction_method 包含 neural_network 时为 True
        ltc_experiment_used = task.prediction_method in ("neural_network", "mixed")
        # 精度档位（继承上游，本模块不引入新档位）
        precision_tier = (
            getattr(self._cfg, "precision_tier", "mesh_calibrated") if self._cfg is not None else "mesh_calibrated"
        )

        return build_gcode_disclaimer(
            precision_tier=precision_tier,
            controller_type=task.controller_type,
            material_name=task.material_name,
            material_calibration_status=material_calibration_status,
            chatter_report_source=task.source_chatter_report_path,
            operation_plan_source=task.source_operation_plan_path,
            prediction_method=task.prediction_method or "analytical",
            total_features=task.total_features,
            stable_features=task.stable_features,
            unstable_features=task.unstable_features,
            pending_calibration=task.pending_calibration,
            ltc_experiment_used=ltc_experiment_used,
            gcode_file_exported=gcode_file_exported,
        )

    def _export_gcode_file(
        self,
        task: GCodeGenerationTask,
        exportable: list[FeatureGCodeResult],
    ) -> str:
        """导出 G 代码文件至 {workspace_dir}/{task_id}.{ext}。

        Args:
            task: 任务
            exportable: 可导出的特征列表（confirmed + edited）

        Returns:
            G 代码文件绝对路径

        Note:
            导出完整 G 代码文本（task.gcode_text），不按特征切分。
            特征级 G 代码段在 report JSON 中单独导出（供阶段 7 参考）。
        """
        ext = get_file_extension(task.controller_type)
        file_name = f"{task.task_id}{ext}"
        file_path = Path(task.workspace_dir) / file_name

        try:
            file_path.write_text(
                task.gcode_text,
                encoding="utf-8",
            )
        except OSError as e:
            raise GCodeGenerationPipelineError(f"G 代码文件写入失败: {e}") from e

        return str(file_path)

    def _export_report_json(
        self,
        task: GCodeGenerationTask,
        exportable: list[FeatureGCodeResult],
        reviewer: str,
    ) -> str:
        """导出审核记录 JSON 至 {workspace_dir}/{task_id}.report.json。

        供阶段 7 CAM 校验读取，包含：
        - task_id / task_status / exported_at / reviewer
        - controller_type / material_name / program_number
        - source_chatter_report_path / source_operation_plan_path
        - prediction_method / pending_calibration
        - cam_validation_required（始终 True）
        - gcode_file_path / gcode_total_lines
        - feature_results（每条特征的 G 代码段 + 审核状态 + edited_params）
        - industrial_hard_gates_note（工业硬门槛告知）
        """
        report_path = Path(task.workspace_dir) / f"{task.task_id}.report.json"

        # 计算总行数
        total_lines = len(task.gcode_text.split("\n")) if task.gcode_text else 0

        export_data = {
            "task_id": task.task_id,
            "task_status": GCodeGenerationTaskStatus.SUCCEEDED.value,
            "exported_at": time.time(),
            "reviewer": reviewer,
            "controller_type": task.controller_type,
            "material_name": task.material_name,
            "program_number": task.program_number,
            "safe_z": task.safe_z,
            "stock_top_z": task.stock_top_z,
            "source_chatter_report_path": task.source_chatter_report_path,
            "source_operation_plan_path": task.source_operation_plan_path,
            "prediction_method": task.prediction_method,
            "pending_calibration": task.pending_calibration,
            "cam_validation_required": True,  # 项目记忆硬约束：始终 True
            "gcode_file_path": str(
                Path(task.workspace_dir) / f"{task.task_id}{get_file_extension(task.controller_type)}"
            ),
            "gcode_total_lines": total_lines,
            "total_features": task.total_features,
            "stable_features": task.stable_features,
            "unstable_features": task.unstable_features,
            "feature_results": [r.to_dict() for r in exportable],
            "industrial_hard_gates_note": (
                "本 G 代码仅供阶段 7 CAM 校验参考，"
                "实际加工必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验 + 持证操作员 + 导师签字。"
                "系统绝不直接接口 CNC 控制器，G 代码文件需手动加载到 CAM 软件。"
                "极限切深为理论值，实际加工必须留 20% 安全裕度。"
            ),
        }

        try:
            report_path.write_text(
                json.dumps(export_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (OSError, TypeError) as e:
            raise GCodeGenerationPipelineError(f"审核记录 JSON 写入失败: {e}") from e

        return str(report_path)
