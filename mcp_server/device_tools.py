"""设备元数据 → MCP 工具自动生成器（Phase 2：② A2M 思路）。

按 DeviceDescriptor 的 capabilities 自动生成 MCP 工具，而非手写每个设备工具：

- 每个 operation 生成 ``{device_id}_{op.name}`` 工具，参数签名由 param_schema 驱动
  （FastMCP 据此自动构造 JSON Schema）；
- 每个设备生成 ``{device_id}_read_status`` 状态回读工具；
- 参数越界 fail-closed 拒绝，错误以结构化文本返回（与 tools.py 统一格式一致）。

集成入口：``register_device_tools(server, descriptor)`` 在任意 FastMCP server 上
增量注册；``build_device_tool_handlers`` 供测试/非 MCP 场景直接调用。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from mcp_server.device_registry import (
    DeviceDescriptor,
    DeviceOperation,
    SimulatedDevice,
)

logger = logging.getLogger("lingjing-mcp")

# param_schema type Python 类型注解（FastMCP 生成 JSON Schema 用）
_PY_TYPE_MAP = {
    "number": "float",
    "integer": "int",
    "string": "str",
    "boolean": "bool",
}


def _fmt_success(data: dict[str, Any]) -> str:
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False)


def _fmt_error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


async def _execute_op(backend: SimulatedDevice, op: DeviceOperation, params: dict[str, Any]) -> str:
    """执行设备能力并格式化为文本响应（异常不抛出，转结构化错误）。"""
    try:
        result = backend.execute(op.name, params)
        return _fmt_success(result)
    except ValueError as exc:
        return _fmt_error(str(exc))
    except Exception as exc:  # noqa: BLE001 - 设备后端异常统一兜底
        logger.exception("device op %s failed", op.name)
        return _fmt_error(f"设备执行异常: {exc}")


def _make_op_handler(backend: SimulatedDevice, op: DeviceOperation) -> Callable[..., Any]:
    """按 param_schema 动态构造带显式签名的 async handler。

    使用 exec 生成显式参数签名（FastMCP 依赖函数签名生成工具 JSON Schema，
    不支持 **kwargs 展开）。参数名/类型/默认值均来自已校验的元数据。
    """
    arg_defs: list[str] = []
    for pname, pmeta in op.param_schema.items():
        typ = _PY_TYPE_MAP.get(pmeta.get("type", "string"), "Any")
        if pmeta.get("required"):
            arg_defs.append(f"{pname}: {typ}")
        else:
            default = pmeta.get("default")
            arg_defs.append(f"{pname}: {typ} = {default!r}")
    signature = ", ".join(arg_defs) if arg_defs else ""

    globals_ns: dict[str, Any] = {
        "_execute_op": _execute_op,
        "_backend": backend,
        "_op": op,
    }
    code = f"async def _h({signature}):\n    return await _execute_op(_backend, _op, locals())"
    exec(code, globals_ns, globals_ns)  # noqa: S102 - 元数据已白名单校验，非外部输入
    return globals_ns["_h"]


def _make_read_status_handler(backend: SimulatedDevice) -> Callable[[], Any]:
    async def _read_status() -> str:
        return _fmt_success(backend.status())

    return _read_status


def build_device_tool_handlers(
    descriptor: DeviceDescriptor,
    backend: SimulatedDevice | None = None,
) -> dict[str, Callable[..., Any]]:
    """构建 {tool_name: handler} 映射（供测试/直接调用）。

    Args:
        descriptor: 设备描述符。
        backend: 仿真后端（缺省自动创建）。

    Returns:
        工具名 → async handler 的映射。
    """
    descriptor.validate()
    backend = backend if backend is not None else SimulatedDevice(descriptor)
    handlers: dict[str, Callable[..., Any]] = {}
    prefix = descriptor.tool_prefix()
    for op in descriptor.operations:
        handlers[f"{prefix}_{op.name}"] = _make_op_handler(backend, op)
    handlers[f"{prefix}_read_status"] = _make_read_status_handler(backend)
    return handlers


def register_device_tools(
    server: Any,
    descriptor: DeviceDescriptor,
    backend: SimulatedDevice | None = None,
) -> list[str]:
    """在 FastMCP server 上按元数据自动注册设备工具（A2M 思路）。

    Args:
        server: FastMCP 实例（鸭子类型，需具备 ``.tool(name=, description=)`` 装饰器）。
        descriptor: 设备描述符。
        backend: 仿真后端（缺省自动创建）。

    Returns:
        注册的工具名列表。
    """
    handlers = build_device_tool_handlers(descriptor, backend)
    registered: list[str] = []
    for tool_name, handler in handlers.items():
        description = f"设备 {descriptor.name}（{descriptor.device_type}）自动生成工具"
        if tool_name.endswith("_read_status"):
            description = f"读取设备 {descriptor.name} 实时状态（信号快照）"
        else:
            op_name = tool_name.rsplit("_", 1)[-1]
            op = next((o for o in descriptor.operations if o.name == op_name), None)
            if op is not None:
                description = f"{descriptor.name}: {op.description}"
        server.tool(name=tool_name, description=description)(handler)
        registered.append(tool_name)

    logger.info(
        "设备 %s 已自动注册 %d 个 MCP 工具: %s",
        descriptor.device_id,
        len(registered),
        registered,
    )
    return registered


__all__ = [
    "build_device_tool_handlers",
    "register_device_tools",
]
