"""切削参数推荐流水线编排器：串联 material_resolver → recommender → 工程师审核 → ChatterParams 导出。

执行顺序
========
1. 创建任务（PENDING）：从阶段 3 输出的 STEP 路径 + 阶段 2 confirmed_features.json + 材料ID 创建任务
2. 异步触发执行：
   a. 加载阶段 2 confirmed_features.json → ReviewedFeatureRef 列表
   b. MaterialResolver 查询材料参数
   c. CuttingParamRecommender.recommend() 为每个特征推荐切削参数
   d. 状态置为 PARAMS_RECOMMENDED（等待工程师审核）
3. 工程师审核：逐条 confirmed / rejected / edited
   - 全部审核完毕 → REVIEWED
4. 导出 ChatterParams JSON（供阶段 5 颤振预测使用）→ SUCCEEDED

工业硬约束（项目记忆）：
- 切削参数必须经工程师审核 + CAM 软件二次校验后才允许上机床
- 系统定位「工程师助手」，非「全自动切削参数生成器」
- HRC52 数据待自采校准，K_s 影响阶段 5 颤振预测精度

精度继承链：
- 阶段 1 image_to_3d.precision_tier → 阶段 2 → 阶段 3 → 阶段 4
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

from app.cutting_parameters.cutting_disclaimer import (
    CuttingDisclaimer,
    build_cutting_disclaimer,
)
from app.cutting_parameters.cutting_store import (
    CuttingParametersError,
    CuttingParametersTask,
    CuttingParametersTaskStatus,
    CuttingReviewStatus,
    MaterialNotFoundError,
    RecommendedCuttingParams,
    generate_task_id,
    get_task_store,
)
from app.cutting_parameters.material_resolver import (
    MaterialParams,
    MaterialResolver,
    MaterialResolverError,
    get_material_resolver,
)
from app.cutting_parameters.recommender import (
    CuttingParamRecommender,
    FeatureNotSupportedError,
    RecommendationError,
    to_chatter_params_dict,
)
from app.core.safe_errors import safe_error_message

if TYPE_CHECKING:
    from app.config import CuttingParametersConfig

logger = logging.getLogger(__name__)

__all__ = [
    "CuttingParametersPipeline",
    "CuttingParametersResult",
    "CuttingParametersPipelineError",
    "CuttingReviewError",
    "FeaturesLoadError",
]


# 异常类


class CuttingParametersPipelineError(CuttingParametersError):
    """流水线通用异常。"""


class FeaturesLoadError(CuttingParametersPipelineError):
    """阶段 2 confirmed_features.json 加载失败。"""


class CuttingReviewError(CuttingParametersPipelineError):
    """工程师审核操作失败。"""


# 结果数据类


@dataclass
class CuttingParametersResult:
    """切削参数任务结果摘要，用于 API 响应。"""

    task_id: str
    status: str
    source_parametric_geometry_task_id: str
    material_id: str
    precision_tier: str
    mesh_calibrated: bool
    feature_count: int
    recommended_count: int
    chatter_params_path: str | None
    error_message: str | None = None
    disclaimer: CuttingDisclaimer | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "source_parametric_geometry_task_id": self.source_parametric_geometry_task_id,
            "material_id": self.material_id,
            "precision_tier": self.precision_tier,
            "mesh_calibrated": self.mesh_calibrated,
            "feature_count": self.feature_count,
            "recommended_count": self.recommended_count,
            "chatter_params_path": self.chatter_params_path,
            "error_message": self.error_message,
            "disclaimer": self.disclaimer.to_dict() if self.disclaimer else None,
        }


# 流水线


class CuttingParametersPipeline:
    """切削参数推荐流水线编排器。

    串联 material_resolver → recommender → 工程师审核 → ChatterParams 导出。
    """

    def __init__(
        self,
        cfg: "CuttingParametersConfig",
        resolver: MaterialResolver | None = None,
        recommender: CuttingParamRecommender | None = None,
    ) -> None:
        """初始化流水线。

        Args:
            cfg: CuttingParametersConfig 实例
            resolver: 材料解析器（默认全局单例，便于测试注入）
            recommender: 推荐引擎（默认用 CuttingParamRecommender()，便于测试注入）
        """
        self._cfg = cfg
        self._store = get_task_store()
        self._resolver = resolver if resolver is not None else get_material_resolver()
        self._recommender = recommender if recommender is not None else CuttingParamRecommender(self._resolver)

    # 创建任务

    def create_task(
        self,
        source_parametric_geometry_task_id: str,
        step_file_path: str,
        input_features_path: str,
        material_id: str,
        precision_tier: str = "standard",
        mesh_calibrated: bool = False,
        machine_type: str = "default",
        tool_diameter_mm: float = 10.0,
        num_flutes: int = 4,
    ) -> CuttingParametersTask:
        """创建切削参数推荐任务。

        Args:
            source_parametric_geometry_task_id: 阶段 3 任务 ID（追溯用）
            step_file_path: 阶段 3 输出的 STEP 文件路径
            input_features_path: 阶段 2 导出的 confirmed_features.json 路径
            material_id: 材料 ID (al_6061 / ti_tc4 / steel_hrc52 等)
            precision_tier: 精度档位 (coarse / standard / high)
            mesh_calibrated: 上游 mesh 是否已标定
            machine_type: 机床类型标识（仅供追溯）
            tool_diameter_mm: 刀具直径 (mm)
            num_flutes: 齿数

        Returns:
            CuttingParametersTask（状态为 PENDING）
        """
        task_id = generate_task_id()
        workspace_dir = Path(self._cfg.output_dir) / task_id
        workspace_dir.mkdir(parents=True, exist_ok=True)

        task = CuttingParametersTask(
            task_id=task_id,
            created_at=time.time(),
            source_parametric_geometry_task_id=source_parametric_geometry_task_id,
            step_file_path=step_file_path,
            input_features_path=input_features_path,
            material_id=material_id,
            precision_tier=precision_tier,
            mesh_calibrated=mesh_calibrated,
            machine_type=machine_type,
            tool_diameter_mm=tool_diameter_mm,
            num_flutes=num_flutes,
            workspace_dir=str(workspace_dir),
        )
        self._store.create_task(task)
        logger.info(
            "创建切削参数任务 task_id=%s source_pg_task_id=%s material=%s tier=%s",
            task_id,
            source_parametric_geometry_task_id,
            material_id,
            precision_tier,
        )
        return task

    # 执行流水线（异步）

    async def run_pipeline(self, task_id: str) -> CuttingParametersResult:
        """异步执行材料查询 + 参数推荐。

        Args:
            task_id: 任务 ID

        Returns:
            CuttingParametersResult

        Raises:
            CuttingParametersPipelineError: 任务不存在 / 状态不允许执行
        """
        task = self._store.get_task(task_id)
        if task is None:
            raise CuttingParametersPipelineError(f"任务不存在: {task_id}")

        if task.status not in (
            CuttingParametersTaskStatus.PENDING.value,
            CuttingParametersTaskStatus.FAILED.value,
        ):
            raise CuttingParametersPipelineError(f"任务状态不允许执行: {task.status}（仅 pending/failed 可执行）")

        # 标记为 RUNNING
        task.status = CuttingParametersTaskStatus.RUNNING.value
        task.started_at = time.time()
        task.error_message = ""
        # H10 修复：store.update_task 是同步阻塞 I/O，转移到线程池避免阻塞事件循环。
        await asyncio.to_thread(self._store.update_task, task)

        try:
            # 1. 校验材料 ID（提前失败，避免无效推荐）
            try:
                # H10 修复：get_material 涉及文件/网络 I/O，转移到线程池。
                material = await asyncio.to_thread(self._resolver.get_material, task.material_id)
            except MaterialResolverError as e:
                raise MaterialNotFoundError(str(e)) from e

            # 2. 加载阶段 2 confirmed_features.json
            features = await asyncio.to_thread(self._load_input_features, task.input_features_path)
            if not features:
                raise FeaturesLoadError(f"阶段 2 confirmed_features.json 中无任何特征: {task.input_features_path}")

            # 3. 为每个特征推荐切削参数
            recommended: list[RecommendedCuttingParams] = []
            skipped: list[dict[str, Any]] = []
            for feat in features:
                try:
                    # H10 修复：recommender.recommend 是同步计算，转移到线程池。
                    params = await asyncio.to_thread(
                        self._recommender.recommend,
                        feature_id=str(feat.get("feature_id", "")),
                        feature_type=str(feat.get("feature_type", "")),
                        material_id=task.material_id,
                        precision_tier=task.precision_tier,
                        tool_diameter_mm=task.tool_diameter_mm,
                        num_flutes=task.num_flutes,
                        machine_type=task.machine_type,
                    )
                    recommended.append(params)
                except (FeatureNotSupportedError, RecommendationError) as e:
                    skipped.append(
                        {
                            "feature_id": feat.get("feature_id", ""),
                            "feature_type": feat.get("feature_type", ""),
                            "error": str(e),
                        }
                    )
                    logger.warning(
                        "任务 %s 特征 %s 推荐失败: %s",
                        task_id,
                        feat.get("feature_id", ""),
                        e,
                    )

            if not recommended:
                raise CuttingParametersPipelineError(f"所有特征切削参数推荐均失败，skipped={len(skipped)}")

            # 4. 状态置为 PARAMS_RECOMMENDED（等待工程师审核）
            task.recommended_params = recommended
            task.status = CuttingParametersTaskStatus.PARAMS_RECOMMENDED.value
            self._store.update_task(task)

            # 5. 持久化跳过列表（便于工程师回溯）
            if skipped:
                self._persist_skipped_features(task_id, skipped)

            disclaimer = self._build_disclaimer(task, material, chatter_params_ready=False)

            logger.info(
                "任务 %s 推荐完成 recommended=%d skipped=%d material=%s",
                task_id,
                len(recommended),
                len(skipped),
                task.material_id,
            )

            return CuttingParametersResult(
                task_id=task_id,
                status=task.status,
                source_parametric_geometry_task_id=task.source_parametric_geometry_task_id,
                material_id=task.material_id,
                precision_tier=task.precision_tier,
                mesh_calibrated=task.mesh_calibrated,
                feature_count=len(features),
                recommended_count=len(recommended),
                chatter_params_path=None,
                disclaimer=disclaimer,
            )

        except Exception as e:
            safe = safe_error_message(e, context="cutting_parameters.run_pipeline")
            task.status = CuttingParametersTaskStatus.FAILED.value
            task.error_message = safe.get("message", "")
            self._store.update_task(task)
            logger.error(
                "任务 %s 执行失败 error_id=%s message=%s",
                task_id,
                safe.get("error_id"),
                safe.get("message"),
            )
            return CuttingParametersResult(
                task_id=task_id,
                status=CuttingParametersTaskStatus.FAILED.value,
                source_parametric_geometry_task_id=task.source_parametric_geometry_task_id,
                material_id=task.material_id,
                precision_tier=task.precision_tier,
                mesh_calibrated=task.mesh_calibrated,
                feature_count=0,
                recommended_count=0,
                chatter_params_path=None,
                error_message=safe.get("message"),
            )

    # 工程师审核

    def review_params(
        self,
        task_id: str,
        feature_id: str,
        review_status: str,
        reviewed_by: str = "engineer",
        edited_params: dict[str, float] | None = None,
        engineer_notes: str = "",
    ) -> RecommendedCuttingParams:
        """工程师审核单个特征的切削参数。

        Args:
            task_id: 任务 ID
            feature_id: 特征 ID
            review_status: 审核状态 (confirmed / rejected / edited)
            reviewed_by: 审核人
            edited_params: 编辑后的参数（仅 review_status=edited 时使用）
            engineer_notes: 工程师备注

        Returns:
            审核后的 RecommendedCuttingParams

        Raises:
            CuttingReviewError: 任务不存在 / 状态不允许审核 / 特征不存在
        """
        task = self._store.get_task(task_id)
        if task is None:
            raise CuttingReviewError(f"任务不存在: {task_id}")

        if task.status != CuttingParametersTaskStatus.PARAMS_RECOMMENDED.value:
            raise CuttingReviewError(f"任务状态不允许审核: {task.status}（仅 params_recommended 可审核）")

        # 校验 review_status
        valid_statuses = {
            CuttingReviewStatus.CONFIRMED.value,
            CuttingReviewStatus.REJECTED.value,
            CuttingReviewStatus.EDITED.value,
        }
        if review_status not in valid_statuses:
            raise CuttingReviewError(f"无效审核状态: {review_status}，合法值: {sorted(valid_statuses)}")

        # edited 必须提供 edited_params
        if review_status == CuttingReviewStatus.EDITED.value:
            if not edited_params:
                raise CuttingReviewError("review_status=edited 时必须提供 edited_params")

        # 查找特征
        target: RecommendedCuttingParams | None = None
        for params in task.recommended_params:
            if params.feature_id == feature_id:
                target = params
                break
        if target is None:
            raise CuttingReviewError(f"特征 ID 不存在于推荐列表中: {feature_id}")

        # 应用审核
        target.review_status = review_status
        target.reviewed_by = reviewed_by
        target.reviewed_at = time.time()
        target.engineer_notes = engineer_notes
        if review_status == CuttingReviewStatus.EDITED.value and edited_params:
            target.edited_params = dict(edited_params)

        # 检查是否全部审核完毕 REVIEWED
        all_reviewed = all(p.review_status != CuttingReviewStatus.PENDING.value for p in task.recommended_params)
        if all_reviewed:
            task.status = CuttingParametersTaskStatus.REVIEWED.value
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

    # 导出 ChatterParams

    def export_chatter_params(self, task_id: str) -> str:
        """导出 ChatterParams JSON（供阶段 5 颤振预测使用）。

        Args:
            task_id: 任务 ID

        Returns:
            ChatterParams JSON 文件路径

        Raises:
            CuttingParametersPipelineError: 任务不存在 / 状态不允许导出
        """
        task = self._store.get_task(task_id)
        if task is None:
            raise CuttingParametersPipelineError(f"任务不存在: {task_id}")

        if task.status != CuttingParametersTaskStatus.REVIEWED.value:
            raise CuttingParametersPipelineError(f"任务状态不允许导出: {task.status}（仅 reviewed 可导出）")

        # 仅导出 confirmed + edited 的特征（rejected 排除）
        exportable = [
            p
            for p in task.recommended_params
            if p.review_status
            in (
                CuttingReviewStatus.CONFIRMED.value,
                CuttingReviewStatus.EDITED.value,
            )
        ]
        if not exportable:
            raise CuttingParametersPipelineError(f"任务 {task_id} 无可导出的特征参数（所有特征均被 rejected）")

        # 转换为 ChatterParams dict 列表
        chatter_params_list: list[dict[str, Any]] = []
        for params in exportable:
            try:
                cp_dict = to_chatter_params_dict(
                    params,
                    resolver=self._resolver,
                    machine_id=task.machine_type,
                )
                chatter_params_list.append(
                    {
                        "feature_id": params.feature_id,
                        "feature_type": params.feature_type,
                        "operation": params.operation,
                        "chatter_params": cp_dict,
                        "material_id": params.material_id,
                        "k_s_n_per_mm2": cp_dict["tool"]["cutting_force_coeff"],
                    }
                )
            except (MaterialNotFoundError, RecommendationError) as e:
                logger.warning(
                    "任务 %s 特征 %s ChatterParams 转换失败: %s",
                    task_id,
                    params.feature_id,
                    e,
                )

        if not chatter_params_list:
            raise CuttingParametersPipelineError(f"任务 {task_id} 无可导出的 ChatterParams")

        # 写入 JSON
        export_path = Path(task.workspace_dir) / f"{task_id}_chatter_params.json"
        export_data = {
            "task_id": task_id,
            "source_parametric_geometry_task_id": task.source_parametric_geometry_task_id,
            "material_id": task.material_id,
            "precision_tier": task.precision_tier,
            "mesh_calibrated": task.mesh_calibrated,
            "machine_type": task.machine_type,
            "tool_diameter_mm": task.tool_diameter_mm,
            "num_flutes": task.num_flutes,
            "cam_validation_required": task.cam_validation_required,
            "exported_at": time.time(),
            "feature_count": len(chatter_params_list),
            "chatter_params_list": chatter_params_list,
            "industrial_hard_gates_note": (
                "本 ChatterParams 仅供阶段 5 颤振预测参考，"
                "实际加工必须经 CAM 软件二次校验 + 工程师审核 + 持证操作员 + 导师签字"
            ),
        }
        try:
            export_path.write_text(
                json.dumps(export_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            raise CuttingParametersPipelineError(f"ChatterParams 写入失败: {e}") from e

        # 状态置为 SUCCEEDED
        task.status = CuttingParametersTaskStatus.SUCCEEDED.value
        task.chatter_params_path = str(export_path)
        task.completed_at = time.time()
        self._store.update_task(task)

        logger.info(
            "任务 %s ChatterParams 导出完成 path=%s features=%d",
            task_id,
            export_path,
            len(chatter_params_list),
        )
        return str(export_path)

    # 内部辅助

    def _load_input_features(self, features_path: str) -> list[dict[str, Any]]:
        """加载阶段 2 confirmed_features.json。

        接受多种格式（兼容阶段 2/3 导出）：
        - list[dict]：直接返回
        - dict with "features" key：返回 dict["features"]
        - dict with "confirmed_features" key：返回 dict["confirmed_features"]
        """
        path = Path(features_path)
        if not path.exists():
            raise FeaturesLoadError(f"confirmed_features.json 不存在: {features_path}")

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise FeaturesLoadError(f"confirmed_features.json 解析失败: {e}") from e

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("features", "confirmed_features", "input_features"):
                if key in data and isinstance(data[key], list):
                    return data[key]
        raise FeaturesLoadError(
            "confirmed_features.json 格式不支持，应为 list 或含 'features'/'confirmed_features' 键的 dict"
        )

    def _build_disclaimer(
        self,
        task: CuttingParametersTask,
        material: MaterialParams,
        chatter_params_ready: bool,
    ) -> CuttingDisclaimer:
        """构造精度告知。"""
        return build_cutting_disclaimer(
            mesh_calibrated=task.mesh_calibrated,
            feature_source=task.input_features_path,
            step_source=task.step_file_path,
            material_id=task.material_id,
            material_calibration_status=material.calibration_status,
            precision_tier=task.precision_tier,
            machine_type=task.machine_type,
            tool_diameter_mm=task.tool_diameter_mm,
            chatter_params_ready=chatter_params_ready,
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
