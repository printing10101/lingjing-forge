"""CAM 后端子包（P1-3 拆分自原 cam_adapter.py）。

本子包将原 1202 行 God class ``cam_adapter.py`` 按策略模式拆分为 7 个文件：

- ``_common.py``：共享基础设施
    - 常量：``_VALID_BACKENDS`` / ``_VALID_STATUSES`` / ``_JSON_*_FIELD``
    - 数据类：``CamSoftwareReport``（公开）
    - 抽象基类：``_BaseBackend``
    - 模块 logger
- ``internal_only.py``：``_InternalOnlyBackend``（跳过 CAM 软件）
- ``pycam.py``：``_PyCamBackend``（subprocess 调用 PyCAM 包装器）
- ``nx_open.py``：``_NxOpenBackend``（subprocess 调用 NX Open）
- ``powermill.py``：``_PowerMillBackend``（subprocess 调用 PowerMill）
- ``manual.py``：``_ManualBackend``（兜底，生成校验清单）
- ``dispatcher.py``：``CamAdapter``（策略分发器，公开类）

向后兼容：
    - ``from app.cam_validation.cam_adapter import CamAdapter`` 仍可用
      （由 ``cam_adapter.py`` re-export shim 保障）
    - ``from app.cam_validation.cam_adapter import CamSoftwareReport`` 仍可用
    - ``from app.cam_validation.backends import CamAdapter`` 也支持
    - ``from app.cam_validation.backends.dispatcher import CamAdapter`` 也支持

项目记忆硬约束：
    - CAM 软件调用通过 subprocess，系统绝不直接接口 CNC 控制器
    - NX Open / PowerMill SDK 升级不破坏 cam_validation 模块（subprocess + JSON 解耦）
    - cam_validation_required 始终 True，不可由环境变量关闭
    - 降级不阻塞任务，告知文本必须明确标注「实际使用的 CAM 后端」与「降级原因」
"""

from __future__ import annotations

# 公开符号：CamAdapter（编排器入口）+ CamSoftwareReport（数据类）
# 子后端类（_InternalOnlyBackend 等）以下划线开头，视为内部实现细节，
# 不在 backends/__init__.py 公开导出，但可通过子模块路径直接访问。
from ._common import CamSoftwareReport
from .dispatcher import CamAdapter

__all__: list[str] = [
    "CamAdapter",
    "CamSoftwareReport",
]
