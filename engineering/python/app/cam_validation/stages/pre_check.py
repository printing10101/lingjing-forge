"""阶段 7 CAM 校验流水线 - 预检查阶段（P1-3 拆分自原 pipeline.py）。

本模块提供 ``PreCheckMixin``，封装任务生命周期管理 + 查询 + disclaimer 构建：

- ``create_task``：创建 PENDING 任务（含 source_gcode_report_path /
   source_gcode_file_path / controller_type / cam_backend）
- ``delete_task``：删除任务（SUCCEEDED 禁删硬约束由 CamTaskStore 实现）
- ``get_task`` / ``list_tasks``：任务查询
- ``_resolve_output_dir``：解析输出目录（cfg 为 None 时使用默认值）
- ``_build_disclaimer``：构造 CAM 校验精度告知（项目记忆硬约束：
   requires_cam_validation 始终 True，warning_message 永远非空）
- ``_build_result``：构造任务结果摘要（含 disclaimer），供 run_pipeline /
   confirm_task 共享

依赖 ``CamValidationPipeline`` 实例的以下属性（由 ``__init__`` 初始化）：
``_cfg`` / ``_store``

跨 mixin 调用：无（本 mixin 的方法仅依赖 self._cfg / self._store）
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.cam_validation.cam_disclaimer import (
    CamDisclaimer,
    build_cam_disclaimer,
)
from app.cam_validation.cam_store import (
    CamValidationError,
    CamValidationPipelineError,
    CamValidationTask,
    CamValidationTaskStatus,
    generate_task_id,
    is_valid_cam_backend,
)

from ._common import CamValidationResult, logger

if TYPE_CHECKING:
    pass


class PreCheckMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _cfg: Any
    _store: Any

    """CAM 校验流水线预检查阶段 mixin。

    封装任务创建 / 删除 / 查询 + disclaimer / result 构建辅助方法。

    依赖 ``CamValidationPipeline`` 实例的以下属性（由 ``__init__`` 初始化）：
    ``_cfg`` / ``_store``

    项目记忆硬约束：
        - cam_validation_required 始终 True，不可由环境变量关闭
        - SUCCEEDED 状态禁止删除（链路最终产物，需保留供审计追溯）
        - 系统绝不直接接口 CNC 控制器
    """

    # 创建任务

    def create_task(
        self,
        source_gcode_report_path: str,
        source_gcode_file_path: str = "",
        controller_type: str = "fanuc_0i",
        material_name: str = "45#钢",
        safe_z: float = 80.0,
        stock_top_z: float = 50.0,
        cam_backend: str = "internal_only",
        tool_diameter_mm: float | None = None,
        tool_type: str = "",
    ) -> CamValidationTask:
        """创建 CAM 校验任务（PENDING）。

        Args:
            source_gcode_report_path: 阶段 6 report.json 路径
            source_gcode_file_path: 阶段 6 G 代码文件路径（留空时从
                report.json 的 gcode_file_path 字段读取）
            controller_type: 目标控制器（fanuc_0i / siemens_840d /
                heidenhain_tnc / xmachine_xm100）
            material_name: 材料名（用于 disclaimer 显示）
            safe_z: 安全 Z 高度（mm，留空则从 report.json 读取）
            stock_top_z: 毛坯顶面 Z（mm，留空则从 report.json 读取）
            cam_backend: CAM 后端（internal_only / pycam / nx_open /
                powermill / manual）
            tool_diameter_mm: 实际装刀直径（mm，可选）。提供时体素仿真
                基于该直径执行；缺省回退配置默认值并向任务写警告，
                提示工程师确认（直径与实际装刀不符时仿真结论无效）。
            tool_type: 实际刀具类型（可选，如 end_mill / ball_end_mill）。

        Returns:
            CamValidationTask（状态为 PENDING）

        Raises:
            CamValidationPipelineError: 输入路径为空 / cam_backend 非法 /
                tool_diameter_mm 非法（非有限正数）/ workspace 创建失败
        """
        if not source_gcode_report_path:
            raise CamValidationPipelineError("source_gcode_report_path 不能为空")
        if not is_valid_cam_backend(cam_backend):
            raise CamValidationPipelineError(
                f"非法 CAM 后端：{cam_backend}，合法值：internal_only / pycam / nx_open / powermill / manual"
            )
        # bool 是 int 子类，True 直调时会伪装成 1.0mm，显式拒绝
        if tool_diameter_mm is not None and (
            isinstance(tool_diameter_mm, bool)
            or not isinstance(tool_diameter_mm, (int, float))
            or not math.isfinite(float(tool_diameter_mm))
            or float(tool_diameter_mm) <= 0
        ):
            raise CamValidationPipelineError(f"非法 tool_diameter_mm：{tool_diameter_mm}，必须是有限正数（mm）")

        # 确定实际使用的 cam_backend（来自 config.default_cam_backend 或入参）
        # 入参优先；若入参为 internal_only 且 config 有指定，使用 config 的值
        requested_backend = cam_backend
        if self._cfg is not None:
            # 允许 config 覆盖默认（仅当入参为默认 internal_only 时）
            if cam_backend == "internal_only":
                requested_backend = getattr(self._cfg, "default_cam_backend", cam_backend)

        task_id = generate_task_id()
        output_dir = self._resolve_output_dir()
        workspace_dir = output_dir / task_id
        try:
            workspace_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise CamValidationPipelineError(f"创建 workspace 失败: {e}") from e

        task = CamValidationTask(
            task_id=task_id,
            source_gcode_report_path=source_gcode_report_path,
            source_gcode_file_path=source_gcode_file_path,
            controller_type=controller_type,
            material_name=material_name,
            safe_z=safe_z,
            stock_top_z=stock_top_z,
            status=CamValidationTaskStatus.PENDING.value,
            cam_backend_requested=requested_backend,
            cam_backend_used=requested_backend,  # 初始等于 requested，校验后可能更新
            workspace_dir=str(workspace_dir),
            started_at=time.time(),  # 创建时间（list_tasks 排序依据）
            cam_validation_required=True,  # 项目记忆硬约束：始终 True
            tool_diameter_mm=float(tool_diameter_mm) if tool_diameter_mm is not None else None,
            tool_type=tool_type,
        )
        self._store.add_task(task)
        logger.info(
            "创建 CAM 校验任务 task_id=%s controller=%s material=%s gcode_report=%s cam_backend=%s",
            task_id,
            controller_type,
            material_name,
            source_gcode_report_path,
            requested_backend,
        )
        return task

    # 删除任务（委托给 CamTaskStore，SUCCEEDED 禁删硬约束已实现）

    def delete_task(self, task_id: str) -> None:
        """删除任务。

        项目记忆硬约束：SUCCEEDED 状态禁止删除。
        其他状态可删（PENDING / RUNNING / VALIDATED / REVIEWED / FAILED /
        TIMEOUT / CANCELLED）。

        Raises:
            ReviewError: SUCCEEDED 禁删
            CamValidationError: 任务不存在
        """
        self._store.delete_task(task_id, allow_delete_succeeded=False)

    # 任务查询

    def get_task(self, task_id: str) -> CamValidationTask:
        """获取任务详情。

        Raises:
            CamValidationPipelineError: 任务不存在
        """
        try:
            return self._store.get_task(task_id)
        except CamValidationError as e:
            raise CamValidationPipelineError(str(e)) from e

    def list_tasks(
        self,
        status_filter: str | None = None,
    ) -> list[CamValidationTask]:
        """列出任务（可选状态过滤，按创建时间倒序）。"""
        return self._store.list_tasks(status_filter=status_filter)

    # 内部辅助：构建结果摘要 + disclaimer（供 SoftwareCheckMixin /
    # MergeReportMixin 共享调用）

    def _build_result(
        self,
        task: CamValidationTask,
        error_message: str | None = None,
    ) -> CamValidationResult:
        """构造任务结果摘要（含 disclaimer）。"""
        disclaimer = self._build_disclaimer(task, cam_report_exported=bool(task.cam_report_path))
        return CamValidationResult(
            task_id=task.task_id,
            status=task.status,
            source_gcode_report_path=task.source_gcode_report_path,
            source_gcode_file_path=task.source_gcode_file_path,
            controller_type=task.controller_type,
            material_name=task.material_name,
            gcode_total_lines=task.gcode_total_lines,
            total_features=task.total_features,
            passed_features=task.passed_features,
            failed_features=task.failed_features,
            pending_calibration=task.pending_calibration,
            prediction_method=task.prediction_method,
            cam_backend_requested=task.cam_backend_requested,
            cam_backend_used=task.cam_backend_used,
            cam_backend_fallback_reason=task.cam_backend_fallback_reason,
            cam_report_path=task.cam_report_path or None,
            internal_report_path=task.internal_report_path or None,
            error_message=error_message,
            disclaimer=disclaimer,
        )

    def _build_disclaimer(
        self,
        task: CamValidationTask,
        cam_report_exported: bool,
    ) -> CamDisclaimer:
        """构造 CAM 校验精度告知。

        项目记忆硬约束：
            - requires_cam_validation 始终 True，不可由参数关闭
            - requires_engineer_review 始终 True
            - warning_message 永远非空
        """
        # HRC52 材料校准状态（继承阶段 5/6）
        material_calibration_status = "pending_calibration" if task.pending_calibration else "calibrated"
        # LTC 实验性路径：prediction_method 包含 neural_network 时为 True
        ltc_experiment_used = task.prediction_method in ("neural_network", "mixed")
        # 精度档位（继承上游，本模块不引入新档位）
        precision_tier = (
            getattr(self._cfg, "precision_tier", "mesh_calibrated") if self._cfg is not None else "mesh_calibrated"
        )

        return build_cam_disclaimer(
            precision_tier=precision_tier,
            controller_type=task.controller_type,
            material_name=task.material_name,
            material_calibration_status=material_calibration_status,
            gcode_report_source=task.source_gcode_report_path,
            gcode_file_source=task.source_gcode_file_path,
            prediction_method=task.prediction_method or "analytical",
            total_features=task.total_features,
            passed_features=task.passed_features,
            failed_features=task.failed_features,
            pending_calibration=task.pending_calibration,
            ltc_experiment_used=ltc_experiment_used,
            cam_backend_used=task.cam_backend_used,
            cam_backend_fallback_reason=task.cam_backend_fallback_reason,
            cam_backend_requested=task.cam_backend_requested,
            cam_report_exported=cam_report_exported,
        )

    # 内部辅助：解析输出目录

    def _resolve_output_dir(self) -> Path:
        """解析输出目录。cfg 为 None 时使用默认 outputs/cam_validation。"""
        if self._cfg is not None and hasattr(self._cfg, "output_dir"):
            return Path(self._cfg.output_dir)
        return Path("outputs/cam_validation")
