"""阶段 5 ChatterReport 加载器（阶段 6）。

职责：
    读取阶段 5 导出的 ChatterReport JSON，校验必填字段，
    返回 list[FeatureChatterResult]（直接复用阶段 5 的 dataclass）。

项目记忆硬约束：
    - 若 ChatterReport 不存在或字段缺失，抛出 ChatterReportLoadError
    - 若 ChatterReport 的 task_status != SUCCEEDED，拒绝加载并提示「阶段 5 未审核通过」
    - K_s（cutting_force_coeff）直接来自阶段 4，不二次拟合（阶段 6 不涉及拟合）
    - HRC52 材料 pending_calibration 标注由阶段 5 完成，阶段 6 仅继承

精度继承链：
    阶段 5 ChatterReport 包含：
    - feature_results: list[FeatureChatterResult]
      - feature_id / feature_type / material_id
      - spindle_rpm / axial_depth_mm / limit_depth_mm
      - stable / confidence / prediction_method
      - review_status（阶段 5 工程师审核状态）
    - task_status: SUCCEEDED（阶段 5 已审核通过）
    - prediction_method: analytical / neural_network / mixed
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.chatter_prediction.chatter_store import FeatureChatterResult
from app.gcode_generation.gcode_store import (
    ChatterReportLoadError,
    PENDING_CALIBRATION_MATERIALS,
)

logger = logging.getLogger(__name__)


# ChatterReport JSON 必填字段

REQUIRED_REPORT_FIELDS = {
    "task_id",
    "task_status",
    "feature_results",
    "material_id",
    "prediction_method",
}

REQUIRED_FEATURE_FIELDS = {
    "feature_id",
    "feature_type",
    "material_id",
    "spindle_rpm",
    "axial_depth_mm",
    "limit_depth_mm",
    "stable",
    "stability_margin",
    "method",
    "ltc_active",
    "confidence",
}


# LoadedChatterReport：加载后的 ChatterReport 结构


class LoadedChatterReport:
    """加载后的阶段 5 ChatterReport。

    封装原始 JSON + 解析后的 feature_results 列表。
    """

    def __init__(
        self,
        report_path: str,
        raw_json: dict[str, Any],
        feature_results: list[FeatureChatterResult],
        material_id: str,
        prediction_method: str,
        task_status: str,
        pending_calibration: bool,
    ) -> None:
        self.report_path = report_path
        self.raw_json = raw_json
        self.feature_results = feature_results
        self.material_id = material_id
        self.prediction_method = prediction_method
        self.task_status = task_status
        self.pending_calibration = pending_calibration

    @property
    def total_features(self) -> int:
        return len(self.feature_results)

    @property
    def stable_features(self) -> int:
        return sum(1 for f in self.feature_results if f.stable)

    @property
    def unstable_features(self) -> int:
        return sum(1 for f in self.feature_results if not f.stable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_path": self.report_path,
            "material_id": self.material_id,
            "prediction_method": self.prediction_method,
            "task_status": self.task_status,
            "pending_calibration": self.pending_calibration,
            "total_features": self.total_features,
            "stable_features": self.stable_features,
            "unstable_features": self.unstable_features,
            "feature_count": len(self.feature_results),
        }


# ChatterReportLoader：加载器


class ChatterReportLoader:
    """阶段 5 ChatterReport 加载器。

    使用方式：
        loader = ChatterReportLoader()
        report = loader.load("/path/to/chatter_report.json")
        for feature in report.feature_results:
            logger.info("特征 %s: 稳定 %s, 极限深度 %.3f", feature.feature_id, feature.stable, feature.limit_depth_mm)
    """

    def load(self, report_path: str) -> LoadedChatterReport:
        """加载 ChatterReport JSON。

        Args:
            report_path: ChatterReport JSON 文件路径

        Returns:
            LoadedChatterReport 对象

        Raises:
            ChatterReportLoadError: 文件不存在 / JSON 格式错误 / 必填字段缺失 /
                                   task_status != SUCCEEDED
        """
        path = Path(report_path)
        if not path.exists():
            raise ChatterReportLoadError(f"阶段 5 ChatterReport 不存在: {report_path}")

        try:
            raw_json = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ChatterReportLoadError(f"ChatterReport JSON 格式错误: {e}") from e

        # 校验必填字段
        missing = REQUIRED_REPORT_FIELDS - set(raw_json.keys())
        if missing:
            raise ChatterReportLoadError(f"ChatterReport 缺少必填字段: {missing}")

        task_status = raw_json["task_status"]
        if task_status != "succeeded":
            raise ChatterReportLoadError(
                f"阶段 5 ChatterReport task_status={task_status}，"
                "未审核通过，拒绝加载。请先在阶段 5 完成审核并导出 SUCCEEDED 报告。"
            )

        # 解析 feature_results
        raw_features = raw_json["feature_results"]
        if not isinstance(raw_features, list):
            raise ChatterReportLoadError("ChatterReport feature_results 必须是列表")

        feature_results: list[FeatureChatterResult] = []
        for i, raw_feature in enumerate(raw_features):
            feature = self._parse_feature(raw_feature, i, report_path)
            feature_results.append(feature)

        material_id = raw_json["material_id"]
        prediction_method = raw_json["prediction_method"]

        # 检测 HRC52 待校准材料
        pending_calibration = self._detect_pending_calibration(material_id, feature_results)

        return LoadedChatterReport(
            report_path=report_path,
            raw_json=raw_json,
            feature_results=feature_results,
            material_id=material_id,
            prediction_method=prediction_method,
            task_status=task_status,
            pending_calibration=pending_calibration,
        )

    def _parse_feature(
        self,
        raw_feature: dict[str, Any],
        index: int,
        report_path: str,
    ) -> FeatureChatterResult:
        """解析单个特征结果，保留阶段 5 的完整状态。

        阶段 5 通过 FeatureChatterResult.to_dict() 导出全部 23 个字段，
        此处对必填字段（无默认值）做缺失校验，对可选字段用 .get() 提供回退值，
        以便阶段 6 工程师审核时能看到阶段 5 的完整审核上下文（review_status /
        material_calibration_status / engineer_notes 等）。
        """
        missing = REQUIRED_FEATURE_FIELDS - set(raw_feature.keys())
        if missing:
            raise ChatterReportLoadError(f"ChatterReport feature_results[{index}] 缺少必填字段: {missing}")

        # [N-H3] value 非 None 校验：float(None) / bool(None) 会抛 TypeError 或静默转 False
        # 阶段 5 导出时若某字段为 null，此处给出明确错误而非在 float() 处崩溃
        null_fields = [field for field in REQUIRED_FEATURE_FIELDS if raw_feature[field] is None]
        if null_fields:
            raise ChatterReportLoadError(
                f"ChatterReport feature_results[{index}] 必填字段值为 null: {null_fields}，报告路径: {report_path}"
            )

        return FeatureChatterResult(
            feature_id=str(raw_feature["feature_id"]),
            feature_type=str(raw_feature["feature_type"]),
            material_id=str(raw_feature["material_id"]),
            spindle_rpm=float(raw_feature["spindle_rpm"]),
            axial_depth_mm=float(raw_feature["axial_depth_mm"]),
            limit_depth_mm=float(raw_feature["limit_depth_mm"]),
            stable=bool(raw_feature["stable"]),
            stability_margin=float(raw_feature["stability_margin"]),
            method=str(raw_feature["method"]),
            ltc_active=bool(raw_feature["ltc_active"]),
            confidence=float(raw_feature["confidence"]),
            inference_time_ms=float(raw_feature.get("inference_time_ms", 0.0)),
            warnings=list(raw_feature.get("warnings", [])),
            material_calibration_status=str(raw_feature.get("material_calibration_status", "calibrated")),
            review_status=str(raw_feature.get("review_status", "pending")),
            edited_params=dict(raw_feature.get("edited_params", {})),
            reviewed_by=str(raw_feature.get("reviewed_by", "")),
            reviewed_at=float(raw_feature.get("reviewed_at", 0.0)),
            engineer_notes=str(raw_feature.get("engineer_notes", "")),
            source_cutting_params_task_id=str(raw_feature.get("source_cutting_params_task_id", "")),
            machine_id=str(raw_feature.get("machine_id", "")),
            tool_id=str(raw_feature.get("tool_id", "")),
            cutting_force_coeff=float(raw_feature.get("cutting_force_coeff", 0.0)),
        )

    def _detect_pending_calibration(
        self,
        material_id: str,
        feature_results: list[FeatureChatterResult],
    ) -> bool:
        """检测是否含 HRC52 待校准材料。

        与阶段 5 predictor_adapter.PENDING_CALIBRATION_MATERIALS 对齐
        （阶段 6 直接复用 gcode_store 中重新声明的同名 frozenset）。
        """
        material_id_lower = material_id.lower()
        if material_id_lower in PENDING_CALIBRATION_MATERIALS:
            return True
        for feature in feature_results:
            if feature.material_id.lower() in PENDING_CALIBRATION_MATERIALS:
                return True
        return False


__all__ = [
    "LoadedChatterReport",
    "ChatterReportLoader",
    "REQUIRED_REPORT_FIELDS",
    "REQUIRED_FEATURE_FIELDS",
]
