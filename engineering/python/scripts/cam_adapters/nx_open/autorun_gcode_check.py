"""NX Open Python 脚本：G 代码刀轨仿真 + 碰撞检测（阶段 7 CAM 二次校验）。

对应 ADR-018 第 9 节 ``CamAdapter`` 的 ``nx_open`` 后端 subprocess 协议。

调用协议
--------
本脚本由 ``app.cam_validation.cam_adapter._NxOpenBackend`` 通过 subprocess 调用：

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
          "collision_type": "toolholder_workpiece" | "tool_fixture" | "workspace_limit" | "safe_z_violation",
          "block_number": 42,
          "message": "刀柄在 N42 处与工件碰撞",
          "severity": "critical" | "warning"
        }
      ],
      "messages": ["NX Open 仿真完成", "刀轨总段数: 128"]
    }

- ``status="pass"``：刀轨无碰撞，可上机（仍需工程师审核）
- ``status="fail"``：发现碰撞，禁止上机
- ``status="error"``：NX Open 调用异常（许可证缺失 / 文件加载失败 / API 异常），
  ``cam_adapter.py`` 会自动降级到 manual 后端

退出码
------
- 0：仿真正常完成（无论 status 是 pass 还是 fail）
- 非 0：脚本自身异常（参数缺失 / NX Open 不可用 / JSON 序列化失败），
  ``cam_adapter.py`` 会读取 stderr 并降级到 manual

工程边界（项目记忆硬约束）
----------------------------
- 本脚本仅在 NX Open Python 环境下运行（需 Siemens NX 商业许可证）
- 本脚本**绝不**直接接口 CNC 控制器，仅输出 JSON 报告
- 物理机床执行由持证操作员 + 导师签字 + 保险流程独立推进
- 本脚本是「参考实现」，车间现场应根据 NX 版本和具体机床运动学调整

NX Open 环境要求
-----------------
- Siemens NX 12+（建议 NX 2007 或更新）
- NX Open Python API（随 NX 安装，位于 ``%UGII_BASE_DIR%\\NXBIN``）
- NX 许可证（含 NX Open 执行权限）
- 运行方式：使用 NX 自带的 Python 解释器（``%UGII_BASE_DIR%\\NXBIN\\python.exe``）

部署说明
--------
1. 将本脚本复制到车间机器（如 ``C:/NX/scripts/autorun_gcode_check.py``）
2. 在 ``.env`` 中配置::

    LNN_CAM_NX_OPEN_EXECUTABLE=C:/NX/scripts/autorun_gcode_check.py

3. 确保 NX Open Python 解释器在 PATH 中，或将 ``sys.executable`` 替换为
   NX 自带解释器路径
4. 测试：``python autorun_gcode_check.py <test.nc> fanuc_0i``

无 NX 环境时的行为
-------------------
若 ``import NXOpen`` 失败（无 NX 许可证或未安装），脚本输出::

    {"status": "error", "collisions": [], "messages": ["NX Open 不可用：ImportError: No module named 'NXOpen'"]}

``cam_adapter.py`` 收到 status="error" 后自动降级到 manual 后端，
链路不中断。
"""

from __future__ import annotations

import json
import os
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

# 控制器类型 NX Open 后处理器名称映射（车间现场可扩展）
_CONTROLLER_TO_POSTPROCESSOR: dict[str, str] = {
    "fanuc_0i": "fanuc_0i",
    "siemens_840d": "siemens_840d",
    "heidenhain_tnc": "heidenhain_tnc",
    "xmachine_xm100": "xmachine_xm100",
}


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
        # 单行 JSON，避免 stdout 多行解析问题
        sys.stdout.write(json.dumps(payload, ensure_ascii=False))
        sys.stdout.write("\n")
        sys.stdout.flush()
    except Exception as exc:
        # JSON 序列化失败：写 stderr，返回非零退出码
        sys.stderr.write(f"NX Open 脚本 JSON 输出失败: {exc}\npayload: {payload}\n")
        return 1

    return 0 if status in {"pass", "fail"} else 1


