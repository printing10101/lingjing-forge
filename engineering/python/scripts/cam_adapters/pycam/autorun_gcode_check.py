"""PyCAM Python 脚本：G 代码刀轨校验（阶段 7 CAM 二次校验）。

对应 ADR-018 第 9 节 ``CamAdapter`` 的 ``pycam`` 后端 subprocess 协议。

调用协议
--------
本脚本由 ``app.cam_validation.cam_adapter._PyCamBackend`` 通过 subprocess 调用：

.. code-block:: bash

    python <this_script.py> <gcode_file_path> <controller_type>

参数
----
- ``gcode_file_path`` (argv[1])：G 代码文件绝对路径（.nc / .mpf / .h）
- ``controller_type`` (argv[2])：目标控制器类型
  （fanuc_0i / siemens_840d / heidenhain_tnc / xmachine_xm100）

输出
----
向 stdout 输出**单行 JSON**，符合 ``cam_adapter.py`` 期望的协议：

.. code-block:: json

    {
      "status": "pass" | "fail" | "error",
      "collisions": [
        {
          "collision_type": "workspace_limit" | "safe_z_violation" | "rapid_in_material" | "tool_workpiece",
          "block_number": 42,
          "message": "刀轨在 N42 处超出工作空间 X+ 边界",
          "severity": "critical" | "warning"
        }
      ],
      "messages": ["PyCAM 校验完成", "刀轨总段数: 128"]
    }

- ``status="pass"``：刀轨未发现 PyCAM 能力范围内的风险，可上机（仍需工程师审核）
- ``status="fail"``：发现风险，禁止上机
- ``status="error"``：PyCAM 调用异常（模块不可用 / 文件加载失败），
  ``cam_adapter.py`` 会自动降级到 manual 后端

退出码
------
- 0：校验正常完成（无论 status 是 pass 还是 fail）
- 非 0：脚本自身异常（参数缺失 / PyCAM 不可用 / JSON 序列化失败），
  ``cam_adapter.py`` 会读取 stderr 并降级到 manual

PyCAM 能力边界（诚实标注）
----------------------------
PyCAM 0.6.x 是**刀轨生成器**（从 DXF/STL 几何生成 G 代码），不是完整的
G 代码仿真器。其 ``Importers/`` 仅支持 DXF/STL/SVG/PS，**不支持 G 代码导入**。
因此本包装器实现的校验项为 PyCAM 能力范围内的基础几何检查：

1. **工作空间边界检查**：刀轨点位是否超出机床工作空间（X/Y/Z 行程）
2. **安全 Z 检查**：Z 坐标是否低于安全平面（可能导致刀具-工件/夹具碰撞）
3. **G0 快速移动在材料内部检测**：G0 移动时 Z 低于安全 Z（高风险）
4. **刀轨连续性检查**：相邻点位距离异常（可能漏写坐标）

**PyCAM 不支持**的检测项（需 NX Open / PowerMill 等工业级 CAM 软件）：
- 刀柄-工件碰撞（toolholder_workpiece）
- 刀具-夹具碰撞（tool_fixture）
- 完整的材料切除仿真
- 机床运动学仿真（5 轴、多轴）

这些限制会在 ``messages`` 中明确标注，工程师应结合 manual 校验清单或
NX Open / PowerMill 后端补充检测。

工程边界（项目记忆硬约束）
----------------------------
- 本脚本绝不直接接口 CNC 控制器，仅输出 JSON 报告
- 物理机床执行由持证操作员 + 导师签字 + 保险流程独立推进
- 本脚本是「PyCAM 能力范围内的基础校验」，不替代完整 CAM 软件仿真

PyCAM 环境要求
---------------
- Python 3.8+
- PyCAM 0.6+（``pip install pycam``）
- 无需 GUI 依赖（本脚本仅使用 PyCAM 核心数据结构，不导入 Gtk/OpenGL）

部署说明
--------
1. PyCAM 已安装：``pip install pycam``
2. 在 ``.env`` 中配置::

    LNN_CAM_PYCAM_EXECUTABLE=<项目路径>/python/scripts/cam_adapters/pycam/autorun_gcode_check.py

3. 测试：``python autorun_gcode_check.py <test.nc> fanuc_0i``

无 PyCAM 环境时的行为
-------------------
若 ``import pycam`` 失败（未安装），脚本输出::

    {"status": "error", "collisions": [], "messages": ["PyCAM 不可用：ImportError: No module named 'pycam'"]}

``cam_adapter.py`` 收到 status="error" 后自动降级到 manual 后端，链路不中断。
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 输出协议常量（与 cam_adapter.py _JSON_*_FIELD 对齐）

_JSON_STATUS_FIELD = "status"
_JSON_COLLISIONS_FIELD = "collisions"
_JSON_MESSAGES_FIELD = "messages"

_VALID_STATUSES = frozenset({"pass", "fail", "error"})

# 控制器类型 后处理器名称映射（与 NX Open 脚本对齐）
_CONTROLLER_TO_POSTPROCESSOR: dict[str, str] = {
    "fanuc_0i": "fanuc_0i",
    "siemens_840d": "siemens_840d",
    "heidenhain_tnc": "heidenhain_tnc",
    "xmachine_xm100": "xmachine_xm100",
}

# 工作空间与安全参数（可通过环境变量覆盖）

# 默认机床工作空间（mm），对应典型 3 轴 VMC（如 VMC750）
# 实际部署应通过环境变量按车间机床配置
_DEFAULT_WORKSPACE_X = float(os.environ.get("PYCAM_WORKSPACE_X", "500.0"))
_DEFAULT_WORKSPACE_Y = float(os.environ.get("PYCAM_WORKSPACE_Y", "400.0"))
_DEFAULT_WORKSPACE_Z = float(os.environ.get("PYCAM_WORKSPACE_Z", "300.0"))

# 默认安全 Z 平面（mm，工件坐标系 Z+ 方向，高于工件顶面）
_DEFAULT_SAFE_Z = float(os.environ.get("PYCAM_SAFE_Z", "50.0"))

# G0 快速移动最大允许 Z（低于此值且 Z < safe_z 视为风险）
# G0 在材料内部是高风险行为，可能导致刀具崩裂
_DEFAULT_RAPID_MAX_Z = float(os.environ.get("PYCAM_RAPID_MAX_Z", "5.0"))

# 相邻点位最小距离（mm），低于此值视为异常（可能漏写坐标）
_DEFAULT_MIN_MOVE_DISTANCE = float(os.environ.get("PYCAM_MIN_MOVE_DISTANCE", "0.001"))


# 输出工具函数


def _emit_result(
    status: str,
    collisions: list[dict[str, Any]] | None = None,
    messages: list[str] | None = None,
) -> int:
    """向 stdout 输出单行 JSON 结果，返回退出码。

    Args:
        status: 校验状态（pass / fail / error）
        collisions: 碰撞事件列表（每条 dict）
        messages: 诊断消息列表

    Returns:
        0（status=pass/fail）或 1（status=error）
    """
    if status not in _VALID_STATUSES:
        status = "error"

    payload = {
        _JSON_STATUS_FIELD: status,
        _JSON_COLLISIONS_FIELD: collisions or [],
        _JSON_MESSAGES_FIELD: messages or [],
    }

    try:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False))
        sys.stdout.write("\n")
        sys.stdout.flush()
    except Exception as exc:
        sys.stderr.write(f"PyCAM 脚本 JSON 输出失败: {exc}\npayload: {payload}\n")
        return 1

    return 0 if status in {"pass", "fail"} else 1


def _now_iso() -> str:
    """当前 UTC 时间 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# G 代码解析器（轻量实现，PyCAM 无原生 G 代码 Importer）


