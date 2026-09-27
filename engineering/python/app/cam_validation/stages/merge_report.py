"""阶段 7 CAM 校验流水线 - 报告合并与导出阶段（P1-3 拆分自原 pipeline.py）。

本模块提供 ``MergeReportMixin``，封装工程师审核 + 确认 + 报告导出逻辑：

- ``review_task``：工程师审核单个特征校验结果（VALIDATED → REVIEWED）
- ``confirm_task``：确认任务（REVIEWED → SUCCEEDED）+ 导出双 JSON
    - {task_id}.cam_report.json：链路最终产物，含双层校验结论 +
      工程师审核记录 + 工业硬门槛告知
    - {task_id}.internal_report.json：内部预校验详细报告，供前端可视化
- ``_export_cam_report``：导出 CAM 校验报告 JSON
- ``_build_cam_software_report_dict``：从 task 反推 CAM 软件校验报告字典
- ``_export_internal_report``：导出内部预校验详细报告 JSON

依赖 ``CamValidationPipeline`` 实例的以下属性（由 ``__init__`` 初始化）：
``_cfg`` / ``_store``

跨 mixin 调用：
- ``self._build_result``：来自 ``PreCheckMixin``（构造 CamValidationResult）

线程安全（项目记忆硬约束）：
- 审核操作使用独立的 _review_lock 防止并发审核冲突
- 导出操作使用 _export_lock 防止文件写入竞争
- CAM 软件调用使用 _cam_call_lock 防止 NX/PowerMill 并发实例崩溃

工业硬约束（项目记忆）：
- SUCCEEDED 状态禁止删除（cam_report.json 是链路最终产物，供审计追溯）
- 阶段 7 产物终止于「CAM 校验报告 JSON」，不触及物理机床
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from collections.abc import Callable

from app.cam_validation.cam_store import (
    CamReviewStatus,
    CamValidationError,
    CamValidationPipelineError,
    CamValidationTask,
    CamValidationTaskStatus,
    FeatureValidationResult,
    ReviewError,
)

from ._common import (
    _DEFAULT_MODE,
    _DEFAULT_STOCK_LENGTH_MM,
    _DEFAULT_STOCK_WIDTH_MM,
    logger,
)


class MergeReportMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _build_result: Callable[..., Any]
    _store: Any

    """报告合并与导出阶段 mixin：工程师审核 + 确认 + 报告导出。

    封装 review_task + confirm_task + _export_cam_report +
    _build_cam_software_report_dict + _export_internal_report。

    依赖 ``CamValidationPipeline`` 实例的以下属性（由 ``__init__`` 初始化）：
    ``_cfg`` / ``_store``

    跨 mixin 调用：
    - ``self._build_result``：来自 ``PreCheckMixin``

    线程安全（项目记忆硬约束）：
        - 审核操作使用 _review_lock 防止并发审核冲突
        - 导出操作使用 _export_lock 防止文件写入竞争
    """

    # 工程师审核

    def review_task(
        self,
        task_id: str,
        feature_id: str,
        review_status: str,
        reviewed_by: str = "engineer",
        edited_params: dict[str, Any] | None = None,
        engineer_notes: str = "",
    ) -> FeatureValidationResult:
        """工程师审核单个特征校验结果（VALIDATED → REVIEWED）。

        单轮审核，与阶段 5/6 一致：
            - confirmed：工程师确认该特征双层校验结论
            - rejected：工程师拒绝该特征（需阶段 6 重新生成 G 代码）
            - edited：工程师编辑校验参数（如调整 safe_z / 切换后端）

        全部特征审核完毕后，任务自动转为 REVIEWED。

        Args:
            task_id: 任务 ID
            feature_id: 特征 ID
            review_status: 审核状态（confirmed / rejected / edited）
            reviewed_by: 审核人
            edited_params: 编辑后的参数（仅 review_status=edited 时使用）
            engineer_notes: 工程师备注（写入 FeatureValidationResult.edited_params）

        Returns:
            审核后的 FeatureValidationResult

        Raises:
            ReviewError: 任务不存在 / 状态不允许审核 / 特征不存在 / 审核字段非法
        """
        try:
            task = self._store.get_task(task_id)
        except CamValidationError as e:
            raise ReviewError(str(e)) from e

        if task.status != CamValidationTaskStatus.VALIDATED.value:
            raise ReviewError(f"任务状态不允许审核: {task.status}（仅 validated 可审核）")

        # 校验 review_status
        valid_statuses = {
            CamReviewStatus.CONFIRMED.value,
            CamReviewStatus.REJECTED.value,
            CamReviewStatus.EDITED.value,
        }
        if review_status not in valid_statuses:
            raise ReviewError(f"无效审核状态: {review_status}，合法值: {sorted(valid_statuses)}")

        # edited 必须提供 edited_params
        if review_status == CamReviewStatus.EDITED.value:
            if not edited_params:
                raise ReviewError("review_status=edited 时必须提供 edited_params")

        # 加审核锁（防止并发审核冲突）
        with self._store.review_lock:
            # 重新获取任务（可能在等待锁期间状态已变）
            task = self._store.get_task(task_id)
            if task.status != CamValidationTaskStatus.VALIDATED.value:
                raise ReviewError(f"任务状态已变更: {task.status}（并发审核冲突）")

            # 查找特征
            target: FeatureValidationResult | None = None
            for result in task.feature_validation_results:
                if result.feature_id == feature_id:
                    target = result
                    break
            if target is None:
                raise ReviewError(f"特征 ID 不存在: {feature_id}")

            # 应用审核
            target.review_status = review_status
            if review_status == CamReviewStatus.EDITED.value and edited_params:
                target.edited_params = dict(edited_params)
                if engineer_notes:
                    target.edited_params["engineer_notes"] = engineer_notes
            elif engineer_notes:
                # 非 edited 也允许记录备注
                target.edited_params = {"engineer_notes": engineer_notes}

            # 检查是否全部审核完毕 REVIEWED
            all_reviewed = all(
                r.review_status != CamReviewStatus.PENDING.value for r in task.feature_validation_results
            )
            if all_reviewed:
                task.status = CamValidationTaskStatus.REVIEWED.value
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

    # 确认任务 + 导出报告

    def confirm_task(
        self,
        task_id: str,
        reviewer: str = "engineer",
    ):
        """确认任务：REVIEWED → SUCCEEDED + 导出双 JSON。

        导出产物（项目记忆硬约束：两个 JSON 职责不同）：
            - {task_id}.cam_report.json：链路最终产物，含双层校验结论 +
              工程师审核记录 + 工业硬门槛告知
            - {task_id}.internal_report.json：内部预校验详细报告，含
              CollisionReport.events 完整列表 + 特征归因细节，供前端可视化

        SUCCEEDED 后禁止删除（cam_store.delete_task 已实现硬约束）。

        Args:
            task_id: 任务 ID
            reviewer: 审核人

        Returns:
            CamValidationResult（含 cam_report_path / internal_report_path）

        Raises:
            CamValidationPipelineError: 任务不存在 / 状态不允许确认
            ReviewError: 全部特征均被 rejected（无可导出的校验结论）
        """
        # 延迟导入以避免循环依赖（CamValidationResult 在 _common 中定义）

        try:
            task = self._store.get_task(task_id)
        except CamValidationError as e:
            raise CamValidationPipelineError(str(e)) from e

        if task.status != CamValidationTaskStatus.REVIEWED.value:
            raise CamValidationPipelineError(f"任务状态不允许确认: {task.status}（仅 reviewed 可确认）")

        # 检查至少有一个特征非 rejected
        non_rejected = [r for r in task.feature_validation_results if r.review_status != CamReviewStatus.REJECTED.value]
        if not non_rejected:
            raise ReviewError(f"任务 {task_id} 无可导出的校验结论（所有特征均被 rejected）")

        # 加导出锁（防止文件写入竞争）
        with self._store.export_lock:
            task = self._store.get_task(task_id)
            if task.status != CamValidationTaskStatus.REVIEWED.value:
                raise CamValidationPipelineError(f"任务状态已变更: {task.status}（并发确认冲突）")

            # 导出 cam_report.json（最终结论）
            cam_report_path = self._export_cam_report(task, reviewer)

            # 导出 internal_report.json（调试细节）
            internal_report_path = self._export_internal_report(task, reviewer)

            # 状态置为 SUCCEEDED
            task.cam_report_path = cam_report_path
            task.internal_report_path = internal_report_path
            task.completed_at = time.time()
            task.reviewed_by = reviewer
            task.status = CamValidationTaskStatus.SUCCEEDED.value
            self._store.update_task(task)

        logger.info(
            "任务 %s CAM 校验报告导出完成 cam_report=%s internal_report=%s",
            task_id,
            cam_report_path,
            internal_report_path,
        )
        return self._build_result(task)

    # 内部辅助：导出 cam_report.json

    def _export_cam_report(
        self,
        task: CamValidationTask,
        reviewer: str,
    ) -> str:
        """导出 CAM 校验报告 JSON（链路最终产物）。

        文件路径：{workspace_dir}/{task_id}.cam_report.json

        包含：
            - task_id / task_status / exported_at / reviewer
            - source_gcode_report_path / source_gcode_file_path（上游追溯）
            - controller_type / material_name / safe_z / stock_top_z
            - prediction_method / pending_calibration
            - cam_validation_required（始终 True）
            - gcode_total_lines
            - cam_backend_requested / cam_backend_used / cam_backend_fallback_reason
            - cam_software_report（CAM 软件二次校验归一化报告）
            - voxel_simulation_report（体素材料去除仿真摘要，闭环强制层）
            - total_features / passed_features / failed_features
            - feature_validation_results（每条特征的双层校验 + 审核记录）
            - industrial_hard_gates_note（工业硬门槛告知）
        """
        report_path = Path(task.workspace_dir) / f"{task.task_id}.cam_report.json"

        # 从任务警告中提取 CAM 后端降级信息（防御性）
        cam_software_report_dict = self._build_cam_software_report_dict(task)

        export_data = {
            "task_id": task.task_id,
            "task_status": CamValidationTaskStatus.SUCCEEDED.value,
            "exported_at": time.time(),
            "reviewer": reviewer,
            # 上游追溯
            "source_gcode_report_path": task.source_gcode_report_path,
            "source_gcode_file_path": task.source_gcode_file_path,
            # 工艺参数（继承阶段 6）
            "controller_type": task.controller_type,
            "material_name": task.material_name,
            "safe_z": task.safe_z,
            "stock_top_z": task.stock_top_z,
            "prediction_method": task.prediction_method,
            "pending_calibration": task.pending_calibration,
            # 项目记忆硬约束
            "cam_validation_required": True,
            # G 代码统计
            "gcode_total_lines": task.gcode_total_lines,
            # CAM 后端策略
            "cam_backend_requested": task.cam_backend_requested,
            "cam_backend_used": task.cam_backend_used,
            "cam_backend_fallback_reason": task.cam_backend_fallback_reason,
            # CAM 软件二次校验报告
            "cam_software_report": cam_software_report_dict,
            # 体素材料去除仿真摘要（闭环强制层；passed=null 表示未执行，
            # DNC 下发闸门对 null 按"未通过"处理；tool_source 标注仿真
            # 刀具来源：actual=任务提供 / report=阶段6携带 / config_default=默认）
            "voxel_simulation_report": {
                "passed": task.voxel_check_passed,
                "engine": task.voxel_engine,
                "collision_count": task.voxel_collision_count,
                "tool_diameter_mm": task.voxel_tool_diameter_mm,
                "tool_source": task.voxel_tool_source,
            },
            # 双层校验统计
            "total_features": task.total_features,
            "passed_features": task.passed_features,
            "failed_features": task.failed_features,
            # 每条特征的双层校验 + 审核记录
            "feature_validation_results": [r.to_dict() for r in task.feature_validation_results],
            # 审核元数据
            "reviewed_by": task.reviewed_by,
            "reviewed_at": task.reviewed_at,
            # 警告与错误
            "warnings": list(task.warnings),
            "errors": list(task.errors),
            # 工业硬门槛告知
            "industrial_hard_gates_note": (
                "本 CAM 校验报告是阶段 7 链路最终产物，"
                "供审计 / 合规 / 论文引用。"
                "内部预校验（CollisionDetector）是 AABB 包围盒级别快速预筛，"
                "不可替代 CAM 软件二次校验。"
                "G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后方可上机床，"
                "系统绝不直接接口 CNC 控制器，物理机床执行由人工 + 持证操作员完成。"
                "大一独立项目不触及物理机床，阶段 7 产物终止于本 JSON。"
            ),
        }

        try:
            report_path.write_text(
                json.dumps(export_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (OSError, TypeError) as e:
            raise CamValidationPipelineError(f"cam_report.json 写入失败: {e}") from e

        return str(report_path)

    def _build_cam_software_report_dict(
        self,
        task: CamValidationTask,
    ) -> dict[str, Any]:
        """从 task.feature_validation_results 反推 CAM 软件校验报告字典。

        由于 _execute_validation 未将 CamSoftwareReport 完整对象持久化到 task，
        此方法从 feature_validation_results 中提取共享字段（cam_messages /
        cam_backend_used）+ 任务级 cam_check_passed 重建归一化报告字典。

        局限性：
            - 无法恢复 CamSoftwareReport.subprocess_returncode /
              manual_checklist_path 等子后端特有字段
            - status 字段根据 cam_check_passed 推断（pass / fail）
        """
        if not task.feature_validation_results:
            return {
                "status": "skipped",
                "backend_used": task.cam_backend_used,
                "messages": [],
                "collisions": [],
                "degraded": bool(task.cam_backend_fallback_reason),
                "degradation_reason": task.cam_backend_fallback_reason,
                "gcode_file_path": task.source_gcode_file_path,
                "controller_type": task.controller_type,
                "validation_timestamp": "",
                "subprocess_returncode": None,
                "manual_checklist_path": "",
            }

        # 所有特征共享同一个 cam_check_passed 和 cam_messages
        first = task.feature_validation_results[0]
        cam_check_passed = first.cam_check_passed
        # status 推断：manual 后端 manual_pending；其他 pass/fail
        if task.cam_backend_used == "manual":
            status = "manual_pending"
        elif task.cam_backend_used == "internal_only":
            status = "skipped"
        elif cam_check_passed:
            status = "pass"
        else:
            status = "fail"

        return {
            "status": status,
            "backend_used": task.cam_backend_used,
            "messages": list(first.cam_messages),
            "collisions": [],
            "degraded": bool(task.cam_backend_fallback_reason),
            "degradation_reason": task.cam_backend_fallback_reason,
            "gcode_file_path": task.source_gcode_file_path,
            "controller_type": task.controller_type,
            "validation_timestamp": "",
            "subprocess_returncode": None,
            "manual_checklist_path": "",
        }

    # 内部辅助：导出 internal_report.json

    def _export_internal_report(
        self,
        task: CamValidationTask,
        reviewer: str,
    ) -> str:
        """导出内部预校验详细报告 JSON（调试细节，供前端可视化）。

        文件路径：{workspace_dir}/{task_id}.internal_report.json

        包含：
            - task_id / exported_at / reviewer
            - controller_type / safe_z / stock_top_z / stock_dimensions
            - mode（3axis / 5axis）
            - total_segments / segments_checked
            - unattributed_events（归因失败的碰撞事件）
            - feature_results（每条特征的内部预校验结果）
            - warnings（包含归因失败警告 + stock 一致性警告）

        Note:
            由于 _execute_validation 未持久化完整 CollisionReport，
            此报告从 feature_validation_results 中提取 internal 字段重建。
            完整 CollisionReport.events 可在 task.warnings 中追溯。
        """
        report_path = Path(task.workspace_dir) / f"{task.task_id}.internal_report.json"

        # 从 feature_validation_results 提取内部预校验字段
        internal_feature_results = [
            {
                "feature_id": fr.feature_id,
                "feature_type": fr.feature_type,
                "line_range": list(fr.line_range),
                "internal_check_passed": fr.internal_check_passed,
                "internal_events": list(fr.internal_events),
                "spindle_rpm": round(fr.spindle_rpm, 4),
                "axial_depth_mm": round(fr.axial_depth_mm, 4),
                "limit_depth_mm": round(fr.limit_depth_mm, 4),
                "stable": fr.stable,
                "safety_margin_ratio": round(fr.safety_margin_ratio, 4),
                "warning": fr.warning,
            }
            for fr in task.feature_validation_results
        ]

        # 汇总所有 internal_events（用于前端可视化碰撞事件）
        all_internal_events: list[dict[str, Any]] = []
        for fr in task.feature_validation_results:
            all_internal_events.extend(fr.internal_events)

        export_data = {
            "task_id": task.task_id,
            "exported_at": time.time(),
            "reviewer": reviewer,
            # 校验参数
            "controller_type": task.controller_type,
            "safe_z": task.safe_z,
            "stock_top_z": task.stock_top_z,
            "stock_dimensions": [
                _DEFAULT_STOCK_LENGTH_MM,
                _DEFAULT_STOCK_WIDTH_MM,
                task.stock_top_z,  # stock_height = stock_top_z
            ],
            "mode": _DEFAULT_MODE,
            # 统计
            "total_segments": 0,  # 无法从 task 追溯，留 0（实际值见 task.warnings）
            "segments_checked": 0,
            # 归因失败的碰撞事件（无法从 task 追溯，留空列表）
            "unattributed_events": [],
            # 所有碰撞事件汇总
            "all_internal_events": all_internal_events,
            # 每条特征的内部预校验结果
            "feature_results": internal_feature_results,
            # 警告列表
            "warnings": list(task.warnings),
            # 调试说明
            "debug_note": (
                "本报告为阶段 7 内部预校验（CollisionDetector）的调试细节，"
                "供前端可视化碰撞事件 + 刀轨高亮。"
                "完整 CollisionReport 对象未持久化，total_segments / "
                "segments_checked / unattributed_events 字段为 0 / 空列表。"
                "如需完整 CollisionReport，请在 _execute_validation 中持久化。"
            ),
        }

        try:
            report_path.write_text(
                json.dumps(export_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (OSError, TypeError) as e:
            raise CamValidationPipelineError(f"internal_report.json 写入失败: {e}") from e

        return str(report_path)
