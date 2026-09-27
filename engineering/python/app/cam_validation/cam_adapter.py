"""CAM 软件二次校验接入层（re-export shim）。

本模块在 P1-3 重构后仅作为公开 API 的 re-export shim，原 1202 行 God class
已按策略模式拆分为 ``app.cam_validation.backends`` 子包：

- ``backends/_common.py``：共享基础设施
    - 常量：``_VALID_BACKENDS`` / ``_VALID_STATUSES`` / ``_JSON_*_FIELD``
    - 数据类：``CamSoftwareReport``（公开）
    - 抽象基类：``_BaseBackend``
- ``backends/internal_only.py``：``_InternalOnlyBackend``（跳过 CAM 软件）
- ``backends/pycam.py``：``_PyCamBackend``（subprocess 调用 PyCAM 包装器）
- ``backends/nx_open.py``：``_NxOpenBackend``（subprocess 调用 NX Open）
- ``backends/powermill.py``：``_PowerMillBackend``（subprocess 调用 PowerMill）
- ``backends/manual.py``：``_ManualBackend``（兜底，生成校验清单）
- ``backends/dispatcher.py``：``CamAdapter``（策略分发器，公开类）

策略模式分发到 5 个子后端：
    - internal_only : 仅内部预校验，跳过 CAM 软件（告知文本标注「未二次校验」）
    - pycam         : subprocess 调用 PyCAM 包装器脚本（开源，4 项基础检查）
    - nx_open       : subprocess 调用 NX Open Python 脚本（licensed，工业级）
    - powermill     : subprocess 调用 PowerMill 宏（licensed，工业级）
    - manual        : 生成手动校验清单 + 工程师回填（兜底，永不失败）

降级策略（项目记忆硬约束：链路不中断）：
    - cam_backend == "pycam" 但包装器脚本未配置或不存在 → 自动降级到 manual，追加警告
    - cam_backend == "nx_open" 但 nx_open_executable 为空 → 自动降级到 manual，追加警告
    - cam_backend == "powermill" 但 powermill_executable 为空 → 自动降级到 manual，追加警告
    - 降级不阻塞任务，告知文本必须明确标注「实际使用的 CAM 后端」与「降级原因」

工程边界（项目记忆硬约束）：
    - 系统绝不直接接口 CNC 控制器，CAM 软件调用通过 subprocess
    - NX Open / PowerMill SDK 升级不破坏 cam_validation 模块（subprocess + JSON 解耦）
    - 物理机床执行由人工 + CAM 软件 + 持证操作员完成，阶段 7 不触及

向后兼容：
    - ``from app.cam_validation.cam_adapter import CamAdapter`` 仍可用
    - ``from app.cam_validation.cam_adapter import CamSoftwareReport`` 仍可用
    - 类与函数签名不变
"""

from __future__ import annotations

# 从拆分子包 re-export 公开符号（向后兼容）
from app.cam_validation.backends._common import (
    CamSoftwareReport,
    _BaseBackend,
    _JSON_COLLISIONS_FIELD,
    _JSON_MESSAGES_FIELD,
    _JSON_STATUS_FIELD,
    _VALID_BACKENDS,
    _VALID_STATUSES,
)
from app.cam_validation.backends.dispatcher import CamAdapter
from app.cam_validation.backends.internal_only import _InternalOnlyBackend
from app.cam_validation.backends.manual import _ManualBackend
from app.cam_validation.backends.nx_open import _NxOpenBackend
from app.cam_validation.backends.powermill import _PowerMillBackend
from app.cam_validation.backends.pycam import _PyCamBackend

__all__: list[str] = [
    # 公开
    "CamAdapter",
    "CamSoftwareReport",
    # 内部（以下划线开头，保留 re-export 以兼容可能的内省 / 测试用法）
    "_BaseBackend",
    "_InternalOnlyBackend",
    "_PyCamBackend",
    "_NxOpenBackend",
    "_PowerMillBackend",
    "_ManualBackend",
    # 常量
    "_VALID_BACKENDS",
    "_VALID_STATUSES",
    "_JSON_STATUS_FIELD",
    "_JSON_COLLISIONS_FIELD",
    "_JSON_MESSAGES_FIELD",
]
