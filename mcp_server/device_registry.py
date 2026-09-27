"""设备元数据模型（AAS 风格）+ 仿真设备后端（Phase 2：② A2M 思路）。

借鉴 A2M 的 AAS（Asset Administration Shell）思路：
- 用结构化元数据描述设备（能力 Operation 子模型 + 状态 Signal 子模型）；
- 由元数据自动生成 MCP 工具（device_tools.py），而不是手写每个设备工具；
- 用户无真实产线（确认走仿真），故提供 SimulatedDevice 内存状态机后端，
  让「元数据 → 工具 → 执行 → 状态回读」闭环在本机完全可跑可测。

安全约定（继承项目 MCP 输入校验传统）：
- device_id / operation 名走白名单正则，拒绝路径遍历与特殊字符；
- 参数 schema 显式声明 type/min/max/required，执行时越界即拒绝（fail-closed）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 白名单
_DEVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_OP_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SIGNAL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ALLOWED_PARAM_TYPES = {"number", "integer", "string", "boolean"}


class DeviceDescriptorError(ValueError):
    """设备元数据非法。"""


@dataclass
class DeviceOperation:
    """设备能力描述（AAS Operation 子模型简化）。"""

    name: str
    description: str
    param_schema: dict[str, dict[str, Any]] = field(default_factory=dict)
    returns: str = "设备运行状态摘要"


@dataclass
class DeviceSignal:
    """设备状态信号（AAS Submodel 状态信号简化）。"""

    name: str
    unit: str = ""
    kind: str = "number"  # number / string / boolean


@dataclass
class DeviceDescriptor:
    """设备描述符（AAS Asset Administration Shell 简化模型）。

    Attributes:
        device_id: 设备唯一 ID（小写字母数字开头，仅 a-z0-9_-，长度 ≤64）。
        name: 设备显示名。
        device_type: 设备类型（cnc_milling / sensor / robot ...）。
        controller: 控制器类型（fanuc_0i / siemens_840d ...）。
        operations: 能力列表（自动生成同名 MCP 工具）。
        signals: 状态信号列表（自动生成 {device_id}_read_status 工具）。
        metadata: 附加元数据（厂商/型号/位置等）。
    """

    device_id: str
    name: str
    device_type: str = "generic"
    controller: str = ""
    operations: list[DeviceOperation] = field(default_factory=list)
    signals: list[DeviceSignal] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        """元数据合法性校验（fail-closed）。

        Raises:
            DeviceDescriptorError: 任一字段非法。
        """
        if not self.device_id or not _DEVICE_ID_RE.match(self.device_id):
            raise DeviceDescriptorError(f"device_id 非法: {self.device_id!r}（须 ^[a-z0-9][a-z0-9_-]{{0,63}}$）")
        if not self.name or len(self.name) > 128:
            raise DeviceDescriptorError(f"设备 name 非法: {self.name!r}（1~128 字符）")

        seen_ops: set[str] = set()
        for op in self.operations:
            if not op.name or not _OP_NAME_RE.match(op.name):
                raise DeviceDescriptorError(f"operation 名非法: {op.name!r}（须 ^[a-z][a-z0-9_]{{0,63}}$）")
            if op.name in seen_ops:
                raise DeviceDescriptorError(f"operation 名重复: {op.name}")
            seen_ops.add(op.name)
            if not op.description or len(op.description) > 512:
                raise DeviceDescriptorError(f"operation {op.name} description 非法")
            for pname, pmeta in op.param_schema.items():
                if not _OP_NAME_RE.match(pname):
                    raise DeviceDescriptorError(f"operation {op.name} 参数名非法: {pname!r}")
                ptype = pmeta.get("type")
                if ptype not in _ALLOWED_PARAM_TYPES:
                    raise DeviceDescriptorError(
                        f"operation {op.name} 参数 {pname} type 非法: {ptype!r}（允许: {sorted(_ALLOWED_PARAM_TYPES)}）"
                    )
                if ptype in ("number", "integer"):
                    lo, hi = pmeta.get("min"), pmeta.get("max")
                    if lo is not None and hi is not None and lo > hi:
                        raise DeviceDescriptorError(f"operation {op.name} 参数 {pname} min>max（{lo}>{hi}）")

        seen_sigs: set[str] = set()
        for sig in self.signals:
            if not sig.name or not _SIGNAL_NAME_RE.match(sig.name):
                raise DeviceDescriptorError(f"信号名非法: {sig.name!r}")
            if sig.name in seen_sigs:
                raise DeviceDescriptorError(f"信号名重复: {sig.name}")
            seen_sigs.add(sig.name)
            if len(sig.unit) > 32:
                raise DeviceDescriptorError(f"信号 {sig.name} unit 过长")

    def tool_prefix(self) -> str:
        """MCP 工具名前缀（device_id 直接可用，白名单已保证安全）。"""
        return self.device_id


# 仿真设备后端（用户确认：闭环走仿真，无真实产线）
class SimulatedDevice:
    """内存状态机设备后端：让元数据驱动的工具在本机可执行可回读。

    - execute(op_name, params)：按 schema 校验参数（越界 fail-closed）→ 更新状态
    - read_signal(name) / status()：状态回读
    """

    def __init__(self, descriptor: DeviceDescriptor) -> None:
        descriptor.validate()
        self.descriptor = descriptor
        # 状态初始化：数值信号 0.0，字符串信号 ""
        self._state: dict[str, Any] = {}
        for sig in descriptor.signals:
            if sig.kind == "string":
                self._state[sig.name] = ""
            elif sig.kind == "boolean":
                self._state[sig.name] = False
            else:
                self._state[sig.name] = 0.0

    def _validate_params(self, op: DeviceOperation, params: dict[str, Any]) -> dict[str, Any]:
        """按 op.param_schema 校验并规范化参数（fail-closed）。"""
        cleaned: dict[str, Any] = {}
        for pname, pmeta in op.param_schema.items():
            ptype = pmeta.get("type", "string")
            required = bool(pmeta.get("required", False))
            if pname not in params or params[pname] is None:
                if required:
                    raise ValueError(f"{op.name}: 缺少必填参数 {pname}")
                continue
            raw = params[pname]
            val: Any
            try:
                if ptype == "number":
                    val = float(raw)
                elif ptype == "integer":
                    val = int(raw)
                elif ptype == "boolean":
                    val = bool(raw)
                else:
                    val = str(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{op.name}: 参数 {pname} 类型错误（期望 {ptype}）")
            # 数值边界
            if ptype in ("number", "integer"):
                lo = pmeta.get("min")
                hi = pmeta.get("max")
                if lo is not None and val < lo:
                    raise ValueError(f"{op.name}: 参数 {pname}={val} 低于下限 {lo}（fail-closed 拒绝）")
                if hi is not None and val > hi:
                    raise ValueError(f"{op.name}: 参数 {pname}={val} 超过上限 {hi}（fail-closed 拒绝）")
            # 字符串长度
            if ptype == "string" and len(val) > 256:
                raise ValueError(f"{op.name}: 参数 {pname} 超过 256 字符")
            cleaned[pname] = val
        return cleaned

    def execute(self, op_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """执行设备能力（模拟）。

        Args:
            op_name: operation 名。
            params: 参数 dict。

        Returns:
            {"op": op_name, "state": {...}, "message": "..."}

        Raises:
            ValueError: 未知能力 / 参数越界（fail-closed）。
        """
        params = params or {}
        op = next((o for o in self.descriptor.operations if o.name == op_name), None)
        if op is None:
            raise ValueError(
                f"设备 {self.descriptor.device_id} 无能力 {op_name!r}（可用: "
                f"{[o.name for o in self.descriptor.operations]}）"
            )

        cleaned = self._validate_params(op, params)

        # 模拟执行：参数中与信号同名的值写入状态
        applied: dict[str, Any] = {}
        for pname, pval in cleaned.items():
            if pname in self._state:
                self._state[pname] = pval
                applied[pname] = pval
        # 无对应信号的参数记录为"已受理"（模拟完成）
        for pname, pval in cleaned.items():
            if pname not in applied:
                applied[pname] = pval

        logger.info("设备 %s 执行 %s params=%s", self.descriptor.device_id, op_name, applied)
        return {
            "op": op_name,
            "device_id": self.descriptor.device_id,
            "applied": applied,
            "state": dict(self._state),
            "message": f"{op_name} 执行完成（仿真）",
        }

    def read_signal(self, name: str) -> Any:
        if name not in self._state:
            raise ValueError(f"设备 {self.descriptor.device_id} 无信号 {name!r}")
        return self._state[name]

    def set_signal(self, name: str, value: Any) -> None:
        """外部写入信号（工厂物理耦合仿真用，如振动传感器读数）。

        Args:
            name: 信号名。
            value: 信号值（按信号 kind 做类型强制）。

        Raises:
            ValueError: 信号不存在。
        """
        if name not in self._state:
            raise ValueError(f"设备 {self.descriptor.device_id} 无信号 {name!r}")
        sig = next((s for s in self.descriptor.signals if s.name == name), None)
        if sig is not None:
            if sig.kind == "number":
                value = float(value)
            elif sig.kind == "boolean":
                value = bool(value)
            else:
                value = str(value)
        self._state[name] = value

    def status(self) -> dict[str, Any]:
        return {
            "device_id": self.descriptor.device_id,
            "name": self.descriptor.name,
            "device_type": self.descriptor.device_type,
            "controller": self.descriptor.controller,
            "state": dict(self._state),
        }


# 演示设备注册表（Phase 2 演示 + 测试用；Phase 3b 仿真工厂将扩展）
def build_cnc_milling_descriptor() -> DeviceDescriptor:
    """一台仿真数控铣床（fanuc_0i）。"""
    return DeviceDescriptor(
        device_id="cnc_mill_01",
        name="仿真立式加工中心 VMC-850",
        device_type="cnc_milling",
        controller="fanuc_0i",
        operations=[
            DeviceOperation(
                name="start_spindle",
                description="启动主轴旋转（S 值由 spindle_rpm 指定）",
                param_schema={"spindle_rpm": {"type": "number", "min": 50.0, "max": 24000.0, "required": True}},
                returns="主轴启动确认 + 当前转速",
            ),
            DeviceOperation(
                name="stop_spindle",
                description="停止主轴旋转",
                param_schema={},
                returns="主轴停止确认",
            ),
            DeviceOperation(
                name="move_axis",
                description="移动坐标轴到指定位置（软限位内）",
                param_schema={
                    "x": {"type": "number", "min": -1000.0, "max": 1000.0, "required": True},
                    "y": {"type": "number", "min": -1000.0, "max": 1000.0, "required": True},
                    "z": {"type": "number", "min": -500.0, "max": 500.0, "required": True},
                },
                returns="轴位置确认",
            ),
            DeviceOperation(
                name="set_feed_rate",
                description="设置进给速度（mm/min）",
                param_schema={"feed_rate": {"type": "number", "min": 10.0, "max": 20000.0, "required": True}},
                returns="进给速度确认",
            ),
        ],
        signals=[
            DeviceSignal("spindle_rpm", "rpm", "number"),
            DeviceSignal("spindle_on", "", "boolean"),
            DeviceSignal("x", "mm", "number"),
            DeviceSignal("y", "mm", "number"),
            DeviceSignal("z", "mm", "number"),
            DeviceSignal("feed_rate", "mm/min", "number"),
        ],
        metadata={"vendor": "SimuCNC", "model": "VMC-850E", "location": "仿真车间-01"},
    )


def build_vibration_sensor_descriptor() -> DeviceDescriptor:
    """一台仿真振动传感器（颤振监测）。"""
    return DeviceDescriptor(
        device_id="vib_sensor_01",
        name="仿真加速度计（铣削颤振监测）",
        device_type="sensor",
        controller="",
        operations=[
            DeviceOperation(
                name="reset",
                description="清零振动信号缓冲",
                param_schema={},
                returns="缓冲清零确认",
            )
        ],
        signals=[
            DeviceSignal("vibration_peak", "m/s^2", "number"),
            DeviceSignal("chatter_level", "", "string"),
        ],
        metadata={"vendor": "SimuSensor", "model": "ACC-100", "location": "仿真车间-01"},
    )


def build_demo_registry() -> list[DeviceDescriptor]:
    """返回 Phase 2 演示设备列表（后续 Phase 3b 扩展为完整仿真工厂）。"""
    return [
        build_cnc_milling_descriptor(),
        build_vibration_sensor_descriptor(),
    ]


__all__ = [
    "DeviceDescriptor",
    "DeviceOperation",
    "DeviceSignal",
    "DeviceDescriptorError",
    "SimulatedDevice",
    "build_cnc_milling_descriptor",
    "build_vibration_sensor_descriptor",
    "build_demo_registry",
]
