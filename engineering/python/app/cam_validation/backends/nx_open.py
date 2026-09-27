"""NX Open 后端：subprocess 调用 NX Open Python 脚本（P1-3 拆分自原 cam_adapter.py）。

NX Open 是工业级 CAM 软件，需要许可证。
通过 subprocess + JSON 解耦，避免 NX SDK 升级破坏 cam_validation 模块。

降级策略：
    - nx_open_executable 为空 → 降级到 manual
    - subprocess 超时 → 降级到 manual
    - subprocess 返回非零退出码 → 降级到 manual
    - JSON 输出解析失败 → 降级到 manual
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


class _NxOpenBackend(_BaseBackend):
    """subprocess 调用 Siemens NX Open Python 脚本执行刀轨仿真。"""

    backend_name = "nx_open"

    def __init__(self, nx_open_executable: str) -> None:
        """初始化 NX Open 后端。

        Args:
            nx_open_executable: NX Open Python 脚本路径（如
                "C:/NX/autorun_gcode_check.py"），留空表示不可用
        """
        self._nx_open_executable = nx_open_executable or ""

    def validate(
        self,
        gcode_file_path: str,
        controller_type: str,
    ) -> CamSoftwareReport:
        # 1. 检查 nx_open_executable 配置
        if not self._nx_open_executable:
            return self._degrade_to_manual(
                gcode_file_path,
                controller_type,
                "NX Open 可执行脚本未配置（LNN_CAM_NX_OPEN_EXECUTABLE 为空），自动降级到 manual 后端。",
            )

        # 2. 检查脚本文件是否存在
        script_path = Path(self._nx_open_executable)
        if not script_path.is_file():
            return self._degrade_to_manual(
                gcode_file_path,
                controller_type,
                f"NX Open 脚本文件不存在：{self._nx_open_executable}。自动降级到 manual 后端。",
            )

        # 3. subprocess 调用 NX Open
        # 协议：python <script> <gcode_path> <controller_type>
        # 期望 stdout 输出 JSON：{"status": "pass"/"fail", "collisions": [...], "messages": [...]}
        try:
            result = subprocess.run(
                [sys.executable, str(script_path), gcode_file_path, controller_type],
                capture_output=True,
                text=True,
                timeout=600,  # NX Open 仿真耗时较长，留 10 分钟
                check=False,
            )
        except subprocess.TimeoutExpired:
            return self._degrade_to_manual(
                gcode_file_path,
                controller_type,
                "NX Open 子进程超时（>600s），自动降级到 manual 后端。",
            )
        except FileNotFoundError as e:
            return self._degrade_to_manual(
                gcode_file_path,
                controller_type,
                f"Python 解释器或 NX Open 脚本未找到：{e}。自动降级到 manual 后端。",
            )
        except subprocess.SubprocessError as e:
            return self._degrade_to_manual(
                gcode_file_path,
                controller_type,
                f"NX Open subprocess 调用异常：{e}。自动降级到 manual 后端。",
            )

        # 4. 解析 JSON 输出
        if result.returncode != 0:
            stderr_msg = result.stderr.strip() if result.stderr else ""
            return self._degrade_to_manual(
                gcode_file_path,
                controller_type,
                f"NX Open 返回非零退出码（returncode={result.returncode}）："
                f"{stderr_msg[:500]}。自动降级到 manual 后端。",
            )

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            stdout_preview = (result.stdout or "")[:500]
            return self._degrade_to_manual(
                gcode_file_path,
                controller_type,
                f"NX Open 输出 JSON 解析失败：{e}。stdout 预览：{stdout_preview}。自动降级到 manual 后端。",
            )

        # 5. 归一化字段
        status = str(payload.get(_JSON_STATUS_FIELD, "error")).lower()
        if status not in {"pass", "fail"}:
            status = "error"

        collisions = self._normalize_collisions(payload.get(_JSON_COLLISIONS_FIELD))
        messages = self._normalize_messages(payload.get(_JSON_MESSAGES_FIELD))

        if status == "error":
            return self._degrade_to_manual(
                gcode_file_path,
                controller_type,
                "NX Open 报告 status=error：" + " | ".join(messages[:3]),
            )

        logger.info(
            "CamAdapter(nx_open): 校验完成 status=%s, collisions=%d",
            status,
            len(collisions),
        )

        return CamSoftwareReport(
            status=status,
            backend_used="nx_open",
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
        """降级到 manual 后端（不抛错）。"""
        logger.warning("CamAdapter(nx_open): 降级到 manual。原因：%s", reason)
        manual = _ManualBackend()
        report = manual.validate(gcode_file_path, controller_type)
        report.degraded = True
        report.degradation_reason = reason
        report.messages.insert(0, f"[NX Open 降级] {reason}")
        return report
