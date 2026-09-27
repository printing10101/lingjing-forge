"""CAM 后端策略分发器（P1-3 拆分自原 cam_adapter.py）。

``CamAdapter`` 是阶段 7 CAM 软件二次校验接入层，采用策略模式分发到 5 个子后端：
    - internal_only：仅内部预校验，跳过 CAM 软件
    - pycam：subprocess 调用 PyCAM 包装器脚本
    - nx_open：subprocess 调用 NX Open Python 脚本
    - powermill：subprocess 调用 PowerMill 宏
    - manual：生成手动校验清单 + 工程师回填（兜底）

降级策略（项目记忆硬约束：链路不中断）：
    - cam_backend == "pycam" 但包装器脚本未配置或不存在 → 自动降级到 manual，追加警告
    - cam_backend == "nx_open" 但 nx_open_executable 为空 → 自动降级到 manual，追加警告
    - cam_backend == "powermill" 但 powermill_executable 为空 → 自动降级到 manual，追加警告
    - 降级不阻塞任务，告知文本必须明确标注「实际使用的 CAM 后端」与「降级原因」

工程边界（项目记忆硬约束）：
    - 系统绝不直接接口 CNC 控制器，CAM 软件调用通过 subprocess
    - NX Open / PowerMill SDK 升级不破坏 cam_validation 模块（subprocess + JSON 解耦）
    - 物理机床执行由人工 + CAM 软件 + 持证操作员完成，阶段 7 不触及

线程安全：
    - CamAdapter 本身无状态（子后端持有不可变 config）
    - 调用方 CamValidationPipeline._cam_call_lock 串行化 CAM 软件调用
    - 防止 NX/PowerMill 并发实例崩溃
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.cam_validation.cam_store import CamAdapterError
from app.config import CamValidationConfig

from ._common import (
    CamSoftwareReport,
    _BaseBackend,
    _VALID_BACKENDS,
    _VALID_STATUSES,
    logger,
)
from .internal_only import _InternalOnlyBackend
from .manual import _ManualBackend
from .nx_open import _NxOpenBackend
from .powermill import _PowerMillBackend
from .pycam import _PyCamBackend


class CamAdapter:
    """阶段 7 CAM 软件二次校验接入层（策略模式分发）。

    策略模式分发到 5 个子后端：
        - internal_only：仅内部预校验，跳过 CAM 软件
        - pycam：subprocess 调用 PyCAM 包装器脚本
        - nx_open：subprocess 调用 NX Open Python 脚本
        - powermill：subprocess 调用 PowerMill 宏
        - manual：生成手动校验清单 + 工程师回填（兜底）

    工程边界（项目记忆硬约束）：
        - 系统绝不直接接口 CNC 控制器，CAM 软件调用通过 subprocess
        - NX Open / PowerMill SDK 升级不破坏 cam_validation 模块（subprocess + JSON 解耦）
        - 物理机床执行由人工 + CAM 软件 + 持证操作员完成，阶段 7 不触及

    线程安全：
        - CamAdapter 本身无状态（子后端持有不可变 config）
        - 调用方 CamValidationPipeline._cam_call_lock 串行化 CAM 软件调用
        - 防止 NX/PowerMill 并发实例崩溃
    """

    def __init__(self, config: CamValidationConfig) -> None:
        """初始化 CAM 适配器，构建 5 个子后端实例。

        Args:
            config: CAM 校验配置（CamValidationConfig）
        """
        self._config = config

        # 构建 5 个子后端（不可变，线程安全）
        self._backends: dict[str, _BaseBackend] = {
            "internal_only": _InternalOnlyBackend(),
            "pycam": _PyCamBackend(config.pycam_executable),
            "nx_open": _NxOpenBackend(config.nx_open_executable),
            "powermill": _PowerMillBackend(config.powermill_executable),
            "manual": _ManualBackend(),
        }

        # 手动校验清单输出目录（manual 后端使用）
        # 默认使用 config.output_dir 下的 manual_checklists/ 子目录
        self._manual_output_dir: str = os.path.join(config.output_dir, "manual_checklists")

    def validate(
        self,
        gcode_file_path: str,
        controller_type: str,
        cam_backend: str,
    ) -> CamSoftwareReport:
        """调用指定 CAM 后端执行二次校验。

        Args:
            gcode_file_path: G 代码文件绝对路径
            controller_type: 目标控制器类型（fanuc / siemens / heidenhain）
            cam_backend: CAM 后端名称（internal_only / pycam / nx_open /
                powermill / manual）

        Returns:
            CamSoftwareReport 归一化报告

        Raises:
            CamAdapterError: 未知 CAM 后端名称（不在 _VALID_BACKENDS 内）
        """
        # 1. 校验 cam_backend 合法性
        if cam_backend not in _VALID_BACKENDS:
            raise CamAdapterError(f"未知 CAM 后端：{cam_backend}。合法后端：{sorted(_VALID_BACKENDS)}。")

        # 2. 检查 G 代码文件存在性（所有后端共享的前置校验）
        if not gcode_file_path or not Path(gcode_file_path).is_file():
            # G 代码文件不存在：降级到 manual（不抛错，由 pipeline 决定是否 FAILED）
            logger.warning(
                "CamAdapter: G 代码文件不存在或为空：%s，降级到 manual 后端生成校验清单。",
                gcode_file_path,
            )
            manual = self._backends["manual"]
            self._inject_manual_output_dir(manual)
            report = manual.validate(gcode_file_path or "(empty)", controller_type)
            report.degraded = True
            report.degradation_reason = (
                f"G 代码文件不存在或路径为空：{gcode_file_path}。无法执行 CAM 软件校验，降级到 manual。"
            )
            report.messages.insert(0, f"[G 代码缺失降级] {report.degradation_reason}")
            return report

        # 3. 分发到对应子后端
        backend = self._backends.get(cam_backend)
        if backend is None:
            # 理论上不会走到这里（步骤 1 已校验），但保留防御性兜底
            raise CamAdapterError(f"CAM 后端实例未找到：{cam_backend}（虽然名称合法）。可能是 CamAdapter 初始化异常。")

        # 4. manual 后端注入输出目录
        if isinstance(backend, _ManualBackend):
            self._inject_manual_output_dir(backend)

        # 5. 执行校验
        try:
            report = backend.validate(gcode_file_path, controller_type)
        except CamAdapterError:
            raise
        except Exception as e:
            # 子后端内部异常兜底：降级到 manual
            logger.exception(
                "CamAdapter: 子后端 %s 抛出未捕获异常，降级到 manual。",
                cam_backend,
            )
            manual = self._backends["manual"]
            self._inject_manual_output_dir(manual)
            report = manual.validate(gcode_file_path, controller_type)
            report.degraded = True
            report.degradation_reason = f"子后端 {cam_backend} 抛出未捕获异常：{e}。自动降级到 manual 后端。"
            report.messages.insert(0, f"[子后端异常降级] {report.degradation_reason}")

        # 6. 校验归一化报告的 status 合法性
        if report.status not in _VALID_STATUSES:
            logger.warning(
                "CamAdapter: 子后端 %s 返回非法 status=%r，强制改为 error。",
                cam_backend,
                report.status,
            )
            report.status = "error"
            report.messages.append("[归一化警告] 子后端返回非法 status，已改为 error。")

        logger.info(
            "CamAdapter.validate 完成：backend=%s → used=%s, status=%s, degraded=%s, collisions=%d",
            cam_backend,
            report.backend_used,
            report.status,
            report.degraded,
            len(report.collisions),
        )

        return report

    def list_available_backends(self) -> list[dict[str, Any]]:
        """列出所有 CAM 后端及其可用性状态（供 /precision_info 端点使用）。

        Returns:
            后端信息列表，每条 dict 含：
                - name: 后端名称
                - available: 是否可用（True/False）
                - reason: 不可用原因（available=True 时为空字符串）
                - description: 后端描述
        """
        backends_info: list[dict[str, Any]] = []

        # internal_only：始终可用
        backends_info.append(
            {
                "name": "internal_only",
                "available": True,
                "reason": "",
                "description": "仅内部预校验（AABB 包围盒），秒级反馈，不可上机",
            }
        )

        # pycam
        # 可用性判定：包装器脚本文件存在（脚本内部自检 pycam 包是否可导入）
        pycam_available = bool(self._config.pycam_executable) and Path(self._config.pycam_executable).is_file()
        backends_info.append(
            {
                "name": "pycam",
                "available": pycam_available,
                "reason": "" if pycam_available else "PyCAM 包装器脚本未配置或文件不存在（LNN_CAM_PYCAM_EXECUTABLE）",
                "description": "开源 PyCAM 刀轨校验（4 项基础检查，无需许可证）",
            }
        )

        # nx_open
        nx_available = bool(self._config.nx_open_executable) and Path(self._config.nx_open_executable).is_file()
        backends_info.append(
            {
                "name": "nx_open",
                "available": nx_available,
                "reason": "" if nx_available else "NX Open 脚本未配置或文件不存在",
                "description": "Siemens NX Open 工业级刀轨仿真（需许可证）",
            }
        )

        # powermill
        pm_available = bool(self._config.powermill_executable) and Path(self._config.powermill_executable).exists()
        backends_info.append(
            {
                "name": "powermill",
                "available": pm_available,
                "reason": "" if pm_available else "PowerMill 可执行文件未配置或不存在",
                "description": "Autodesk PowerMill 工业级刀轨仿真（需许可证）",
            }
        )

        # manual：始终可用
        backends_info.append(
            {
                "name": "manual",
                "available": True,
                "reason": "",
                "description": "手动校验清单 + 工程师回填（兜底，永不失败）",
            }
        )

        return backends_info

    def _inject_manual_output_dir(self, backend: _BaseBackend) -> None:
        """将 self._manual_output_dir 注入到 _ManualBackend 实例。

        Args:
            backend: 子后端实例（仅 _ManualBackend 使用 _output_dir 属性）
        """
        if isinstance(backend, _ManualBackend):
            backend._output_dir = self._manual_output_dir  # type: ignore[attr-defined]