# G 代码行正则：匹配 NXX GXX X.. Y.. Z.. F.. S.. 等格式
# 支持行号 N、G 代码 G、坐标 X/Y/Z、进给 F、主轴转速 S
_GCODE_LINE_RE = re.compile(
    r"(?i)"
    r"(?P<block>N\d+\s*)?"  # 可选行号
    r"(?P<gcode>G\d+(?:\.\d+)?)?"  # G 代码
    r"(?:.*?)"  # 中间任意字符（非贪婪）
    r"(?P<x>X\s*[-+]?\d*\.?\d+)?"  # X 坐标
    r"(?:.*?)(?P<y>Y\s*[-+]?\d*\.?\d+)?"  # Y 坐标
    r"(?:.*?)(?P<z>Z\s*[-+]?\d*\.?\d+)?"  # Z 坐标
    r"(?:.*?)(?P<f>F\s*[-+]?\d*\.?\d+)?"  # 进给率
)

# 注释行匹配（括号注释 ; 括号注释）
_COMMENT_PAREN_RE = re.compile(r"\([^)]*\)")
_COMMENT_SEMI_RE = re.compile(r";.*$")


class GcodeMove:
    """G 代码单条移动指令的归一化表示。

    Attributes:
        block_number: 行号（N 后数字），无行号时为 -1
        gcode: G 代码字符串（如 "G0" / "G1" / "G2" / "G3"）
        x, y, z: 目标坐标（绝对坐标，mm）；None 表示该轴未变化
        feed_rate: 进给率（mm/min），None 表示未指定
        raw_line: 原始 G 代码行（用于错误定位）
        line_index: 行索引（0-based，用于错误定位）
    """

    __slots__ = (
        "block_number",
        "gcode",
        "x",
        "y",
        "z",
        "feed_rate",
        "raw_line",
        "line_index",
    )

    def __init__(
        self,
        block_number: int,
        gcode: str,
        x: float | None,
        y: float | None,
        z: float | None,
        feed_rate: float | None,
        raw_line: str,
        line_index: int,
    ) -> None:
        self.block_number = block_number
        self.gcode = gcode
        self.x = x
        self.y = y
        self.z = z
        self.feed_rate = feed_rate
        self.raw_line = raw_line
        self.line_index = line_index


