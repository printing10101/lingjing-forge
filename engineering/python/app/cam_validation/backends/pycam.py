"""PyCAM 后端：subprocess 调用 PyCAM 包装器脚本（P1-3 拆分自原 cam_adapter.py）。

PyCAM 0.6.x 是开源 CNC 刀轨生成器（DXF/STL → G 代码），不是完整的
G 代码仿真器；其 ``Importers/`` 仅支持 DXF/STL/SVG/PS，**不支持 G 代码
导入**，也无 ``__main__.py``（``python -m pycam`` 不可用）。

因此本后端通过项目自带的包装器脚本
``python/scripts/cam_adapters/pycam/autorun_gcode_check.py`` 调用，
包装器脚本自实现轻量 G 代码解析器，并在 PyCAM 能力范围内执行 4 项
基础校验（工作空间边界 / 安全 Z / G0 材料内 / 刀轨连续性）。

调用协议与 NX Open 后端对齐：
    ``python <pycam_executable> <gcode_file_path> <controller_type>``
    stdout 输出 JSON：
    ``{"status": "pass"/"fail"/"error", "collisions": [...], "messages": [...]}``

降级策略：
    - pycam_executable 未配置 → 降级到 manual
    - 包装器脚本文件不存在 → 降级到 manual
    - subprocess 返回非零退出码 → 降级到 manual（不抛错）
    - JSON 输出解析失败 → 降级到 manual
    - status="error" → 降级到 manual
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ._common import (
    CamSoftwareReport,
    _BaseBackend,
    _JSON_COLLISIONS_FIELD,
    _JSON_MESSAGES_FIELD,
    _JSON_STATUS_FIELD,
    logger,
)
from .manual import _ManualBackend


class _PyCamBackend(_BaseBackend):
    """调用 PyCAM 开源工具执行 G 代码刀轨校验。"""

    backend_name = "pycam"

    def __init__(self, pycam_executable: str) -> None:
        """初始化 PyCAM 后端。

        Args:
            pycam_executable: PyCAM 包装器脚本绝对路径
                （如 ``.../python/scripts/cam_adapters/pycam/autorun_gcode_check.py``），
                留空表示 PyCAM 不可用
        """
        self._pycam_executable = pycam_executable or ""

    def validate(
        self,
        gcode_file_path: str,
        controller_type: str,
    ) -> CamSoftwareReport:
        # 1. 检查 pycam_executable 配置
        if not self._pycam_executable:
            return self._degrade_to_manual(
                gcode_file_path,
                controller_type,
                "PyCAM 包装器脚本未配置（LNN_CAM_PYCAM_EXECUTABLE 为空），自动降级到 manual 后端。",
            )

        # 2. 检查包装器脚本文件是否存在
        # 不在此处预检 import pycam —— 包装器脚本内部完成 PyCAM 导入，
        # 无环境时返回 status="error"，由下方归一化逻辑统一降级到 manual。
        script_path = Path(self._pycam_executable)
        if not script_path.is_file():
            return self._degrade_to_manual(
                gcode_file_path,
                controller_type,
                f"PyCAM 包装器脚本文件不存在：{self._pycam_executable}。自动降级到 manual 后端。",
            )

        # 3. subprocess 调用 PyCAM 包装器脚本
        # 协议：python <script> <gcode_path> <controller_type>
        # 期望 stdout 输出 JSON：{"status": "pass"/"fail"/"error", "collisions": [...], "messages": [...]}
        try:
            result = subprocess.run(
                [sys.executable, str(script_path), gcode_file_path, controller_type],
                capture_output=True,
                text=True,
                timeout=600,  # PyCAM 校验耗时较长，留 10 分钟
                check=False,  # 不抛 CalledProcessError，手动检查 returncode
            )
        except subprocess.TimeoutExpired:
            return self._degrade_to_manual(
                gcode_file_path,
                controller_type,
                "PyCAM 子进程超时（>600s），自动降级到 manual 后端。",
            )
        except FileNotFoundError as e:
            return self._degrade_to_manual(
                gcode_file_path,
                controller_type,
                f"Python 解释器或 PyCAM 包装器脚本未找到：{e}。自动降级到 manual 后端。",
            )
        except subprocess.SubprocessError as e:
            return self._degrade_to_manual(
                gcode_file_path,
                controller_type,
                f"PyCAM subprocess 调用异常：{e}。自动降级到 manual 后端。",
            )

        # 4. 解析 JSON 输出
        if result.returncode != 0:
            # PyCAM 包装器返回非零退出码，解析 stderr 作为诊断消息
            stderr_msg = result.stderr.strip() if result.stderr else ""
            return self._degrade_to_manual(
                gcode_file_path,
                controller_type,
                f"PyCAM 包装器返回非零退出码（returncode={result.returncode}）："
                f"{stderr_msg[:500]}。自动降级到 manual 后端。",
            )

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            stdout_preview = (result.stdout or "")[:500]
            return self._degrade_to_manual(
                gcode_file_path,
                controller_type,
                f"PyCAM 输出 JSON 解析失败：{e}。stdout 预览：{stdout_preview}。自动降级到 manual 后端。",
            )

        # 5. 归一化字段
        status = str(payload.get(_JSON_STATUS_FIELD, "error")).lower()
        if status not in {"pass", "fail"}:
            status = "error"

        collisions = self._normalize_collisions(payload.get(_JSON_COLLISIONS_FIELD))
        messages = self._normalize_messages(payload.get(_JSON_MESSAGES_FIELD))

        # status=error 时降级到 manual
        if status == "error":
            return self._degrade_to_manual(
                gcode_file_path,
                controller_type,
                "PyCAM 报告 status=error：" + " | ".join(messages[:3]),
            )

        logger.info(
            "CamAdapter(pycam): 校验完成 status=%s, collisions=%d",
            status,
            len(collisions),
        )

        return CamSoftwareReport(
            status=status,
            backend_used="pycam",
            messages=messages,
            collisions=collisions,
            degraded=False,
            degradation_reason="",
            gcode_file_path=gcode_file_path,
            controller_type=controller_type,
            validation_timestamp=self._now_iso(),
            subprocess_returncode=result.returncode,
            manual_checklist_path="",
        )

    def _degrade_to_manual(
        self,
        gcode_file_path: str,
        controller_type: str,
        reason: str,
    ) -> CamSoftwareReport:
        """降级到 manual 后端（不抛错）。

        Args:
            gcode_file_path: G 代码文件路径
            controller_type: 控制器类型
            reason: 降级原因（写入 degradation_reason + messages）

        Returns:
            status="manual_pending" 的 CamSoftwareReport，
            backend_used="manual"，degraded=True
        """
        logger.warning("CamAdapter(pycam): 降级到 manual。原因：%s", reason)
        manual = _ManualBackend()
        report = manual.validate(gcode_file_path, controller_type)
        report.degraded = True
        report.degradation_reason = reason
        report.messages.insert(0, f"[PyCAM 降级] {reason}")
        return report
