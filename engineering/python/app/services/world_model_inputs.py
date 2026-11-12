"""从已审核的特征任务组装世界模型几何输入，保留尺度来源。"""

from copy import deepcopy
from pathlib import Path

import numpy as np

from app.contracts.world_model import InvalidStateError
from app.feature_extraction.feature_store import get_feature_store
from app.plugins.world_model.geometry_features_deriver import GeometryFeaturesDeriver


def calibrated_mesh(task) -> bool:
    """只有同一个已标定上游网格才能沿用毫米尺度。"""
    if not task.source_reconstruction_task_id:
        return False
    try:
        from app.image_to_3d import get_task_store
    except ImportError:
        return False

    upstream = get_task_store().get(task.source_reconstruction_task_id)
    return bool(upstream and upstream.status == "succeeded" and upstream.calibrated
                and Path(upstream.output_mesh_path).resolve() == Path(task.input_mesh_path).resolve())


def list_geometry_sources():
    """只列出完成审核且含有效特征的任务，不扫描用户文件系统。"""
    return [
        {"task_id": task.task_id, "name": Path(task.input_mesh_path).name,
         "updated_at": task.updated_at, "calibrated": calibrated_mesh(task)}
        for task in get_feature_store().list_all(limit=100)
        if task.status in ("reviewed", "succeeded") and any(
            f.review_status in ("confirmed", "edited") for f in task.features)
    ]


def prepare_geometry(task_id: str, millimeters_per_unit: float | None = None, expected_updated_at: float | None = None):
    """从服务端任务记录读取网格；未标定网格要求用户提供明确尺度。"""
    from app.feature_extraction.pipeline import MeshLoadError, _load_mesh, _try_import_trimesh

    task = get_feature_store().get(task_id)
    if task is None:
        raise InvalidStateError("未找到所选零件特征，请刷新列表后重新选择。")
    if expected_updated_at is not None and expected_updated_at != task.updated_at:
        raise InvalidStateError("零件特征已变更，请重新读取零件几何。")
    if task.status not in ("reviewed", "succeeded") or any(f.review_status == "pending" for f in task.features):
        raise InvalidStateError("零件特征尚未完成审核，请先完成特征审核。")
    features = deepcopy([f for f in task.features if f.review_status in ("confirmed", "edited")])
    if not features:
        raise InvalidStateError("所选零件没有已确认特征，请先审核有效特征。")
    calibrated = calibrated_mesh(task)
    if calibrated:
        if millimeters_per_unit is not None and millimeters_per_unit != 1:
            raise InvalidStateError("该网格已按毫米标定，不能重复缩放。")
        scale = 1.0
    else:
        if millimeters_per_unit is None or not np.isfinite(millimeters_per_unit) or millimeters_per_unit <= 0:
            raise InvalidStateError("网格尺度尚未确认，请填写每个网格单位对应的毫米数。")
        scale = float(millimeters_per_unit)
    try:
        vertices, _ = _load_mesh(Path(task.input_mesh_path), _try_import_trimesh())
    except (MeshLoadError, OSError, ValueError, RuntimeError) as exc:
        raise InvalidStateError("零件网格无法读取，请重新导入零件并提取特征。") from exc
    vertices = np.asarray(vertices, dtype=np.float64) * scale
    if vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) < 4 or not np.isfinite(vertices).all():
        raise InvalidStateError("零件网格顶点无效，请检查模型完整性。")
    if np.any(np.ptp(vertices, axis=0) <= 0):
        raise InvalidStateError("所选网格缺少完整三维尺寸，请选择实体零件。")
    # 派生器消费的面积/半径须与网格使用同一物理尺度，尊重审核编辑值。
    for feature in features:
        if not np.isfinite(feature.confidence) or not 0 <= feature.confidence <= 1:
            raise InvalidStateError("特征置信度无效，请重新审核。")
        params = feature.effective_params()
        for key, exponent in (("area_mm2", 2), ("radius_mm", 1)):
            if key in params:
                params[key] = float(params[key]) * scale ** exponent
                if not np.isfinite(params[key]) or params[key] < 0:
                    raise InvalidStateError("特征尺寸无效，请重新审核。")
        feature.params = params
        feature.edited_params = params if feature.review_status == "edited" else {}
    result = GeometryFeaturesDeriver.from_feature_extraction(features, vertices, filter_reviewed=True)
    geometry = result.to_dict()["geometry"]
    values = geometry["bbox_dimensions"] + geometry["feature_vector"] + [geometry["symmetry_score"], geometry["complexity_score"]]
    if not result.is_complete or not np.isfinite(values).all():
        raise InvalidStateError("几何特征不完整，请重新检查特征审核结果。")
    return {
        "geometry": geometry,
        "source": {"task_id": task.task_id, "name": Path(task.input_mesh_path).name,
                   "updated_at": task.updated_at, "feature_count": len(features),
                   "scale_source": "upstream_calibration" if calibrated else "user_supplied",
                   "millimeters_per_unit": scale},
        "notes": result.derivation_notes,
    }
