"""ADR 阶段 1-7 条件模块链路路由注册.

聚合拍照重建全链路（依赖可选库 trimesh / cadquery / torch 等）：
- image_to_3d       — ADR-006 阶段 1：拍照重建（COLMAP SfM → OpenMVS → 标定块尺度归一化）
- feature_extraction — ADR-007 阶段 2：mesh → 平面/圆柱/孔检测 → 工程师审核 → 导出特征集
- parametric_geometry — ADR-008 阶段 3：confirmed_features → B-rep → 装配 → STEP → 两轮审核
- cutting_parameters — ADR-009 阶段 4：STEP + features + material → 推荐参数 → 审核 → ChatterParams
- chatter_prediction — ADR-010 阶段 5：双路径颤振预测（Tlusty 解析 / LTC 神经网络）
- gcode_generation  — ADR-014 阶段 6：ChatterReport + OperationPlan → G 代码生成 + 单轮审核
- cam_validation    — ADR-018 阶段 7：G 代码 + 审核记录 → CAM 软件（NX/PowerMill/PyCAM）二次校验

设计约束（项目记忆硬约束）：
- 每个阶段都依赖可选库，缺失仅 warning 不阻断启动
- 运行时还受 ``config.<module>.enabled`` 开关控制
- 阶段间状态机：``SUCCEEDED`` 状态禁止删除（下游阶段可能已引用其产物）
- 所有产物 ``cam_validation_required`` 始终 True，实际加工必须经 CAM 软件二次校验
- 系统定位「工程师助手」，非「全自动生产线」

返回值：
- ``register`` 返回 ``dict[str, bool]`` 表示各阶段模块的导入可用状态，
  由 ``router_registry`` 接收并写入同名全局变量，供测试与外部观察使用
"""

from __future__ import annotations

import importlib
import logging

from fastapi import FastAPI

from app.config import config

logger = logging.getLogger(__name__)


def _try_include_router(
    app: FastAPI,
    import_path: str,
    router_attr: str = "router",
    *,
    enabled: bool = True,
) -> bool:
    """尝试导入并注册路由，失败仅 warning 不阻断启动.

    Args:
        app: FastAPI 应用实例
        import_path: 模块导入路径
        router_attr: 模块中路由对象的属性名
        enabled: 运行时开关；False 时直接返回 False

    Returns:
        是否成功导入并注册
    """
    if not enabled:
        return False
    try:
        module = importlib.import_module(import_path)
        router = getattr(module, router_attr)
        app.include_router(router)
        return True
    except ImportError as exc:
        logger.warning("条件模块 %s 导入失败，跳过：%s", import_path, exc)
        return False


