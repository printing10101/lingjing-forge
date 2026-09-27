"""CAM 后端共享基础设施（P1-3 拆分自原 cam_adapter.py）。

本模块提供 5 个子后端共享的：
    - 常量：``_VALID_BACKENDS`` / ``_VALID_STATUSES`` / ``_JSON_*_FIELD``
    - 数据类：``CamSoftwareReport``（CAM 软件校验归一化报告）
    - 抽象基类：``_BaseBackend``（子后端统一接口 + 归一化工具方法）
    - 模块 logger

设计原则：
    - 所有子后端 + dispatcher 通过 ``from ._common import ...`` 共享这些定义
    - ``CamSoftwareReport`` 是阶段 7 的公开数据类，需保持向后兼容
      （原 ``from app.cam_validation.cam_adapter import CamSoftwareReport``
       仍可用，由 cam_adapter.py re-export shim 保障）
    - ``_BaseBackend`` 为抽象基类，子类必须实现 ``validate`` 方法

项目记忆硬约束：
    - CAM 软件调用通过 subprocess，系统绝不直接接口 CNC 控制器
    - NX Open / PowerMill SDK 升级不破坏 cam_validation 模块（subprocess + JSON 解耦）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("app.cam_validation.cam_adapter")


# 常量

# 合法 CAM 后端集合（与 CamValidationConfig.__post_init__ 对齐）
_VALID_BACKENDS: frozenset[str] = frozenset(
    {
        "internal_only",
        "pycam",
        "nx_open",
        "powermill",
        "manual",
    }
)

# CAM 软件校验状态枚举
# - skipped：跳过 CAM 软件二次校验（internal_only 后端）
# - pass：CAM 软件校验通过，无碰撞
# - fail：CAM 软件校验失败，发现碰撞
# - manual_pending：等待工程师手动回填校验结果（manual 后端）
# - error：CAM 软件调用异常（subprocess 失败 / JSON 解析失败）
_VALID_STATUSES: frozenset[str] = frozenset(
    {
        "skipped",
        "pass",
        "fail",
        "manual_pending",
        "error",
    }
)

# NX Open / PowerMill subprocess 输出 JSON 报告的字段名约定
# 子后端 subprocess 协议：
# argv = [executable, gcode_file_path, controller_type]
# stdout 输出 JSON：{"status": "pass"/"fail"/"error", "collisions": [...], "messages": [...]}
_JSON_STATUS_FIELD: str = "status"
_JSON_COLLISIONS_FIELD: str = "collisions"
_JSON_MESSAGES_FIELD: str = "messages"


# CamSoftwareReport：CAM 软件校验结果归一化


@dataclass
class CamSoftwareReport:
    """CAM 软件二次校验归一化报告。

    封装 5 个子后端（internal_only / pycam / nx_open / powermill / manual）
    的校验输出为统一结构，供 pipeline.py 写入 cam_software_report 字段
    并合并到 cam_report.json。

    Attributes:
        status: 校验状态（skipped / pass / fail / manual_pending / error）
        backend_used: 实际使用的 CAM 后端名称（可能与请求的 cam_backend 不同，
            如 pycam 不可用降级到 manual 时，backend_used="manual"）
        messages: CAM 软件返回的诊断消息列表（含降级原因 + 校验结论）
        collisions: CAM 软件返回的碰撞事件列表（每条 dict 含 collision_type /
            block_number / message 等字段，由 NX/PowerMill/PyCAM 输出）
        degraded: 是否发生降级（True 表示请求的后端不可用，已降级到 manual）
        degradation_reason: 降级原因（如 "PyCAM 模块不可用：ImportError"），
            未降级时为空字符串
        gcode_file_path: 被校验的 G 代码文件绝对路径
        controller_type: 目标控制器类型（fanuc / siemens / heidenhain）
        validation_timestamp: 校验完成时间戳（ISO 8601 UTC，秒级精度）
        subprocess_returncode: subprocess 退出码（仅 nx_open / powermill 后端
            有意义；其他后端为 None）
        manual_checklist_path: 手动校验清单 markdown 路径（仅 manual 后端
            生成，其他后端为空字符串）
    """

    status: str = "skipped"
    backend_used: str = "internal_only"
    messages: list[str] = field(default_factory=list)
    collisions: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    degradation_reason: str = ""
    gcode_file_path: str = ""
    controller_type: str = "fanuc"
    validation_timestamp: str = ""
    subprocess_returncode: int | None = None
    manual_checklist_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，供 cam_report.json 最终导出。"""
        return {
            "status": self.status,
            "backend_used": self.backend_used,
            "messages": list(self.messages),
            "collisions": list(self.collisions),
            "degraded": self.degraded,
            "degradation_reason": self.degradation_reason,
            "gcode_file_path": self.gcode_file_path,
            "controller_type": self.controller_type,
            "validation_timestamp": self.validation_timestamp,
            "subprocess_returncode": self.subprocess_returncode,
            "manual_checklist_path": self.manual_checklist_path,
        }

    @property
    def safe(self) -> bool:
        """综合安全判定。

        安全当且仅当：
            - status == "skipped"（仅内部预校验，告知文本标注未二次校验）
            - status == "pass"（CAM 软件明确返回通过）
            - status == "manual_pending"（等待工程师手动回填，pipeline 暂不判定失败）

        失败：status in {"fail", "error"}
        """
        return self.status in {"skipped", "pass", "manual_pending"}


# 子后端抽象基类


class _BaseBackend:
    """CAM 后端抽象基类。

    所有子后端实现统一接口：
        validate(gcode_file_path, controller_type) -> CamSoftwareReport

    子类不应抛出 CamAdapterError（除 _BaseBackend.validate 顶层兜底），
    降级场景通过返回 degraded=True 的 CamSoftwareReport 表达。
    """

    backend_name: str = "base"

    def validate(
        self,
        gcode_file_path: str,
        controller_type: str,
    ) -> CamSoftwareReport:
        """执行 CAM 软件校验，返回归一化报告。

        Args:
            gcode_file_path: G 代码文件绝对路径
            controller_type: 目标控制器类型

        Returns:
            CamSoftwareReport 归一化报告
        """
        raise NotImplementedError("子类必须实现 validate()")

    @staticmethod
    def _now_iso() -> str:
        """当前 UTC 时间 ISO 8601 字符串（秒级精度）。"""
        from app.utils.time import utcnow_seconds_iso_z

        return utcnow_seconds_iso_z()

    @staticmethod
    def _normalize_collisions(
        raw: Any,
    ) -> list[dict[str, Any]]:
        """归一化 CAM 软件返回的碰撞事件列表为 list[dict]。

        NX/PowerMill/PyCAM 各自输出格式可能不同，本方法做防御性归一化：
            - None → []
            - list[dict] → 原样返回（每条已为 dict）
            - list[str] → 包装为 [{"message": s}]
            - 其他类型 → 包装为 [{"raw": repr(raw)}]
        """
        if raw is None:
            return []
        if isinstance(raw, list):
            result: list[dict[str, Any]] = []
            for item in raw:
                if isinstance(item, dict):
                    result.append(item)
                elif isinstance(item, str):
                    result.append({"message": item})
                else:
                    result.append({"raw": str(item)})
            return result
        # 非列表：包装为单元素列表
        return [{"raw": str(raw)}]

    @staticmethod
    def _normalize_messages(
        raw: Any,
    ) -> list[str]:
        """归一化消息字段为 list[str]。"""
        if raw is None:
            return []
        if isinstance(raw, list):
            return [str(m) for m in raw]
        if isinstance(raw, str):
            return [raw]
        return [str(raw)]
