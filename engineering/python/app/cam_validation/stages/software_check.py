"""阶段 7 CAM 校验流水线 - CAM 软件校验阶段（P1-3 拆分自原 pipeline.py）。

本模块提供 ``SoftwareCheckMixin``，封装双层校验的核心执行逻辑：

- ``run_pipeline``：异步执行 G 代码加载 + 内部预校验 + CAM 软件二次校验
    状态转移：PENDING → RUNNING → VALIDATED（或 FAILED / TIMEOUT）
- ``_execute_validation``：执行 G 代码加载 + InternalValidator + CamAdapter
    状态转移：RUNNING → VALIDATED（或 FAILED，由调用方处理异常）
- ``_build_feature_results``：将阶段 6 feature_results（list[dict]）转为
    FeatureValidationResult 列表

依赖 ``CamValidationPipeline`` 实例的以下属性（由 ``__init__`` 初始化）：
``_cfg`` / ``_store`` / ``_loader`` / ``_validator`` / ``_adapter``

跨 mixin 调用：
- ``self._build_result``：来自 ``PreCheckMixin``（构造 CamValidationResult）

线程安全（项目记忆硬约束）：
- 审核操作使用独立的 _review_lock 防止并发审核冲突
- 导出操作使用 _export_lock 防止文件写入竞争
- CAM 软件调用使用 _cam_call_lock 防止 NX/PowerMill 并发实例崩溃
"""

from __future__ import annotations

from typing import Any
from collections.abc import Callable

from app.cam_validation.cam_store import (
    CamReviewStatus,
    CamValidationError,
    CamValidationPipelineError,
    CamValidationTask,
    CamValidationTaskStatus,
    FeatureValidationResult,
)
from app.core.safe_errors import safe_error_message
from app.simulation.kinematics import (
    KinematicsValidator,
    auto_work_offset,
    load_profile,
)

from ._common import (
    _DEFAULT_MODE,
    _DEFAULT_STOCK_LENGTH_MM,
    _DEFAULT_STOCK_WIDTH_MM,
    logger,
)