def _parse_gcode_line(line: str, line_index: int) -> GcodeMove | None:
    """解析单行 G 代码，返回 GcodeMove 或 None（空行/注释）。

    Args:
        line: G 代码原始行
        line_index: 行索引（0-based）

    Returns:
        GcodeMove 实例或 None
    """
    # 去除注释
    line_clean = _COMMENT_PAREN_RE.sub("", line)
    line_clean = _COMMENT_SEMI_RE.sub("", line_clean)
    line_clean = line_clean.strip()

    if not line_clean:
        return None

    # 跳过非 G 代码行（M 代码、T 代码、% 等）
    # 但保留含坐标的行（可能是 G0/G1 的省略写法）
    upper = line_clean.upper()
    if not any(upper.startswith(g) for g in ("N", "G", "X", "Y", "Z")) and not any(
        axis in upper for axis in ("X", "Y", "Z")
    ):
        return None

    # 提取行号
    block_number = -1
    block_match = re.match(r"\s*N(\d+)", line_clean)
    if block_match:
        block_number = int(block_match.group(1))

    # 提取 G 代码
    gcode = ""
    gcode_match = re.search(r"(?i)\bG(\d+(?:\.\d+)?)", line_clean)
    if gcode_match:
        gcode = f"G{gcode_match.group(1).upper()}"

    # 提取坐标
    def _extract_coord(axis: str) -> float | None:
        m = re.search(rf"(?i)\b{axis}\s*([-+]?\d*\.?\d+)", line_clean)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return None
        return None

    x = _extract_coord("X")
    y = _extract_coord("Y")
    z = _extract_coord("Z")

    # 提取进给率
    f_match = re.search(r"(?i)\bF\s*([-+]?\d*\.?\d+)", line_clean)
    feed_rate = float(f_match.group(1)) if f_match else None

    # 如果没有任何坐标和 G 代码，跳过
    if not gcode and x is None and y is None and z is None:
        return None

    # 默认 G 代码（无 G 代码但有坐标时，视为 G1 模式继承）
    if not gcode:
        gcode = "G1"  # 多数控制器在 G1 模式下可省略 G1

    return GcodeMove(
        block_number=block_number,
        gcode=gcode,
        x=x,
        y=y,
        z=z,
        feed_rate=feed_rate,
        raw_line=line_clean,
        line_index=line_index,
    )