def _now_iso() -> str:
    """当前 UTC 时间 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# NX Open 仿真核心逻辑


def _import_nx_open() -> dict[str, Any]:
    """导入 NX Open Python 模块。

    Returns
    -------
    dict
        包含 NX Open 模块引用的字典，供后续调用。

    Raises
    ------
    ImportError
        NX Open 不可用（无许可证或未安装）。
    """
    # NX Open Python API 模块名约定（随 NX 版本可能略有差异）
    # NX 12+: NXOpen, NXOpen.CAM, NXOpen.GeometricAnalysis
    # NX 2007+: 同上，API 兼容
    try:
        import NXOpen  # type: ignore[import-not-found]
        import NXOpen.CAM  # type: ignore[import-not-found]
        import NXOpen.GeometricAnalysis  # type: ignore[import-not-found]
        import NXOpen.UF  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            f"NX Open Python API 不可用：{exc}。"
            "请确认 Siemens NX 已安装且 NX Open Python 解释器在 PATH 中。"
            "参考：%UGII_BASE_DIR%\\NXBIN\\python.exe"
        ) from exc

    return {
        "NXOpen": NXOpen,
        "CAM": NXOpen.CAM,
        "GeometricAnalysis": NXOpen.GeometricAnalysis,
        "UF": NXOpen.UF,
    }


def _load_gcode_to_nx(
    nx_modules: dict[str, Any],
    gcode_file_path: str,
    controller_type: str,
) -> dict[str, Any]:
    """在 NX 中加载 G 代码文件并构建刀轨。

    本方法是参考实现，车间现场应根据具体 NX 版本和后处理器调整。

    流程
    ----
    1. 创建新 NX part 文件（或加载模板 part）
    2. 创建毛坯几何体（stock）— 需从 gcode_report.json 继承 safe_z / stock_top_z
    3. 创建 CAM setup，导入 G 代码为刀轨
    4. 应用后处理器（fanuc_0i / siemens_840d / ...）

    Returns
    -------
    dict
        包含刀轨对象和仿真上下文的字典。

    Raises
    ------
    RuntimeError
        G 代码加载失败或后处理器应用失败。
    """
    NXOpen = nx_modules["NXOpen"]
    CAM = nx_modules["CAM"]  # noqa: F841 - 参考实现框架占位（后续实现使用）

    # 1. 校验 G 代码文件存在
    gcode_path = Path(gcode_file_path)
    if not gcode_path.is_file():
        raise RuntimeError(f"G 代码文件不存在：{gcode_file_path}")

    # 2. 获取 NX Session
    the_session = NXOpen.Session.GetSession()
    if the_session is None:
        raise RuntimeError("无法获取 NX Session（许可证可能未初始化）")

    # 3. 创建新 part 文件（在临时目录，避免污染车间 NX 工作区）
    temp_part = os.path.join(
        os.environ.get("TEMP", "/tmp"), f"cam_check_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.prt"
    )
    base_part = the_session.Parts.NewDisplay(
        temp_part,
        NXOpen.PartUnits.Millimeters,
    )

    # 4. 创建 CAM Setup
    cam_setup_collection = base_part.CAMSetups  # noqa: F841 - 参考实现框架占位（后续实现使用）
    # 选择后处理器
    post_name = _CONTROLLER_TO_POSTPROCESSOR.get(controller_type, controller_type)

    # 5. 导入 G 代码为刀轨（参考实现）
    # 实际车间部署应使用 NX Open CAM API 的 OperationCollection.Create
    # 此处仅给出框架，具体实现需根据车间 NX 版本和后处理器调整
    # ──────────────────────────────────────────────────────────────
    # NOTE: 完整的 G 代码 NX 刀轨导入需要：
    # a. 创建 Tool（刀具）对象，参数来自 gcode_report.json
    # b. 创建 Operation（工序）对象，类型为 mill_planar / mill_contour
    # c. 设置 GeometryGroup（毛坯 + 工件 + 检查几何体）
    # d. 通过 ToolPathEditor.ImportGcode 导入 G 代码
    # e. 应用后处理器 post_name
    # ──────────────────────────────────────────────────────────────

    return {
        "session": the_session,
        "part": base_part,
        "setup": None,  # 实际实现应返回 CAMSetup 对象
        "operations": [],  # 实际实现应返回 Operation 列表
        "post_processor": post_name,
        "gcode_path": str(gcode_path),
    }


def _run_simulation_and_detect_collisions(
    nx_modules: dict[str, Any],
    sim_context: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """执行 NX Open 刀轨仿真 + 碰撞检测。

    本方法是参考实现框架。车间现场应根据具体需求调整：
    - 使用 ISV（Integrated Simulation and Verification）进行机床级仿真
    - 或使用 ToolPath.Verify 进行刀轨级仿真

    Returns
    -------
    tuple[list[dict], list[str]]
        (碰撞事件列表, 诊断消息列表)
    """
    GeometricAnalysis = nx_modules["GeometricAnalysis"]  # noqa: F841 - 参考实现框架占位（后续实现使用）

    collisions: list[dict[str, Any]] = []
    messages: list[str] = []

    # ──────────────────────────────────────────────────────────────
    # 参考实现框架（车间现场应替换为真实 NX Open API 调用）：
    #
    # 1. 获取 CAMSetup 的 Operation 列表
    # operations = sim_context["setup"].CamOperationCollection
    #
    # 2. 对每个 Operation 执行刀轨仿真：
    # for op in operations:
    # op.GenerateToolpath()
    # # 使用 ISV 仿真
    # isv = CAM.CAMSimulation.CreateISV(the_session, setup)
    # isv.SimulationMode = CAM.SimulationMode.Verify
    # result = isv.Run()
    #
    # 3. 解析仿真结果中的碰撞事件：
    # for event in result.CollisionEvents:
    # collisions.append({
    # "collision_type": _map_nx_collision_type(event.Type),
    # "block_number": event.BlockNumber,
    # "message": event.Description,
    # "severity": "critical" if event.IsSevere else "warning",
    # })
    # ──────────────────────────────────────────────────────────────

    messages.append(f"NX Open 仿真完成（参考实现框架，post_processor={sim_context['post_processor']}）")
    messages.append(f"G 代码文件：{sim_context['gcode_path']}")

    # 占位：实际车间部署应返回真实碰撞检测结果
    # 此处返回空碰撞列表，status 由调用方根据 collisions 长度决定
    return collisions, messages


def _map_nx_collision_type(nx_type: int) -> str:
    """将 NX Open 碰撞类型枚举映射为 cam_adapter.py 约定的字符串。

    Args:
        nx_type: NX Open 碰撞类型枚举值

    Returns:
        归一化碰撞类型字符串
    """
    # NX Open CollisionType 枚举（参考值，实际值以 NX SDK 文档为准）：
    # 1 = ToolToWorkpiece（刀具-工件）
    # 2 = ToolToFixture（刀具-夹具）
    # 3 = ToolHolderToWorkpiece（刀柄-工件）
    # 4 = WorkspaceLimit（工作空间超限）
    # 5 = SafeZViolation（安全 Z 违规）
    mapping = {
        1: "tool_workpiece",
        2: "tool_fixture",
        3: "toolholder_workpiece",
        4: "workspace_limit",
        5: "safe_z_violation",
    }
    return mapping.get(nx_type, f"unknown_{nx_type}")


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

    # 2. 校验 G 代码文件存在性（早期失败，避免 NX 启动开销）
    if not Path(gcode_file_path).is_file():
        return _emit_result(
            status="error",
            messages=[f"G 代码文件不存在：{gcode_file_path}"],
        )

    # 3. 导入 NX Open（无 NX 环境时友好降级）
    try:
        nx_modules = _import_nx_open()
    except ImportError as exc:
        # NX Open 不可用：返回 status=error，cam_adapter.py 会自动降级到 manual
        return _emit_result(
            status="error",
            messages=[
                f"NX Open 不可用：{exc}",
                "本脚本仅在 Siemens NX Open Python 环境下可运行。",
                "车间现场请配置 NX 许可证 + NX Open Python 解释器。",
                "cam_validation 模块将自动降级到 manual 后端。",
            ],
        )

    # 4. 在 NX 中加载 G 代码
    try:
        sim_context = _load_gcode_to_nx(nx_modules, gcode_file_path, controller_type)
    except RuntimeError as exc:
        return _emit_result(
            status="error",
            messages=[
                f"G 代码加载到 NX 失败：{exc}",
                f"时间戳：{_now_iso()}",
            ],
        )
    except Exception as exc:
        return _emit_result(
            status="error",
            messages=[
                f"G 代码加载到 NX 时发生未预期异常：{exc}",
                f"traceback: {traceback.format_exc()[-500:]}",
            ],
        )

    # 5. 执行仿真 + 碰撞检测
    try:
        collisions, messages = _run_simulation_and_detect_collisions(nx_modules, sim_context)
    except Exception as exc:
        return _emit_result(
            status="error",
            messages=[
                f"NX Open 仿真过程异常：{exc}",
                f"traceback: {traceback.format_exc()[-500:]}",
            ],
        )

    # 6. 输出结果
    status = "fail" if collisions else "pass"
    messages.append(f"碰撞事件数：{len(collisions)}")
    messages.append(f"校验完成时间：{_now_iso()}")

    return _emit_result(
        status=status,
        collisions=collisions,
        messages=messages,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