class SoftwareCheckMixin:
    # 宿主契约：由主类 / 兄弟 mixin 提供
    _build_result: Callable[..., Any]
    _adapter: Any
    _loader: Any
    _store: Any
    _validator: Any
    _voxel_validator: Any

    """CAM 软件校验阶段 mixin：双层校验的核心执行逻辑。

    封装 run_pipeline + _execute_validation + _build_feature_results。

    依赖 ``CamValidationPipeline`` 实例的以下属性（由 ``__init__`` 初始化）：
    ``_cfg`` / ``_store`` / ``_loader`` / ``_validator`` / ``_adapter``

    跨 mixin 调用：
    - ``self._build_result``：来自 ``PreCheckMixin``

    线程安全（项目记忆硬约束）：
        - CAM 软件调用使用 _cam_call_lock 串行化，防止 NX/PowerMill 并发崩溃
    """

    # 执行流水线（异步）

    async def run_pipeline(self, task_id: str):
        """异步执行 G 代码加载 + 内部预校验 + CAM 软件二次校验。

        状态转移：PENDING → RUNNING → VALIDATED（或 FAILED / TIMEOUT）

        Args:
            task_id: 任务 ID

        Returns:
            CamValidationResult

        Raises:
            CamValidationPipelineError: 任务不存在 / 状态不允许执行
        """
        # 延迟导入以避免循环依赖（CamValidationResult 在 _common 中定义）

        try:
            task = self._store.get_task(task_id)
        except CamValidationError as e:
            raise CamValidationPipelineError(str(e)) from e

        if task.status not in (
            CamValidationTaskStatus.PENDING.value,
            CamValidationTaskStatus.FAILED.value,
        ):
            raise CamValidationPipelineError(f"任务状态不允许执行: {task.status}（仅 pending/failed 可重新执行）")

        # 标记为 RUNNING（不覆盖 started_at，保留创建时间作为排序依据）
        task.status = CamValidationTaskStatus.RUNNING.value
        task.error_message = ""
        task.errors = []
        task.warnings = []
        self._store.update_task(task)

        try:
            await self._execute_validation(task)
            # _execute_validation 内部会将状态置为 VALIDATED / FAILED
        except CamValidationError as e:
            # 内部校验抛出的已知异常
            safe = safe_error_message(e, context="cam_validation.run_pipeline")
            task = self._store.get_task(task_id)
            task.status = CamValidationTaskStatus.FAILED.value
            task.error_message = safe.get("message", str(e))
            task.errors.append(safe.get("message", str(e)))
            self._store.update_task(task)
            logger.error(
                "任务 %s 执行失败 error_id=%s message=%s",
                task_id,
                safe.get("error_id"),
                safe.get("message"),
            )
            return self._build_result(task, error_message=safe.get("message"))
        except Exception as e:
            # 未捕获异常兜底
            safe = safe_error_message(e, context="cam_validation.run_pipeline")
            task = self._store.get_task(task_id)
            task.status = CamValidationTaskStatus.FAILED.value
            task.error_message = safe.get("message", str(e))
            task.errors.append(safe.get("message", str(e)))
            self._store.update_task(task)
            logger.error(
                "任务 %s 执行失败（未捕获异常）error_id=%s message=%s",
                task_id,
                safe.get("error_id"),
                safe.get("message"),
            )
            return self._build_result(task, error_message=safe.get("message"))

        # 重新获取任务（_execute_validation 已更新状态）
        task = self._store.get_task(task_id)
        logger.info(
            "任务 %s CAM 校验完成 status=%s total_features=%d passed=%d failed=%d backend_used=%s",
            task_id,
            task.status,
            task.total_features,
            task.passed_features,
            task.failed_features,
            task.cam_backend_used,
        )
        return self._build_result(task)

    # 内部辅助：执行双层校验

    async def _execute_validation(self, task: CamValidationTask) -> None:
        """执行 G 代码加载 + 内部预校验 + CAM 软件二次校验。

        状态转移：RUNNING → VALIDATED（或 FAILED，由调用方处理异常）

        Args:
            task: 任务对象（状态为 RUNNING）

        Raises:
            GCodeReportLoadError: 阶段 6 report.json 加载失败
            InternalValidationError: 内部预校验异常
            CamAdapterError: CAM 软件适配层异常
        """
        # 1. 加载阶段 6 G 代码 + report.json
        load_result = self._loader.load_from_report(task.source_gcode_report_path)

        # 同步 task 字段（从阶段 6 report.json 继承）
        task.source_gcode_file_path = load_result.gcode_file_path
        task.controller_type = load_result.controller_type
        task.material_name = load_result.material_name
        task.safe_z = load_result.safe_z
        task.stock_top_z = load_result.stock_top_z
        task.gcode_total_lines = load_result.gcode_total_lines
        task.pending_calibration = load_result.pending_calibration
        task.prediction_method = load_result.prediction_method
        # 累加加载警告
        if load_result.load_warnings:
            task.warnings.extend(load_result.load_warnings)

        # 2. 将阶段 6 feature_results（list[dict]）转为 FeatureValidationResult
        feature_results = self._build_feature_results(load_result.feature_results)
        task.total_features = len(feature_results)

        # 3. 执行内部预校验（InternalValidator）
        # stock_height = stock_top_z（保证 StockModel 一致性，避免警告）
        stock_height = task.stock_top_z
        collision_report, updated_features = self._validator.validate(
            gcode_text=load_result.gcode_text,
            feature_results=feature_results,
            controller_type=task.controller_type,
            safe_z=task.safe_z,
            stock_top_z=task.stock_top_z,
            stock_length=_DEFAULT_STOCK_LENGTH_MM,
            stock_width=_DEFAULT_STOCK_WIDTH_MM,
            stock_height=stock_height,
            mode=_DEFAULT_MODE,
        )

        # 3.5 程序级运动学校验（行程/主轴/进给/快移扎刀/装刀/程序完整性）。
        # kinematics_check_passed 是 DNC 下发闸门条件之一（与 voxel 同级）；
        # cfg 为 None 的测试注入场景跳过（与 voxel_validator None 同口径）。
        cfg = getattr(self, "_cfg", None)
        if cfg is not None and getattr(cfg, "kinematics_enabled", True):
            kin_profile = load_profile(getattr(cfg, "kinematics_machine_id", "vmc_850"))
            kin_validator = KinematicsValidator(kin_profile)
            kin_report = kin_validator.validate(
                load_result.gcode_text,
                work_offset=auto_work_offset(kin_profile, task.stock_top_z),
                stock_top_z=task.stock_top_z,
            )
            task.kinematics_check_passed = kin_report.passed
            task.kinematics_error_count = kin_report.error_count
            for issue in kin_report.issues:
                if issue.severity != "error":
                    continue
                msg = f"运动学校验 {issue.code}（行 {issue.line_no}）：{issue.message}" + (
                    f" {issue.suggestion}" if issue.suggestion else ""
                )
                if msg not in task.errors:
                    task.errors.append(msg)
            if not kin_report.passed:
                task.warnings.append(
                    f"程序级运动学校验未通过：{kin_report.error_count} 处 error"
                    f"（machine={kin_report.machine_id}）。"
                    f"DNC 下发将被闸门拦截，请修正程序后重新校验。"
                )
            for w in kin_report.warnings:
                if w not in task.warnings:
                    task.warnings.append(w)

        # 4. 体素材料去除仿真校验（闭环强制层，无开关——项目记忆硬约束）
        # 检测两类致命碰撞：切削段过切毛坯底面 / 快速段在安全高度下切入材料。
        # 刀具参数三级来源：任务显式提供（actual）> 阶段 6 report.json 携带
        # （report）> 配置默认值（config_default）；来源随任务/报告如实落盘，
        # 基于默认值的结论向工程师明示"与实际装刀可能不符"。
        # voxel_validator 为 None 仅出现在 cfg=None 的测试注入场景。
        if self._voxel_validator is not None:
            if task.tool_diameter_mm is not None:
                voxel_tool_diameter = task.tool_diameter_mm
                voxel_tool_source = "actual"
            elif load_result.tool_diameter_mm is not None:
                voxel_tool_diameter = load_result.tool_diameter_mm
                voxel_tool_source = "report"
            else:
                voxel_tool_diameter = None
                voxel_tool_source = "config_default"

            voxel_report = self._voxel_validator.validate(
                gcode_text=load_result.gcode_text,
                controller_type=task.controller_type,
                safe_z=task.safe_z,
                stock_top_z=task.stock_top_z,
                stock_length=_DEFAULT_STOCK_LENGTH_MM,
                stock_width=_DEFAULT_STOCK_WIDTH_MM,
                stock_height=stock_height,
                tool_diameter_mm=voxel_tool_diameter,
                tool_type=task.tool_type,
                tool_source=voxel_tool_source,
            )
            task.voxel_check_passed = voxel_report.passed
            task.voxel_collision_count = voxel_report.collision_count
            task.voxel_engine = voxel_report.engine
            task.voxel_tool_diameter_mm = voxel_report.tool_diameter_mm
            # 来源以 validator 回填的 report.tool_source 为准（未提供直径时
            # 一律 config_default），任务落盘与报告口径单一事实源
            task.voxel_tool_source = voxel_report.tool_source

            if voxel_report.tool_source == "config_default":
                task.warnings.append(
                    f"体素仿真使用配置默认刀具直径 {voxel_report.tool_diameter_mm}mm"
                    f"（阶段 6 未携带且任务未提供实际装刀参数），"
                    f"与实际装刀不符时碰撞结论无效，请工程师对照刀具表确认。"
                )

            # 按特征 line_range 归因碰撞 block（口径与 InternalValidator 一致：
            # 未归因的碰撞不上挂特征，由任务级判定 + 警告兜底）
            collision_blocks = set(voxel_report.collision_blocks)
            if collision_blocks:
                attributed: set[int] = set()
                for fr in updated_features:
                    start, end = fr.line_range
                    hits = sorted(b for b in collision_blocks if start > 0 and end > 0 and start <= b <= end)
                    fr.voxel_collision_blocks = hits
                    fr.voxel_check_passed = not hits
                    attributed.update(hits)
                unattributed = collision_blocks - attributed
                if unattributed:
                    task.warnings.append(
                        f"体素仿真存在 {len(unattributed)} 个未归因到任何特征的碰撞 block：{sorted(unattributed)[:20]}"
                    )
            else:
                for fr in updated_features:
                    fr.voxel_collision_blocks = []
                    fr.voxel_check_passed = True

            for w in voxel_report.warnings:
                if w not in task.warnings:
                    task.warnings.append(w)

            if not voxel_report.passed:
                task.errors.append(
                    f"体素材料去除仿真未通过：{voxel_report.collision_count} 处碰撞"
                    f"（severity={voxel_report.severity}，engine={voxel_report.engine}）。"
                    f"DNC 下发将被闸门拦截，请回阶段 6 修改刀轨后重新生成。"
                )
        else:
            # 测试注入场景未提供体素校验器：保持 None（DNC 闸门会拦截，
            # 不允许"未知"状态冒充通过）
            task.warnings.append("体素材料去除仿真未执行（校验器未注入）")

        # 5. 执行 CAM 软件二次校验（_cam_call_lock 串行化）
        with self._store.cam_call_lock:
            cam_report = self._adapter.validate(
                gcode_file_path=load_result.gcode_file_path,
                controller_type=task.controller_type,
                cam_backend=task.cam_backend_requested,
            )

        # 更新 CAM 后端实际使用信息（可能因降级与 requested 不同）
        task.cam_backend_used = cam_report.backend_used
        if cam_report.degraded:
            task.cam_backend_fallback_reason = cam_report.degradation_reason
            task.warnings.append(
                f"CAM 后端降级：请求 {task.cam_backend_requested}，"
                f"实际使用 {cam_report.backend_used}，"
                f"原因：{cam_report.degradation_reason}"
            )

        # 6. 合并两层校验结果到 feature_validation_results
        # CAM 软件二次校验是任务级别的整体判定（NX/PowerMill 返回的 collisions
        # 不一定按特征归因），所有特征共享同一个 cam_check_passed 值
        cam_check_passed = cam_report.safe
        for fr in updated_features:
            fr.cam_check_passed = cam_check_passed
            fr.cam_messages = list(cam_report.messages)
            fr.cam_backend_used = cam_report.backend_used

        # 7. 计算统计
        passed = sum(1 for fr in updated_features if fr.overall_passed)
        failed = len(updated_features) - passed
        task.feature_validation_results = updated_features
        task.passed_features = passed
        task.failed_features = failed

        # 8. 缓存 InternalValidationReport（供 confirm_task 导出 internal_report.json）
        # 通过 _store 间接传递：写入 task.warnings 末尾的标记（避免新增字段）
        # 实际实现：在 confirm_task 时重新构建，这里仅追加 CollisionReport.warnings
        for w in collision_report.warnings:
            if w not in task.warnings:
                task.warnings.append(w)

        # 9. 状态置为 VALIDATED
        task.status = CamValidationTaskStatus.VALIDATED.value
        self._store.update_task(task)

        logger.info(
            "任务 %s 双层校验完成 total_segments=%d segments_checked=%d "
            "collisions=%d features=%d passed=%d failed=%d "
            "cam_backend=%s cam_status=%s",
            task.task_id,
            collision_report.total_segments,
            collision_report.segments_checked,
            len(collision_report.collisions),
            task.total_features,
            passed,
            failed,
            cam_report.backend_used,
            cam_report.status,
        )

    # 内部辅助：构建 FeatureValidationResult 列表

    def _build_feature_results(
        self,
        raw_feature_results: list[dict[str, Any]],
    ) -> list[FeatureValidationResult]:
        """将阶段 6 feature_results（list[dict]）转为 FeatureValidationResult。

        阶段 6 feature_results 字段（每条 dict）：
            - feature_id: 特征 ID
            - feature_type: 特征类型（plane / cylinder / hole / boss）
            - line_range: [start, end] G 代码行号区间
            - spindle_rpm: 主轴转速
            - axial_depth_mm: 轴向切深
            - limit_depth_mm: 极限切深
            - stable: 是否稳定
            - safety_margin_ratio: 安全裕度比例
            - warning: 警告信息
            - review_status: 阶段 6 审核状态（confirmed / edited）
            - edited_params: 阶段 6 工程师编辑参数

        阶段 7 仅读取这些字段，不修改；审核后追加阶段 7 的 review_status。
        """
        results: list[FeatureValidationResult] = []
        for idx, fr in enumerate(raw_feature_results):
            feature_id = str(fr.get("feature_id", f"feature_{idx}"))
            feature_type = str(fr.get("feature_type", "unknown"))

            # line_range：阶段 6 GCodeLoader 已转为 tuple，此处防御性处理
            lr = fr.get("line_range", (0, 0))
            if isinstance(lr, (list, tuple)) and len(lr) == 2:
                line_range = (int(lr[0]), int(lr[1]))
            else:
                line_range = (0, 0)

            results.append(
                FeatureValidationResult(
                    feature_id=feature_id,
                    feature_type=feature_type,
                    line_range=line_range,
                    # 内部预校验结果由 InternalValidator.validate 填充
                    internal_check_passed=True,
                    internal_events=[],
                    # CAM 软件校验结果由 _execute_validation 填充
                    cam_check_passed=True,
                    cam_messages=[],
                    cam_backend_used="internal_only",
                    # 审核状态：阶段 7 默认 PENDING
                    review_status=CamReviewStatus.PENDING.value,
                    edited_params={},
                    # 阶段 6 上下文（用于审核时回溯）
                    spindle_rpm=float(fr.get("spindle_rpm", 0.0)),
                    axial_depth_mm=float(fr.get("axial_depth_mm", 0.0)),
                    limit_depth_mm=float(fr.get("limit_depth_mm", 0.0)),
                    stable=bool(fr.get("stable", True)),
                    safety_margin_ratio=float(fr.get("safety_margin_ratio", 0.0)),
                    warning=str(fr.get("warning", "")),
                )
            )
        return results