def parse_gcode_file(gcode_file_path: str) -> tuple[list[GcodeMove], list[str]]:
    """解析 G 代码文件，返回移动序列和诊断消息。

    Args:
        gcode_file_path: G 代码文件绝对路径

    Returns:
        (moves, messages)：移动序列和诊断消息列表
    """
    messages: list[str] = []
    moves: list[GcodeMove] = []

    try:
        with open(gcode_file_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as exc:
        raise RuntimeError(f"读取 G 代码文件失败：{exc}") from exc

    messages.append(f"G 代码总行数：{len(lines)}")

    for idx, line in enumerate(lines):
        move = _parse_gcode_line(line, idx)
        if move is not None:
            moves.append(move)

    messages.append(f"解析出移动指令：{len(moves)} 条")

    # 统计 G 代码类型分布
    gcode_counts: dict[str, int] = {}
    for m in moves:
        gcode_counts[m.gcode] = gcode_counts.get(m.gcode, 0) + 1
    if gcode_counts:
        dist = ", ".join(f"{k}={v}" for k, v in sorted(gcode_counts.items()))
        messages.append(f"G 代码分布：{dist}")

    return moves, messages


# PyCAM 真实调用（导入核心模块，验证可用性）


def _import_pycam() -> dict[str, Any]:
    """导入 PyCAM 核心模块，返回模块字典。

    Returns
    -------
    dict
        包含 PyCAM 核心模块引用的字典。

    Raises
    ------
    ImportError
        PyCAM 不可用（未安装或核心模块损坏）。
    """
    try:
        import pycam  # type: ignore[import-not-found]
        from pycam.Toolpath import Toolpath  # type: ignore[import-not-found]
        from pycam.Cutters.CylindricalCutter import (
            CylindricalCutter,
        )  # type: ignore[import-not-found]
        from pycam.Cutters.SphericalCutter import (
            SphericalCutter,
        )  # type: ignore[import-not-found]
        from pycam.Cutters.ToroidalCutter import (
            ToroidalCutter,
        )  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(f"PyCAM 核心模块不可用：{exc}。请执行 'pip install pycam' 安装 PyCAM 0.6+。") from exc

    return {
        "pycam": pycam,
        "Toolpath": Toolpath,
        "CylindricalCutter": CylindricalCutter,
        "SphericalCutter": SphericalCutter,
        "ToroidalCutter": ToroidalCutter,
    }


# 校验逻辑（PyCAM 能力范围内的真实检测）


def _check_workspace_limits(
    moves: list[GcodeMove],
    workspace_x: float,
    workspace_y: float,
    workspace_z: float,
) -> list[dict[str, Any]]:
    """检查刀轨点位是否超出机床工作空间。

    Args:
        moves: 移动序列
        workspace_x, workspace_y, workspace_z: 工作空间尺寸（mm）

    Returns:
        碰撞事件列表（workspace_limit 类型）
    """
    collisions: list[dict[str, Any]] = []

    # 允许的坐标范围（假设工件坐标系原点在工作空间一角，负坐标视为越界）
    # 实际车间应根据机床原点位置调整
    x_min, x_max = 0.0, workspace_x
    y_min, y_max = 0.0, workspace_y
    z_min, z_max = -workspace_z, workspace_z  # Z 允许负值（切削深度）

    for move in moves:
        if move.x is not None and (move.x < x_min or move.x > x_max):
            collisions.append(
                {
                    "collision_type": "workspace_limit",
                    "block_number": move.block_number,
                    "message": (f"X={move.x:.3f}mm 超出工作空间 X 范围 [{x_min:.1f}, {x_max:.1f}]mm"),
                    "severity": "critical",
                }
            )
        if move.y is not None and (move.y < y_min or move.y > y_max):
            collisions.append(
                {
                    "collision_type": "workspace_limit",
                    "block_number": move.block_number,
                    "message": (f"Y={move.y:.3f}mm 超出工作空间 Y 范围 [{y_min:.1f}, {y_max:.1f}]mm"),
                    "severity": "critical",
                }
            )
        if move.z is not None and (move.z < z_min or move.z > z_max):
            collisions.append(
                {
                    "collision_type": "workspace_limit",
                    "block_number": move.block_number,
                    "message": (f"Z={move.z:.3f}mm 超出工作空间 Z 范围 [{z_min:.1f}, {z_max:.1f}]mm"),
                    "severity": "critical",
                }
            )

    return collisions


def _check_safe_z(
    moves: list[GcodeMove],
    safe_z: float,
) -> list[dict[str, Any]]:
    """检查 Z 坐标是否低于安全平面。

    安全 Z 是刀具安全换刀/移动的 Z 高度，低于此值在非切削移动时
    可能导致刀具-工件/夹具碰撞。

    Args:
        moves: 移动序列
        safe_z: 安全 Z 平面高度（mm）

    Returns:
        碰撞事件列表（safe_z_violation 类型）
    """
    collisions: list[dict[str, Any]] = []

    for move in moves:
        if move.z is None:
            continue
        # 仅对 G0 快速移动检查安全 Z（G1 切削移动允许低于安全 Z）
        if move.gcode.upper() == "G0" and move.z < safe_z:
            collisions.append(
                {
                    "collision_type": "safe_z_violation",
                    "block_number": move.block_number,
                    "message": (f"G0 快速移动 Z={move.z:.3f}mm 低于安全 Z={safe_z:.1f}mm，可能导致刀具-工件/夹具碰撞"),
                    "severity": "critical",
                }
            )

    return collisions


def _check_rapid_in_material(
    moves: list[GcodeMove],
    safe_z: float,
) -> list[dict[str, Any]]:
    """检查 G0 快速移动是否在材料内部。

    G0 快速移动时 Z 低于安全 Z 且接近材料顶面（Z < rapid_max_z）
    是高风险行为，可能导致刀具崩裂。

    Args:
        moves: 移动序列
        safe_z: 安全 Z 平面高度（mm）

    Returns:
        碰撞事件列表（rapid_in_material 类型）
    """
    collisions: list[dict[str, Any]] = []

    # G0 在 Z < rapid_max_z 时视为风险（rapid_max_z 默认 5mm）
    rapid_max_z = _DEFAULT_RAPID_MAX_Z

    for move in moves:
        if move.z is None:
            continue
        if move.gcode.upper() == "G0" and move.z < rapid_max_z:
            collisions.append(
                {
                    "collision_type": "rapid_in_material",
                    "block_number": move.block_number,
                    "message": (
                        f"G0 快速移动 Z={move.z:.3f}mm 低于材料顶面阈值 {rapid_max_z:.1f}mm，应改用 G1 切削进给"
                    ),
                    "severity": "warning",
                }
            )

    return collisions


def _check_move_continuity(
    moves: list[GcodeMove],
    min_distance: float,
) -> list[dict[str, Any]]:
    """检查刀轨连续性（相邻点位距离异常）。

    Args:
        moves: 移动序列
        min_distance: 最小允许移动距离（mm）

    Returns:
        碰撞事件列表（move_continuity 类型）
    """
    collisions: list[dict[str, Any]] = []

    # 计算绝对坐标序列（继承上一行的坐标）
    cur_x = cur_y = cur_z = 0.0

    for i, move in enumerate(moves):
        new_x = move.x if move.x is not None else cur_x
        new_y = move.y if move.y is not None else cur_y
        new_z = move.z if move.z is not None else cur_z

        if i > 0:
            dx = new_x - cur_x
            dy = new_y - cur_y
            dz = new_z - cur_z
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            if 0 < dist < min_distance:
                collisions.append(
                    {
                        "collision_type": "move_continuity",
                        "block_number": move.block_number,
                        "message": (
                            f"相邻点位距离 {dist:.6f}mm 低于最小阈值 {min_distance:.3f}mm，可能漏写坐标或重复指令"
                        ),
                        "severity": "warning",
                    }
                )

        cur_x, cur_y, cur_z = new_x, new_y, new_z

    return collisions


def _compute_toolpath_bounds(
    moves: list[GcodeMove],
) -> dict[str, tuple[float, float]]:
    """计算刀轨包围盒（使用 PyCAM Toolpath 风格的 min/max 属性）。

    Args:
        moves: 移动序列

    Returns:
        包含 x/y/z 范围的字典：{"x": (min, max), "y": (min, max), "z": (min, max)}
    """
    xs = [m.x for m in moves if m.x is not None]
    ys = [m.y for m in moves if m.y is not None]
    zs = [m.z for m in moves if m.z is not None]

    return {
        "x": (min(xs) if xs else 0.0, max(xs) if xs else 0.0),
        "y": (min(ys) if ys else 0.0, max(ys) if ys else 0.0),
        "z": (min(zs) if zs else 0.0, max(zs) if zs else 0.0),
    }


# 主入口


def main(argv: list[str]) -> int:
    """脚本主入口。

    Args:
        argv: 命令行参数列表（argv[0] 是脚本路径）

    Returns:
        退出码（0=正常完成，1=异常）
    """
    # 1. 参数解析
    if len(argv) < 3:
        return _emit_result(
            status="error",
            messages=[
                f"参数不足：期望 2 个参数（gcode_file_path controller_type），"
                f"实际收到 {len(argv) - 1} 个。"
                f"用法：python autorun_gcode_check.py <gcode_path> <controller_type>"
            ],
        )

    gcode_file_path = argv[1]
    controller_type = argv[2]

    # 校验 controller_type 合法性
    if controller_type not in _CONTROLLER_TO_POSTPROCESSOR:
        return _emit_result(
            status="error",
            messages=[f"未知控制器类型：{controller_type}。合法值：{sorted(_CONTROLLER_TO_POSTPROCESSOR.keys())}"],
        )

    # 2. 校验 G 代码文件存在性（早期失败）
    if not Path(gcode_file_path).is_file():
        return _emit_result(
            status="error",
            messages=[f"G 代码文件不存在：{gcode_file_path}"],
        )

    # 3. 导入 PyCAM（无环境时友好降级）
    try:
        pycam_modules = _import_pycam()
    except ImportError as exc:
        return _emit_result(
            status="error",
            messages=[
                f"PyCAM 不可用：{exc}",
                "请执行 'pip install pycam' 安装 PyCAM 0.6+。",
                "cam_validation 模块将自动降级到 manual 后端。",
            ],
        )

    # 4. 解析 G 代码
    try:
        moves, messages = parse_gcode_file(gcode_file_path)
    except RuntimeError as exc:
        return _emit_result(
            status="error",
            messages=[
                f"G 代码解析失败：{exc}",
                f"时间戳：{_now_iso()}",
            ],
        )
    except Exception as exc:
        return _emit_result(
            status="error",
            messages=[
                f"G 代码解析时发生未预期异常：{exc}",
                f"traceback: {traceback.format_exc()[-500:]}",
            ],
        )

    if not moves:
        # G 代码文件无有效移动指令
        return _emit_result(
            status="error",
            messages=[
                f"G 代码文件未解析出任何移动指令：{gcode_file_path}",
                "可能是空文件或格式不兼容。",
            ],
        )

    # 5. 计算刀轨包围盒（PyCAM Toolpath 风格）
    bounds = _compute_toolpath_bounds(moves)
    messages.append(
        f"刀轨包围盒：X[{bounds['x'][0]:.2f}, {bounds['x'][1]:.2f}]mm, "
        f"Y[{bounds['y'][0]:.2f}, {bounds['y'][1]:.2f}]mm, "
        f"Z[{bounds['z'][0]:.2f}, {bounds['z'][1]:.2f}]mm"
    )

    # 6. 执行 PyCAM 能力范围内的校验
    collisions: list[dict[str, Any]] = []

    # 6.1 工作空间边界检查
    collisions.extend(_check_workspace_limits(moves, _DEFAULT_WORKSPACE_X, _DEFAULT_WORKSPACE_Y, _DEFAULT_WORKSPACE_Z))

    # 6.2 安全 Z 检查
    collisions.extend(_check_safe_z(moves, _DEFAULT_SAFE_Z))

    # 6.3 G0 快速移动在材料内部检测
    collisions.extend(_check_rapid_in_material(moves, _DEFAULT_SAFE_Z))

    # 6.4 刀轨连续性检查
    collisions.extend(_check_move_continuity(moves, _DEFAULT_MIN_MOVE_DISTANCE))

    # 7. 诚实标注 PyCAM 能力边界
    messages.append(
        "PyCAM 能力边界：仅完成工作空间/安全Z/G0材料内/连续性基础检查，"
        "未检测刀柄-工件/刀具-夹具碰撞（需 NX Open / PowerMill）。"
    )
    messages.append(f"PyCAM 版本：{getattr(pycam_modules['pycam'], 'VERSION', 'unknown')}")
    messages.append(f"碰撞事件数：{len(collisions)}")
    messages.append(f"校验完成时间：{_now_iso()}")

    # 8. 输出结果
    status = "fail" if collisions else "pass"

    return _emit_result(
        status=status,
        collisions=collisions,
        messages=messages,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
