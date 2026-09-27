"""颤振预测流水线编排器：串联 chatter_params 加载 → 双路径预测 → 工程师审核 → ChatterReport 导出。

执行顺序
========
1. 创建任务（PENDING）：从阶段 4 输出的 ChatterParams JSON 路径 + 材料 ID 创建任务
2. 异步触发执行：
   a. 加载阶段 4 ChatterParams JSON（含 chatter_params_list 字段，每项一个特征的预测参数）
   b. ChatterPredictorAdapter.predict_feature() 对每个特征执行双路径预测
      - 默认走 Tlusty 解析法（工程可用）
      - LTC 神经网络路径仅在 chatter_model.pt 存在时尝试（实验性）
      - HRC52 材料 pending_calibration 时强制降低置信度
   c. 状态置为 PREDICTED（等待工程师审核）
3. 工程师审核：逐条 confirmed / rejected / edited
   - 全部审核完毕 → REVIEWED
4. 导出 ChatterReport JSON（供阶段 6 G 代码生成使用）→ SUCCEEDED

工业硬约束（项目记忆）：
- 颤振预测必须经工程师审核 + CAM 软件二次校验后才允许上机床
- 系统定位「工程师助手」，非「全自动颤振预测器」
- HRC52 数据待自采校准，K_s 影响颤振预测精度，置信度已强制降低
- K_s（cutting_force_coeff）直接传递，不二次拟合
- SUCCEEDED 状态禁止删除（阶段 6 G 代码生成可能已引用其 ChatterReport）
- cam_validation_required 始终 True

精度继承链：
- 阶段 1 image_to_3d.precision_tier → 阶段 2 → 阶段 3 → 阶段 4 → 阶段 5（本模块）
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

from app.chatter_prediction.chatter_disclaimer import (
    ChatterDisclaimer,
    build_chatter_disclaimer,
)
from app.chatter_prediction.chatter_store import (
    ChatterParamsLoadError,
    ChatterPredictionError,
    ChatterPredictionTask,
    ChatterPredictionTaskStatus,
    ChatterReviewStatus,
    FeatureChatterResult,
    generate_task_id,
    get_task_store,
)
from app.chatter_prediction.predictor_adapter import (
    ChatterPredictorAdapter,
    PredictorAdapterError,
)
from app.core.safe_errors import safe_error_message

if TYPE_CHECKING:
    from app.config import ChatterPredictionConfig

logger = logging.getLogger(__name__)

__all__ = [
    "ChatterPredictionPipeline",
    "ChatterPredictionResult",
    "ChatterPredictionPipelineError",
    "ChatterReviewError",
    "ChatterParamsLoadError",
]


# 异常类


class ChatterPredictionPipelineError(ChatterPredictionError):
    """流水线通用异常。"""


class ChatterReviewError(ChatterPredictionError):
    """工程师审核操作失败。"""


# 结果数据类


@dataclass
class ChatterPredictionResult:
    """颤振预测任务结果摘要，用于 API 响应。"""

    task_id: str
    status: str
    source_cutting_parameters_task_id: str
    material_id: str
    precision_tier: str
    mesh_calibrated: bool
    feature_count: int
    predicted_count: int
    analytical_count: int
    neural_network_count: int
    fallback_count: int
    ltc_model_available: bool
    chatter_report_path: str | None
    error_message: str | None = None
    disclaimer: ChatterDisclaimer | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "source_cutting_parameters_task_id": self.source_cutting_parameters_task_id,
            "material_id": self.material_id,
            "precision_tier": self.precision_tier,
            "mesh_calibrated": self.mesh_calibrated,
            "feature_count": self.feature_count,
            "predicted_count": self.predicted_count,
            "analytical_count": self.analytical_count,
            "neural_network_count": self.neural_network_count,
            "fallback_count": self.fallback_count,
            "ltc_model_available": self.ltc_model_available,
            "chatter_report_path": self.chatter_report_path,
            "error_message": self.error_message,
            "disclaimer": self.disclaimer.to_dict() if self.disclaimer else None,
        }


# 流水线


class ChatterPredictionPipeline:
    """颤振预测流水线编排器。

    串联 chatter_params 加载 → 双路径预测 → 工程师审核 → ChatterReport 导出。
    """

    def __init__(
        self,
        cfg: "ChatterPredictionConfig",
        adapter: ChatterPredictorAdapter | None = None,
    ) -> None:
        """初始化流水线。

        Args:
            cfg: ChatterPredictionConfig 实例
            adapter: 预测适配器（默认用 ChatterPredictorAdapter()，便于测试注入）
        """
        self._cfg = cfg
        self._store = get_task_store()
        self._adapter = adapter if adapter is not None else ChatterPredictorAdapter()

    # 创建任务

    def create_task(
        self,
        source_cutting_parameters_task_id: str,
        chatter_params_path: str,
        material_id: str,
        precision_tier: str = "standard",
        mesh_calibrated: bool = False,
        machine_type: str = "vmc_850",
    ) -> ChatterPredictionTask:
        """创建颤振预测任务。

        Args:
            source_cutting_parameters_task_id: 阶段 4 任务 ID（追溯用）
            chatter_params_path: 阶段 4 输出的 ChatterParams JSON 路径
            material_id: 材料 ID
            precision_tier: 精度档位 (coarse / standard / high)
            mesh_calibrated: 上游 mesh 是否已标定
            machine_type: 机床类型标识（仅供追溯）

        Returns:
            ChatterPredictionTask（状态为 PENDING）
        """
        task_id = generate_task_id()
        workspace_dir = Path(self._cfg.output_dir) / task_id
        workspace_dir.mkdir(parents=True, exist_ok=True)

        task = ChatterPredictionTask(
            task_id=task_id,
            created_at=time.time(),
            source_cutting_parameters_task_id=source_cutting_parameters_task_id,
            chatter_params_path=chatter_params_path,
            material_id=material_id,
            precision_tier=precision_tier,
            mesh_calibrated=mesh_calibrated,
            machine_type=machine_type,
            workspace_dir=str(workspace_dir),
            ltc_model_available=self._adapter.ltc_model_available,
        )
        self._store.create_task(task)
        logger.info(
            "创建颤振预测任务 task_id=%s source_cp_task_id=%s material=%s tier=%s ltc=%s",
            task_id,
            source_cutting_parameters_task_id,
            material_id,
            precision_tier,
            self._adapter.ltc_model_available,
        )
        return task

    # 执行流水线（异步）

    async def run_pipeline(self, task_id: str) -> ChatterPredictionResult:
        """异步执行 ChatterParams 加载 + 双路径预测。

        Args:
            task_id: 任务 ID

        Returns:
            ChatterPredictionResult

        Raises:
            ChatterPredictionPipelineError: 任务不存在 / 状态不允许执行
        """
        task = self._store.get_task(task_id)
        if task is None:
            raise ChatterPredictionPipelineError(f"任务不存在: {task_id}")

        if task.status not in (
            ChatterPredictionTaskStatus.PENDING.value,
            ChatterPredictionTaskStatus.FAILED.value,
        ):
            raise ChatterPredictionPipelineError(f"任务状态不允许执行: {task.status}（仅 pending/failed 可执行）")

        # 标记为 RUNNING
        task.status = ChatterPredictionTaskStatus.RUNNING.value
        task.started_at = time.time()
        task.error_message = ""
        # H10 修复：store.update_task 是同步阻塞 I/O，转移到线程池。
        await asyncio.to_thread(self._store.update_task, task)

        try:
            # 1. 加载阶段 4 ChatterParams JSON
            # H10 修复：_load_chatter_params 涉及文件 I/O + JSON 解析，转移到线程池。
            chatter_params_list = await asyncio.to_thread(self._load_chatter_params, task.chatter_params_path)
            if not chatter_params_list:
                raise ChatterParamsLoadError(f"阶段 4 ChatterParams JSON 中无任何特征: {task.chatter_params_path}")

            # 2. 对每个特征执行双路径预测
            results: list[FeatureChatterResult] = []
            skipped: list[dict[str, Any]] = []
            for item in chatter_params_list:
                feature_id = str(item.get("feature_id", ""))
                feature_type = str(item.get("feature_type", ""))
                cp_dict = item.get("chatter_params")
                if not cp_dict or not isinstance(cp_dict, dict):
                    skipped.append(
                        {
                            "feature_id": feature_id,
                            "feature_type": feature_type,
                            "error": "chatter_params 字段缺失或非 dict",
                        }
                    )
                    continue

                try:
                    # H10 修复：predict_feature 涉及 LTC 模型推理，转移到线程池。
                    result = await asyncio.to_thread(
                        self._adapter.predict_feature,
                        feature_id=feature_id,
                        feature_type=feature_type,
                        material_id=task.material_id,
                        chatter_params_dict=cp_dict,
                        source_cutting_params_task_id=task.source_cutting_parameters_task_id,
                    )
                    results.append(result)
                except (PredictorAdapterError, ValueError, KeyError) as e:
                    skipped.append(
                        {
                            "feature_id": feature_id,
                            "feature_type": feature_type,
                            "error": str(e),
                        }
                    )
                    logger.warning(
                        "任务 %s 特征 %s 预测失败: %s",
                        task_id,
                        feature_id,
                        e,
                    )

            if not results:
                raise ChatterPredictionPipelineError(f"所有特征颤振预测均失败，skipped={len(skipped)}")

            # 3. 统计预测方法分布
            analytical_count = sum(1 for r in results if r.method == "analytical")
            nn_count = sum(1 for r in results if r.method == "neural_network")
            fb_count = sum(1 for r in results if r.method == "fallback")

            # 4. 状态置为 PREDICTED（等待工程师审核）
            task.feature_results = results
            task.status = ChatterPredictionTaskStatus.PREDICTED.value
            task.analytical_count = analytical_count
            task.neural_network_count = nn_count
            task.fallback_count = fb_count
            task.ltc_model_available = self._adapter.ltc_model_available
            self._store.update_task(task)

            # 5. 持久化跳过列表
            if skipped:
                self._persist_skipped_features(task_id, skipped)

            # 6. 构造 disclaimer
            prediction_method = self._resolve_prediction_method(analytical_count, nn_count, fb_count)
            ltc_active_ratio = nn_count / len(results) if results else 0.0
            disclaimer = self._build_disclaimer(
                task=task,
                prediction_method=prediction_method,
                ltc_active_ratio=ltc_active_ratio,
                chatter_report_ready=False,
            )

            logger.info(
                "任务 %s 预测完成 predicted=%d skipped=%d analytical=%d nn=%d fallback=%d",
                task_id,
                len(results),
                len(skipped),
                analytical_count,
                nn_count,
                fb_count,
            )

            return ChatterPredictionResult(
                task_id=task_id,
                status=task.status,
                source_cutting_parameters_task_id=task.source_cutting_parameters_task_id,
                material_id=task.material_id,
                precision_tier=task.precision_tier,
                mesh_calibrated=task.mesh_calibrated,
                feature_count=len(chatter_params_list),
                predicted_count=len(results),
                analytical_count=analytical_count,
                neural_network_count=nn_count,
                fallback_count=fb_count,
                ltc_model_available=self._adapter.ltc_model_available,
                chatter_report_path=None,
                disclaimer=disclaimer,
            )

        except Exception as e:
            safe = safe_error_message(e, context="chatter_prediction.run_pipeline")
            task.status = ChatterPredictionTaskStatus.FAILED.value
            task.error_message = safe.get("message", "")
            self._store.update_task(task)
            logger.error(
                "任务 %s 执行失败 error_id=%s message=%s",
                task_id,
                safe.get("error_id"),
                safe.get("message"),
            )
            return ChatterPredictionResult(
                task_id=task_id,
                status=ChatterPredictionTaskStatus.FAILED.value,
                source_cutting_parameters_task_id=task.source_cutting_parameters_task_id,
                material_id=task.material_id,
                precision_tier=task.precision_tier,
                mesh_calibrated=task.mesh_calibrated,
                feature_count=0,
                predicted_count=0,
                analytical_count=0,
                neural_network_count=0,
                fallback_count=0,
                ltc_model_available=self._adapter.ltc_model_available,
                chatter_report_path=None,
                error_message=safe.get("message"),
            )

    # 工程师审核

    def review_result(
        self,
        task_id: str,
        feature_id: str,
        review_status: str,
        reviewed_by: str = "engineer",
        edited_params: dict[str, float] | None = None,
        engineer_notes: str = "",
    ) -> FeatureChatterResult:
        """工程师审核单个特征的颤振预测结果。

        Args:
            task_id: 任务 ID
            feature_id: 特征 ID
            review_status: 审核状态 (confirmed / rejected / edited)
            reviewed_by: 审核人
            edited_params: 编辑后的参数（仅 review_status=edited 时使用）
                可编辑字段：limit_depth_mm / axial_depth_mm / stable（0/1）
            engineer_notes: 工程师备注

        Returns:
            审核后的 FeatureChatterResult

        Raises:
            ChatterReviewError: 任务不存在 / 状态不允许审核 / 特征不存在
        """
        task = self._store.get_task(task_id)
        if task is None:
            raise ChatterReviewError(f"任务不存在: {task_id}")

        if task.status != ChatterPredictionTaskStatus.PREDICTED.value:
            raise ChatterReviewError(f"任务状态不允许审核: {task.status}（仅 predicted 可审核）")

        # 校验 review_status
        valid_statuses = {
            ChatterReviewStatus.CONFIRMED.value,
            ChatterReviewStatus.REJECTED.value,
            ChatterReviewStatus.EDITED.value,
        }
        if review_status not in valid_statuses:
            raise ChatterReviewError(f"无效审核状态: {review_status}，合法值: {sorted(valid_statuses)}")

        # edited 必须提供 edited_params
        if review_status == ChatterReviewStatus.EDITED.value:
            if not edited_params:
                raise ChatterReviewError("review_status=edited 时必须提供 edited_params")

        # 查找特征
        target: FeatureChatterResult | None = None
        for result in task.feature_results:
            if result.feature_id == feature_id:
                target = result
                break
        if target is None:
            raise ChatterReviewError(f"特征 ID 不存在于预测结果列表中: {feature_id}")

        # 应用审核
        target.review_status = review_status
        target.reviewed_by = reviewed_by
        target.reviewed_at = time.time()
        target.engineer_notes = engineer_notes
        if review_status == ChatterReviewStatus.EDITED.value and edited_params:
            target.edited_params = dict(edited_params)
            # edited 时若修改了 stable 字段，同步更新 stable（0/1 bool）
            if "stable" in edited_params:
                target.stable = bool(edited_params["stable"])
            # edited 时若修改了 limit_depth_mm，同步更新
            if "limit_depth_mm" in edited_params:
                target.limit_depth_mm = float(edited_params["limit_depth_mm"])
            # edited 时若修改了 axial_depth_mm，同步更新
            if "axial_depth_mm" in edited_params:
                target.axial_depth_mm = float(edited_params["axial_depth_mm"])
                # 重新计算稳定性裕度
                if target.limit_depth_mm > 0:
                    target.stability_margin = target.axial_depth_mm / target.limit_depth_mm

        # 检查是否全部审核完毕 REVIEWED
        all_reviewed = all(r.review_status != ChatterReviewStatus.PENDING.value for r in task.feature_results)
        if all_reviewed:
            task.status = ChatterPredictionTaskStatus.REVIEWED.value
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

    # 导出 ChatterReport

    def export_chatter_report(self, task_id: str) -> str:
        """导出 ChatterReport JSON（供阶段 6 G 代码生成使用）。

        Args:
            task_id: 任务 ID

        Returns:
            ChatterReport JSON 文件路径

        Raises:
            ChatterPredictionPipelineError: 任务不存在 / 状态不允许导出
        """
        task = self._store.get_task(task_id)
        if task is None:
            raise ChatterPredictionPipelineError(f"任务不存在: {task_id}")

        if task.status != ChatterPredictionTaskStatus.REVIEWED.value:
            raise ChatterPredictionPipelineError(f"任务状态不允许导出: {task.status}（仅 reviewed 可导出）")

        # 仅导出 confirmed + edited 的特征（rejected 排除）
        exportable = [
            r
            for r in task.feature_results
            if r.review_status
            in (
                ChatterReviewStatus.CONFIRMED.value,
                ChatterReviewStatus.EDITED.value,
            )
        ]
        if not exportable:
            raise ChatterPredictionPipelineError(f"任务 {task_id} 无可导出的预测结果（所有特征均被 rejected）")

        # 构造 ChatterReport
        # 显式写入 task_status + prediction_method，供阶段 6 加载器校验契约
        # （task_status 即将置为 SUCCEEDED；prediction_method 由 method_statistics 推断）
        prediction_method = self._resolve_prediction_method(
            task.analytical_count,
            task.neural_network_count,
            task.fallback_count,
        )
        export_data = {
            "task_id": task_id,
            "task_status": ChatterPredictionTaskStatus.SUCCEEDED.value,
            "prediction_method": prediction_method,
            "source_cutting_parameters_task_id": task.source_cutting_parameters_task_id,
            "material_id": task.material_id,
            "precision_tier": task.precision_tier,
            "mesh_calibrated": task.mesh_calibrated,
            "machine_type": task.machine_type,
            "cam_validation_required": task.cam_validation_required,  # 始终 True
            "ltc_model_available": task.ltc_model_available,
            "exported_at": time.time(),
            "feature_count": len(exportable),
            "method_statistics": {
                "analytical": task.analytical_count,
                "neural_network": task.neural_network_count,
                "fallback": task.fallback_count,
            },
            "feature_results": [r.to_dict() for r in exportable],
            "industrial_hard_gates_note": (
                "本 ChatterReport 仅供阶段 6 G 代码生成参考，"
                "实际加工必须经 CAM 软件二次校验 + 工程师审核 + 持证操作员 + 导师签字。"
                "极限切深为理论值，实际加工必须留 20% 安全裕度。"
            ),
        }

        # 写入 JSON
        export_path = Path(task.workspace_dir) / f"{task_id}_chatter_report.json"
        try:
            export_path.write_text(
                json.dumps(export_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            raise ChatterPredictionPipelineError(f"ChatterReport 写入失败: {e}") from e

        # 状态置为 SUCCEEDED
        task.status = ChatterPredictionTaskStatus.SUCCEEDED.value
        task.chatter_report_path = str(export_path)
        task.completed_at = time.time()
        self._store.update_task(task)

        logger.info(
            "任务 %s ChatterReport 导出完成 path=%s features=%d",
            task_id,
            export_path,
            len(exportable),
        )
        return str(export_path)

    # 内部辅助

    def _load_chatter_params(self, chatter_params_path: str) -> list[dict[str, Any]]:
        """加载阶段 4 ChatterParams JSON。

        阶段 4 export_chatter_params 输出格式：
            {
                "task_id": "...",
                "material_id": "...",
                "chatter_params_list": [
                    {
                        "feature_id": "...",
                        "feature_type": "...",
                        "operation": "...",
                        "chatter_params": {spindle_rpm, machine, tool, axial_depth},
                        "material_id": "...",
                        "k_s_n_per_mm2": ...
                    },
                    ...
                ]
            }
        """
        path = Path(chatter_params_path)
        if not path.exists():
            raise ChatterParamsLoadError(f"阶段 4 ChatterParams JSON 不存在: {chatter_params_path}")

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise ChatterParamsLoadError(f"阶段 4 ChatterParams JSON 解析失败: {e}") from e

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("chatter_params_list", "features", "feature_results"):
                if key in data and isinstance(data[key], list):
                    return data[key]
        raise ChatterParamsLoadError(
            "阶段 4 ChatterParams JSON 格式不支持，应为 list 或含 'chatter_params_list'/'features' 键的 dict"
        )

    def _resolve_prediction_method(
        self,
        analytical_count: int,
        nn_count: int,
        fb_count: int,
    ) -> str:
        """根据预测分布解析主预测方法。"""
        if fb_count > 0:
            return "fallback"
        if nn_count > 0 and analytical_count > 0:
            return "mixed"
        if nn_count > 0:
            return "neural_network"
        return "analytical"

    def _build_disclaimer(
        self,
        task: ChatterPredictionTask,
        prediction_method: str,
        ltc_active_ratio: float,
        chatter_report_ready: bool,
    ) -> ChatterDisclaimer:
        """构造精度告知。

        HRC52 材料强制标注 pending_calibration（与 predictor_adapter 一致）。
        """
        from app.chatter_prediction.predictor_adapter import PENDING_CALIBRATION_MATERIALS

        material_id_lower = task.material_id.lower()
        material_calibration_status = (
            "pending_calibration" if material_id_lower in PENDING_CALIBRATION_MATERIALS else "calibrated"
        )

        return build_chatter_disclaimer(
            mesh_calibrated=task.mesh_calibrated,
            chatter_params_source=task.chatter_params_path,
            material_id=task.material_id,
            material_calibration_status=material_calibration_status,
            precision_tier=task.precision_tier,
            machine_type=task.machine_type,
            prediction_method=prediction_method,
            ltc_model_available=task.ltc_model_available,
            ltc_active_ratio=ltc_active_ratio,
            chatter_report_ready=chatter_report_ready,
        )

    def _persist_skipped_features(self, task_id: str, skipped: list[dict[str, Any]]) -> None:
        """持久化跳过的特征列表（便于工程师回溯）。"""
        task = self._store.get_task(task_id)
        if task is None:
            return
        path = Path(task.workspace_dir) / f"{task_id}_skipped_features.json"
        try:
            path.write_text(
                json.dumps(skipped, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("跳过特征列表持久化失败 %s: %s", task_id, e)
