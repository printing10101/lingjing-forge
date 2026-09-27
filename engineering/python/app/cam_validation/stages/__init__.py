"""CAM 校验流水线 stages 子包入口（P1-3 拆分自原 pipeline.py）。

本子包按阶段职责将原 1175 行的 ``CamValidationPipeline`` God class 拆分为
3 个 mixin，通过多重继承组合回 ``CamValidationPipeline``：

- ``_common.py``：共享基础设施
    - 常量：``_DEFAULT_STOCK_LENGTH_MM`` / ``_DEFAULT_STOCK_WIDTH_MM`` /
      ``_DEFAULT_MODE``
    - 数据类：``CamValidationResult``（公开 API）
    - 模块 logger
- ``pre_check.py``：``PreCheckMixin``
    任务生命周期管理（create / delete / get / list）+ disclaimer / result 构建
- ``software_check.py``：``SoftwareCheckMixin``
    双层校验核心执行（run_pipeline / _execute_validation / _build_feature_results）
- ``merge_report.py``：``MergeReportMixin``
    工程师审核 + 确认 + 报告导出（review_task / confirm_task /
    _export_cam_report / _export_internal_report）

设计原则（与 audit_log.py 的 ChainMixin / WriterMixin / ReaderMixin /
ArchiverMixin 一致）：
    - mixin 之间不直接继承，通过 ``CamValidationPipeline`` 多重继承组合
    - mixin 方法通过 ``self`` 共享实例状态（``_cfg`` / ``_store`` /
      ``_loader`` / ``_validator`` / ``_adapter``）
    - 跨 mixin 调用通过 ``self._build_result`` / ``self._build_disclaimer``
      等方法名约定

项目记忆硬约束：
    - cam_validation_required 始终 True，不可由环境变量关闭
    - allow_delete_succeeded 始终 False（SUCCEEDED 状态禁止删除）
    - 系统绝不直接接口 CNC 控制器，CAM 软件调用通过 subprocess
    - 阶段 7 产物终止于「CAM 校验报告 JSON」，不触及物理机床
"""

from __future__ import annotations

# 公开 API re-export
from ._common import CamValidationResult

# 内部 mixin re-export（供 pipeline.py 组合，也便于测试 / 内省）
from .merge_report import MergeReportMixin
from .pre_check import PreCheckMixin
from .software_check import SoftwareCheckMixin

__all__: list[str] = [
    # 公开数据类
    "CamValidationResult",
    # mixin（供 CamValidationPipeline 多重继承组合）
    "PreCheckMixin",
    "SoftwareCheckMixin",
    "MergeReportMixin",
]