def register(app: FastAPI) -> dict[str, bool]:
    """注册 ADR 阶段 1-7 条件模块链路.

    Args:
        app: FastAPI 应用实例

    Returns:
        各阶段模块的导入可用状态字典，键名与 ``router_registry`` 全局变量同名：
        ``_IMAGE_TO_3D_AVAILABLE`` / ``_FEATURE_EXTRACTION_AVAILABLE`` /
        ``_PARAMETRIC_GEOMETRY_AVAILABLE`` / ``_CUTTING_PARAMETERS_AVAILABLE`` /
        ``_CHATTER_PREDICTION_AVAILABLE`` / ``_GCODE_GENERATION_AVAILABLE`` /
        ``_CAM_VALIDATION_AVAILABLE``
    """
    flags: dict[str, bool] = {
        "_IMAGE_TO_3D_AVAILABLE": False,
        "_FEATURE_EXTRACTION_AVAILABLE": False,
        "_PARAMETRIC_GEOMETRY_AVAILABLE": False,
        "_CUTTING_PARAMETERS_AVAILABLE": False,
        "_CHATTER_PREDICTION_AVAILABLE": False,
        "_GCODE_GENERATION_AVAILABLE": False,
        "_CAM_VALIDATION_AVAILABLE": False,
    }

    # === 阶段 1：拍照重建（ADR-006）===
    # 手机多角度拍照 COLMAP SfM OpenMVS 稠密化 标定块尺度归一化 mesh
    flags["_IMAGE_TO_3D_AVAILABLE"] = _try_include_router(
        app,
        "app.api.v1.image_to_3d.routes",
        enabled=config.image_to_3d.enabled,
    )

    # === 阶段 2：几何特征辅助提取（ADR-007）===
    # mesh 平面/圆柱/孔检测 工程师审核 导出已确认特征集
    # 设计原则：mesh 参数化 CAD 自动转换工业上未解决，本模块输出「算法建议特征」，
    # 必须经工程师逐条审核（confirmed / rejected / edited）后才允许进入阶段 3。
    flags["_FEATURE_EXTRACTION_AVAILABLE"] = _try_include_router(
        app,
        "app.api.v1.feature_extraction.routes",
        enabled=config.feature_extraction.enabled,
    )

    # === 阶段 3：参数化几何输出（ADR-008）===
    # confirmed_features.json B-rep 装配 STEP 两轮审核 最终 STEP
    # 设计约束：工程师必须两轮审核（STEP_GENERATED + finalize）后才允许下载最终 STEP；
    # 最终 STEP 必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后才允许上机床。
    flags["_PARAMETRIC_GEOMETRY_AVAILABLE"] = _try_include_router(
        app,
        "app.api.v1.parametric_geometry.routes",
        enabled=config.parametric_geometry.enabled,
    )

    # === 阶段 4：切削参数推荐（ADR-009）===
    # STEP + confirmed_features.json + material_id 推荐参数 审核 ChatterParams
    # 设计约束：推荐参数必须经工程师逐条审核后才允许导出 ChatterParams；
    # SUCCEEDED 状态禁止删除（阶段 5 颤振预测可能已引用其 ChatterParams）；
    # HRC52 数据待自采校准，K_s 标记为 pending_calibration。
    flags["_CUTTING_PARAMETERS_AVAILABLE"] = _try_include_router(
        app,
        "app.api.v1.cutting_parameters.routes",
        enabled=config.cutting_parameters.enabled,
    )

    # === 阶段 5：颤振预测接入 LTC（ADR-010）===
    # 双路径预测：路径 A Tlusty 解析法（工程默认） 路径 B LTC 神经网络（实验性）
    #  路径 C 兜底默认值
    # 设计约束：HRC52 置信度强制降低至 PENDING_CALIBRATION_CONFIDENCE=0.5；
    # SUCCEEDED 状态禁止删除（阶段 6 G 代码生成可能已引用其 ChatterReport）；
    # cam_validation_required 始终 True。
    flags["_CHATTER_PREDICTION_AVAILABLE"] = _try_include_router(
        app,
        "app.api.v1.chatter_prediction.routes",
        enabled=config.chatter_prediction.enabled,
    )

    # === 阶段 6：G 代码生成接入（ADR-014）===
    # 数据流：阶段 5 ChatterReport + 阶段 3 OperationPlan
    #  GCodeGenerator 工程师单轮审核 G 代码文件 + 审核记录 JSON
    # 设计约束：stable == False 的特征禁止生成 G 代码（强制回阶段 5）；
    # SUCCEEDED 状态禁止删除（阶段 7 CAM 校验可能已引用 G 代码产物）；
    # cam_validation_required 始终 True；系统绝不直接接口 CNC 控制器。
    flags["_GCODE_GENERATION_AVAILABLE"] = _try_include_router(
        app,
        "app.api.v1.gcode_generation.routes",
        enabled=config.gcode_generation.enabled,
    )

    # === 阶段 7：CAM 校验模块（ADR-018）===
    # 数据流：阶段 6 G 代码 + report.json CAM 软件（NX/PowerMill/PyCAM）二次校验
    #  校验报告 工程师确认 上机许可
    # 设计约束：cam_validation_required 始终 True；最终上机仍需持证操作员 + 导师签字 + 保险。
    flags["_CAM_VALIDATION_AVAILABLE"] = _try_include_router(
        app,
        "app.api.v1.cam_validation.routes",
        enabled=config.cam_validation.enabled,
    )

    return flags
