"""阶段 1 模板系统冒烟测试：验证内置 YAML 模板可正确加载并转为 WorkflowSpec。"""
from __future__ import annotations

import sys
import types
from pathlib import Path

# _overlapped stub workaround（与项目根 conftest.py 同源，参见 project_memory.md）：
# Python 3.11.0rc2 在 Windows 上 _overlapped C 扩展初始化失败，抛出
# WinError 10038。此 stub 让 asyncio.windows_events 能完成导入，
# 对纯单元/冒烟测试足够（不需要真实 IOCP）。
if sys.platform == "win32":
    try:
        import _overlapped  # noqa: F401
    except OSError:
        stub = types.ModuleType("_overlapped")
        stub.INVALID_HANDLE_VALUE = -1
        stub.ERROR_IO_PENDING = 997
        stub.ERROR_NETNAME_DELETED = 64
        stub.ERROR_OPERATION_ABORTED = 995
        stub.OVERLAPPED = type("OVERLAPPED", (object,), {
            "__init__": lambda self, *a, **k: None,
            "event": 0,
            "address": 0,
        })

        def _placeholder(*args, **kwargs):
            raise RuntimeError("_overlapped stub: IOCP 不可用")

        for sym in (
            "CreateIoCompletionPort", "GetQueuedCompletionStatus",
            "PostQueuedCompletionStatus", "RegisterWaitWithQueue",
            "UnregisterWait", "CreateEvent", "SetEvent", "ResetEvent",
            "CloseHandle", "BindLocal",
        ):
            setattr(stub, sym, _placeholder)
        stub.FormatMessage = lambda *a, **k: ""
        stub.Overlapped = type("Overlapped", (object,), {
            "__init__": lambda self, *a, **k: None,
            "address": 0, "event": 0, "pending": False, "completed": False,
        })
        sys.modules["_overlapped"] = stub

# 让脚本独立于 cwd 运行
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from app.workflow.templates import (  # noqa: E402
    list_builtin_templates,
    load_builtin_template,
    template_to_spec,
)


def main() -> int:
    templates = list_builtin_templates()
    print(f"[smoke] 列举到 {len(templates)} 个内置模板：")
    for t in templates:
        print(
            f"  - {t['template_id']}: {t['name']} "
            f"({t['node_count']} nodes, {t['edge_count']} edges, v{t['version']})"
        )

    if not templates:
        print("[smoke] FAIL: 没有内置模板")
        return 1

    # 加载并校验 chatter_detection（5 节点验收 DAG）
    tpl = load_builtin_template("chatter_detection")
    spec = template_to_spec(tpl)
    errors = spec.validate()
    print(f"\n[smoke] chatter_detection 加载成功")
    print(f"  name: {spec.name}")
    print(f"  nodes: {[n.node_id for n in spec.nodes]}")
    print(f"  edges: {len(spec.edges)} 条")
    print(f"  DAG 校验错误数: {len(errors)}")
    for e in errors:
        print(f"    - {e}")

    if errors:
        print("\n[smoke] FAIL: DAG 校验未通过")
        return 2

    # 加载 tool_wear_pipeline（4 节点线性）
    tpl2 = load_builtin_template("tool_wear_pipeline")
    spec2 = template_to_spec(tpl2)
    errors2 = spec2.validate()
    print(f"\n[smoke] tool_wear_pipeline 加载成功")
    print(f"  name: {spec2.name}")
    print(f"  nodes: {[n.node_id for n in spec2.nodes]}")
    print(f"  DAG 校验错误数: {len(errors2)}")

    if errors2:
        print("\n[smoke] FAIL: tool_wear_pipeline DAG 校验未通过")
        return 3

    # 验证非法 template_id 防路径穿越
    try:
        load_builtin_template("../etc/passwd")
        print("\n[smoke] FAIL: 路径穿越未拦截")
        return 4
    except Exception as e:
        print(f"\n[smoke] 路径穿越已拦截: {type(e).__name__}")

    print("\n[smoke] ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
