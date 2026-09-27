"""CAM 校验流水线编排器（阶段 7）—— mixin 组合 + re-export shim。

本模块在 P1-3 重构后作为公开 API 的 re-export shim + mixin 组合点，原 1175 行
God class 已按阶段职责拆分为 ``app.cam_validation.stages`` 子包的 3 个 mixin：

- ``stages/_common.py``：共享基础设施
    - 常量：``_DEFAULT_STOCK_LENGTH_MM`` / ``_DEFAULT_STOCK_WIDTH_MM`` /
      ``_DEFAULT_MODE``
    - 数据类：``CamValidationResult``（公开 API）
    - 模块 logger
- ``stages/pre_check.py``：``PreCheckMixin``
    任务生命周期管理（create / delete / get / list）+ disclaimer / result 构建
- ``stages/software_check.py``：``SoftwareCheckMixin``
    双层校验核心执行（run_pipeline / _execute_validation / _build_feature_results）
- ``stages/merge_report.py``：``MergeReportMixin``
    工程师审核 + 确认 + 报告导出（review_task / confirm_task /
    _export_cam_report / _export_internal_report）

本模块通过多重继承将 3 个 mixin 组合回 ``CamValidationPipeline``，并保留原
``__init__`` 签名以初始化共享实例状态（``_cfg`` / ``_store`` / ``_loader`` /
``_validator`` / ``_adapter``）。

职责（与原 pipeline.py 一致，无行为回归）：
    - create_task(...) : 创建 PENDING 任务（含 source_gcode_report_path /
                        source_gcode_file_path / controller_type / cam_backend）
    - run_pipeline(task_id) : PENDING → RUNNING → VALIDATED（或 FAILED / TIMEOUT）
        1. GCodeLoader.load_from_report() 加载阶段 6 G 代码 + feature_results
        2. InternalValidator.validate() 复用 CollisionDetector 执行内部预校验
        3. CamAdapter.validate() 调用 CAM 软件二次校验（_cam_call_lock 串行化）
        4. 合并两层校验结果到 feature_validation_results
        5. 写入 internal_report + cam_software_report
    - review_task(task_id, feature_id, review_status, edited_params) :
        VALIDATED → REVIEWED（单轮审核，与阶段 5/6 一致）
    - confirm_task(task_id, reviewer) : REVIEWED → SUCCEEDED
        - 导出 cam_report.json 到 output_dir/{task_id}.cam_report.json
        - 导出 internal_report.json 到 output_dir/{task_id}.internal_report.json
        - SUCCEEDED 后禁止删除（allow_delete_succeeded=False 硬约束）
    - delete_task(task_id) : 仅允许删除 PENDING / FAILED / TIMEOUT 状态任务
    - get_task(task_id) / list_tasks(status_filter) : 任务查询

线程安全（项目记忆硬约束）：
    - CamTaskStore 使用 threading.Lock 保护 _tasks 字典
    - 审核操作使用独立的 _review_lock 防止并发审核冲突
    - 导出操作使用 _export_lock 防止文件写入竞争
    - CAM 软件调用使用 _cam_call_lock 防止 NX/PowerMill 并发实例崩溃

工业硬约束（项目记忆）：
    - 系统定位「工程师助手」，非「全自动 CAM 校验器」
    - 内部预校验（CollisionDetector）是 AABB 包围盒级别快速预筛，
      **不可替代** CAM 软件二次校验
    - 系统绝不直接接口 CNC 控制器，CAM 软件调用通过 subprocess
    - cam_validation_required 始终 True，不可由环境变量关闭
    - SUCCEEDED 状态禁止删除（cam_report.json 是链路最终产物，供审计追溯）
    - HRC52 pending_calibration 由阶段 5 标注，阶段 7 仅继承并体现在告知文本
    - 阶段 7 产物终止于「CAM 校验报告 JSON」，不触及物理机床

向后兼容：
    - ``from app.cam_validation.pipeline import CamValidationPipeline`` 仍可用
    - ``from app.cam_validation.pipeline import CamValidationResult`` 仍可用
    - 类与函数签名不变（``__init__`` 参数 cfg / loader / validator /
      adapter 保持原样，便于测试注入；新增第 5 个可选参数 voxel_validator）
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# 公开 API re-export（向后兼容）
from app.cam_validation.stages._common import CamValidationResult

# mixin 导入（用于多重继承组合）
from app.cam_validation.stages.merge_report import MergeReportMixin
from app.cam_validation.stages.pre_check import PreCheckMixin
from app.cam_validation.stages.software_check import SoftwareCheckMixin

# __init__ 所需的依赖注入类
from app.cam_validation.cam_adapter import CamAdapter
from app.cam_validation.cam_store import get_task_store
from app.cam_validation.gcode_loader import GCodeLoader
from app.cam_validation.internal_validator import InternalValidator
from app.cam_validation.voxel_validator import VoxelValidator

if TYPE_CHECKING:
    from app.config import CamValidationConfig

__all__ = [
    "CamValidationPipeline",
    "CamValidationResult",
]


# CamValidationPipeline：3 个 mixin 的多重继承组合点


class CamValidationPipeline(
    PreCheckMixin,
    SoftwareCheckMixin,
    MergeReportMixin,
):
    """CAM 校验流水线编排器（3 个 mixin 多重继承组合）。

    串联 GCodeLoader → InternalValidator → CamAdapter → 工程师审核 → 报告导出。

    设计原则（项目记忆硬约束）：
        - 组合（has-a）：CamValidationPipeline 持有 GCodeLoader /
          InternalValidator / CamAdapter 实例，不继承任何子模块
        - mixin 拆分：3 个 mixin 按阶段职责划分（PreCheck / SoftwareCheck /
          MergeReport），通过多重继承组合，方法共享实例状态
        - 单例 store：通过 get_task_store() 获取 CamTaskStore 单例，
          所有任务状态变更通过 store 完成
        - 线程安全：审核 / 导出 / CAM 调用使用 store 暴露的 3 个独立锁
        - 不直接接口 CNC：CAM 软件调用通过 subprocess（在 CamAdapter 内部）

    状态机（与阶段 5/6 对齐）：
        PENDING → RUNNING → VALIDATED → REVIEWED → SUCCEEDED
                    ↘ FAILED
                    ↘ TIMEOUT
                    ↘ CANCELLED

    方法分布（mixin → 方法）：
        - ``PreCheckMixin``:
            - ``create_task`` / ``delete_task`` / ``get_task`` / ``list_tasks``
            - ``_build_result`` / ``_build_disclaimer`` / ``_resolve_output_dir``
        - ``SoftwareCheckMixin``:
            - ``run_pipeline`` (async) / ``_execute_validation`` (async) /
              ``_build_feature_results``
        - ``MergeReportMixin``:
            - ``review_task`` / ``confirm_task``
            - ``_export_cam_report`` / ``_build_cam_software_report_dict`` /
              ``_export_internal_report``
    """

    def __init__(
        self,
        cfg: "CamValidationConfig | None" = None,
        loader: GCodeLoader | None = None,
        validator: InternalValidator | None = None,
        adapter: CamAdapter | None = None,
        voxel_validator: VoxelValidator | None = None,
    ) -> None:
        """初始化流水线。

        Args:
            cfg: CamValidationConfig 实例（可为 None，使用默认 output_dir）
            loader: GCodeLoader 实例（默认用 GCodeLoader()，便于测试注入）
            validator: InternalValidator 实例（默认用 InternalValidator(cfg)）
            adapter: CamAdapter 实例（默认用 CamAdapter(cfg)）
            voxel_validator: VoxelValidator 实例（默认用 VoxelValidator(cfg)，
                体素材料去除仿真为闭环强制层，测试可注入替身）
        """
        self._cfg = cfg
        self._store = get_task_store()
        self._loader = loader if loader is not None else GCodeLoader()

        if validator is not None:
            self._validator = validator
        elif cfg is not None:
            self._validator = InternalValidator(cfg)
        else:
            # cfg 为 None 的测试场景：构造一个最小可用 config
            # （InternalValidator 需要 config.precision_tier 等字段，
            # 此分支仅用于单元测试注入 validator 时跳过构造）
            self._validator = validator

        # 体素校验器：cfg 为 None 且未显式注入时保持 None（测试场景跳过体素层）
        if voxel_validator is not None:
            self._voxel_validator = voxel_validator
        elif cfg is not None:
            self._voxel_validator = VoxelValidator(cfg)
        else:
            self._voxel_validator = voxel_validator

        if adapter is not None:
            self._adapter = adapter
        elif cfg is not None:
            self._adapter = CamAdapter(cfg)
        else:
            # cfg 为 None：无法构造 CamAdapter（依赖 config 的 5 个后端配置）
            # 测试场景必须显式注入 adapter
            self._adapter = adapter
