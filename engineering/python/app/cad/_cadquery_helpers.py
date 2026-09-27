"""CadQuery generator wrapper for CAD generation."""

from __future__ import annotations

import ast
import ctypes
import json
import logging
import os
import struct
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

# 平台兼容：resource 模块仅在 Unix 可用，Windows 下跳过 RLIMIT 配置
_resource: Any
try:
    import resource as _resource
except ImportError:
    _resource = None

import cadquery as cq  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageFilter, ImageOps  # noqa: E402


logger = logging.getLogger(__name__)


# CadQuery 临时输出目录的进程级清理
# 历史问题：execute_and_export / generate_3d_model / generate_with_features
# 三处使用固定名称的临时目录（cadquery_output / cadquery_models），产物文件
# 永不回收，高频建模场景下数日内即可耗尽 /tmp 磁盘。
# 修复策略：模块级维护已创建目录集合，atexit 退出时统一清理。
# 不能使用 TemporaryDirectory 上下文管理器，因为返回的 output_path 会被
# 后续步骤（NL2CAD pipeline / API 响应）读取，必须保留到进程退出。
_CADQUERY_TEMP_DIRS: set[Path] = set()


def _cleanup_cadquery_temp_dirs() -> None:
    """进程退出时清理所有 CadQuery 临时输出目录。"""
    import shutil as _shutil

    for d in list(_CADQUERY_TEMP_DIRS):
        try:
            _shutil.rmtree(d, ignore_errors=True)
        except Exception as e:
            logger.debug("Failed to cleanup cadquery temp dir %s: %s", d, e)


def _register_cadquery_temp_dir(path: Path) -> None:
    """注册一个 CadQuery 临时输出目录，供进程退出时清理。"""
    _CADQUERY_TEMP_DIRS.add(path)


def _unpack_generation_params(
    params: dict[str, Any],
) -> tuple[str, dict[str, float], dict[str, float]]:
    shape_type = params.get("shape_type", "box")
    dimensions = params.get("dimensions") or {}
    position = params.get("position", {"x": 0, "y": 0, "z": 0})
    return shape_type, dimensions, position


def _build_shape_params(
    shape_type: str,
    dimensions: dict[str, float],
) -> dict[str, Any]:
    """box/sphere/cylinder 的 Workplane 方法名与参数（cone 走 _build_solid 特例）。"""
    length = dimensions.get("length", 50)
    width = dimensions.get("width", 30)
    height = dimensions.get("height", 20)

    if shape_type == "sphere":
        radius = max(length, width, height) / 2
        return {"method_name": "sphere", "method_args": [radius]}
    if shape_type == "cylinder":
        return {"method_name": "cylinder", "method_args": [height, width / 2]}

    # box 与未知形状（未知形状回退 box）
    if shape_type != "box":
        logger.warning("Unknown shape type '%s', falling back to box", shape_type)
    return {"method_name": "box", "method_args": [length, width, height]}


def _build_shape_script(
    shape_type: str,
    dimensions: dict[str, float],
    position: dict[str, float],
) -> str:
    """生成参数化模板脚本（顶层命名尺寸变量 → 构造调用）。

    变量化是刻意的：``extract_parameters`` 从顶层赋值提取参数表暴露给
    前端滑杆，用户调参后经 ``regenerate_model`` 免 LLM 重执行。每个形状
    只暴露**真正驱动几何**的尺寸（sphere 只给 radius，不给无效的
    length/width/height），数值语义与 ``_build_solid`` 逐一对齐。

    注意：沙箱脚本只注入 ``cq``、无内建函数，因此 sphere 的 max(...)
    在 Python 侧预计算为具体数值，cone 走 ``cq.Solid.makeCone``
    （cadquery 2.x 的 Workplane 没有 .cone）。
    """
    spec = _SHAPE_SCRIPT_SPECS.get(shape_type)
    if spec is None:
        logger.warning("Unknown shape type '%s', falling back to box", shape_type)
        spec = _SHAPE_SCRIPT_SPECS["box"]

    lines = [f"{name} = {value}" for name, value in spec["vars"](dimensions)]
    lines.append(
        f"result = cq.Workplane('XY').{spec['call']}"
        f".translate(("
        f"{position.get('x', 0)}, "
        f"{position.get('y', 0)}, "
        f"{position.get('z', 0)}"
        f"))"
    )
    return "\n".join(lines)


# 每种形状的脚本规格：顶层变量（Python 侧计算）+ 构造调用表达式。
# 与 _build_solid 的数值语义逐一对齐。
_SHAPE_SCRIPT_SPECS: dict[str, dict[str, Any]] = {
    "box": {
        "vars": lambda d: [
            ("length", d.get("length", 50)),
            ("width", d.get("width", 30)),
            ("height", d.get("height", 20)),
        ],
        "call": "box(length, width, height)",
    },
    "sphere": {
        "vars": lambda d: [
            ("radius", max(d.get("length", 50), d.get("width", 30), d.get("height", 20)) / 2),
        ],
        "call": "sphere(radius)",
    },
    "cylinder": {
        "vars": lambda d: [
            ("height", d.get("height", 20)),
            ("radius", d.get("width", 30) / 2),
        ],
        "call": "cylinder(height, radius)",
    },
    "cone": {
        "vars": lambda d: [
            ("height", d.get("height", 20)),
            ("bottom_radius", d.get("width", 30) / 2),
            ("top_radius", d.get("length", 50) / 2),
        ],
        "call": "add(cq.Solid.makeCone(bottom_radius, top_radius, height))",
    },
}


def _build_solid(
    shape_type: str,
    dimensions: dict[str, float],
    position: dict[str, float],
) -> cq.Workplane:
    px = position.get("x", 0)
    py = position.get("y", 0)
    pz = position.get("z", 0)

    # cone 特例：cadquery 2.x 的 Workplane 没有 .cone（原实现 getattr 会
    # AttributeError），改用 Solid.makeCone；数值语义与 _SHAPE_SCRIPT_SPECS
    # 的 cone 脚本一致（底半径 width/2、顶半径 length/2）。
    if shape_type == "cone":
        solid = cq.Solid.makeCone(
            dimensions.get("width", 30) / 2,
            dimensions.get("length", 50) / 2,
            dimensions.get("height", 20),
        )
        return cq.Workplane("XY").newObject([solid]).translate((px, py, pz))

    params = _build_shape_params(shape_type, dimensions)
    method_name = params["method_name"]
    method_args = params["method_args"]
    method = getattr(cq.Workplane("XY"), method_name)
    return method(*method_args).translate((px, py, pz))


def _wrap_script(script: str, output_path: str, output_format: str) -> str:
    # 版本无关导出：cq.exporters.export(w, fname) 按文件扩展名推断格式，
    # cadquery 2.5.2（requirements pin）无 camelCase exportStep/exportStl，
    # 2.7/2.8 亦支持该通用 API（ExportTypes 可用时显式指定，否则推断）。
    et_map = {"stl": "STL", "obj": "OBJ", "gltf": "GLTF", "step": "STEP"}
    et = et_map.get((output_format or "").lower(), "STL")

    # 注意：不要在此处注入 `import cadquery as cq`。
    # safe_globals 已预注入 cq/cadquery，且 _CadQueryScriptValidator
    # 会无条件拒绝任何 import 语句。注入 import 会导致生成的脚本被自己的
    # 验证器拒绝（C1 bug 修复）。
    return f"""
{script}

_export_type = getattr(cq.exporters, 'ExportTypes', None)
if _export_type is not None and hasattr(_export_type, '{et}'):
    cq.exporters.export(result, {json.dumps(output_path)}, exportType=_export_type.{et})
else:
    cq.exporters.export(result, {json.dumps(output_path)})
"""


def _run_cadquery_script(script: str, task_id: str) -> None:
    """在可强杀的隔离子进程中执行 CadQuery 脚本（根因修复 Linux OCCT 挂死）。

    背景：退化几何（如 box(0,0,0)）在 cadquery-ocp 的 Linux 轮子上会于 OCCT
    原生层挂死；下方沙箱原以 daemon 子线程 + PyThreadState 异步异常实现超时，
    而异步异常只能在 Python 字节码边界生效，无法打断原生调用，泄漏的工作线程
    随后令同进程内所有 CadQuery 调用一并卡死。

    修复方式：父进程把脚本派发到一次性子进程中回环调用本函数（子进程通过
    环境变量 ``_LNN_CADQUERY_IN_CHILD`` 识别自身，直接进入下方沙箱逻辑）。
    超时由 ``subprocess.run`` 强杀子进程（POSIX SIGKILL / Windows
    TerminateProcess）兜底——无论脚本卡在 OCCT 原生层还是死循环，均可彻底回收，
    父进程的 OCCT 状态不受影响。

    沙箱约束保持不变（子进程内逐字沿用原实现）：
    1. AST 审计：使用 _CadQueryScriptValidator 拒绝危险属性访问（如 __class__, __globals__ 等）
    2. 禁止 import：脚本无法动态导入模块，只能使用预注入的 cq/cadquery
    3. 受限内置函数：移除 __import__ 与 type 等危险内置函数
    4. 白名单机制：仅允许访问安全的内置函数和 cadquery 模块
    5. 超时（默认 30s，子进程口径含解释器与 cadquery 导入耗时）+
       内存上限（默认 2GB，Unix only）防 DoS。

    代价：每次执行需在子进程重新导入 cadquery（约 1-3s）。NL2CAD 单任务仅
    执行 1-3 次，可接受。
    """
    # 延迟导入避免循环依赖；必须在进入沙箱前无条件绑定——嵌套的反射 wrapper
    # 引用此名字，若仅在异常分支绑定会因自由变量未关联而退化为 NameError。
    from app.cad.cadquery_gen import CadQueryScriptError

    # 安全修复：先进行 AST 审计，拒绝危险属性访问和 import 语句
    try:
        tree = ast.parse(script)
        from app.cad.cadquery_gen import _CadQueryScriptValidator, _DANGEROUS_ATTRS  # 延迟导入避免循环

        _CadQueryScriptValidator().visit(tree)
    except SyntaxError as e:
        error_msg = f"Script syntax error (task {task_id}): {e}"
        logger.error(error_msg, exc_info=True)
        raise CadQueryScriptError(error_msg) from e

    # 安全修复：用 wrapper 包装反射 API，阻止字符串形式的 dunder 属性访问
    # （AST 审计器只能拦截 `obj.__class__` 直接访问，无法拦截 `getattr(obj, "__class__")`）
    def _safe_getattr(obj: Any, name: str, *default: Any) -> Any:
        if isinstance(name, str) and name in _DANGEROUS_ATTRS:
            raise CadQueryScriptError(f"Access to dangerous attribute '{name}' is forbidden via getattr()")
        return getattr(obj, name, *default) if default else getattr(obj, name)

    def _safe_setattr(obj: Any, name: str, value: Any) -> None:
        if isinstance(name, str) and name in _DANGEROUS_ATTRS:
            raise CadQueryScriptError(f"Setting dangerous attribute '{name}' is forbidden via setattr()")
        setattr(obj, name, value)

    def _safe_delattr(obj: Any, name: str) -> None:
        if isinstance(name, str) and name in _DANGEROUS_ATTRS:
            raise CadQueryScriptError(f"Deleting dangerous attribute '{name}' is forbidden via delattr()")
        delattr(obj, name)

    # 创建受控的执行环境
    safe_globals = {
        "__builtins__": {
            # 安全修复：移除 __import__，禁止脚本动态导入模块
            # 安全修复：移除 type（可被用于动态构造类型逃逸）
            "print": print,
            "len": len,
            "range": range,
            "enumerate": enumerate,
            "zip": zip,
            "map": map,
            "list": list,
            "dict": dict,
            "tuple": tuple,
            "set": set,
            "frozenset": frozenset,
            "str": str,
            "int": int,
            "float": float,
            "bool": bool,
            "isinstance": isinstance,
            "issubclass": issubclass,
            "hasattr": hasattr,
            # 反射 API 用 wrapper 包装，过滤危险 dunder 属性
            "getattr": _safe_getattr,
            "setattr": _safe_setattr,
            "delattr": _safe_delattr,
            "property": property,
            "staticmethod": staticmethod,
            "classmethod": classmethod,
            "super": super,
            "object": object,
            "Exception": Exception,
            "ValueError": ValueError,
            "TypeError": TypeError,
            "KeyError": KeyError,
            "IndexError": IndexError,
            "AttributeError": AttributeError,
            "RuntimeError": RuntimeError,
            "StopIteration": StopIteration,
            "True": True,
            "False": False,
            "None": None,
        },
        "cq": cq,
        "cadquery": cq,
    }

    # S5 修复：通过子线程执行 exec()，主线程用 join(timeout) 实现超时控制
    # 超时后通过 PyThreadState_SetAsyncExc 向子线程注入异常以中断 exec
    # 子进程回环：一次性子进程以 _WORKER_BOOTSTRAP 重新进入本函数（见模块 docstring），
    # 父进程绝不直接运行用户脚本，超时可强杀整个子进程。
    if os.environ.get("_LNN_CADQUERY_IN_CHILD") != "1":
        _run_in_killable_child(script, task_id)
        return

    timeout_seconds = float(os.environ.get("LNN_CADQUERY_TIMEOUT", "30"))
    memory_limit_mb = int(os.environ.get("LNN_CADQUERY_MEMORY_LIMIT_MB", "2048"))

    result_holder: dict[str, Any] = {"exc": None}

    def _execute_in_thread() -> None:
        # S5 修复：在子线程中设置资源限制（仅 Unix）
        # RLIMIT_AS 限制进程虚拟内存上限；RLIMIT_CPU 限制 CPU 时间（秒）
        # 注意：RLIMIT_AS 对 fork 后的子进程生效，对当前线程实际上是进程级限制
        if _resource is not None and memory_limit_mb > 0:
            try:
                # 内存上限（字节）
                mem_bytes = memory_limit_mb * 1024 * 1024
                if hasattr(_resource, "setrlimit") and hasattr(_resource, "RLIMIT_AS"):
                    _resource.setrlimit(_resource.RLIMIT_AS, (mem_bytes, mem_bytes))
                logger.debug("Set RLIMIT_AS=%d MB for task %s", memory_limit_mb, task_id)
            except (ValueError, OSError) as e:
                # setrlimit 失败不应阻断执行，仅记录警告
                logger.warning(
                    "Failed to set RLIMIT_AS for task %s: %s (skip memory limit)",
                    task_id,
                    e,
                )
        try:
            exec(script, safe_globals)
        except (
            ValueError,
            KeyError,
            TypeError,
            OSError,
            RuntimeError,
            SyntaxError,
            NameError,
            KeyboardInterrupt,
            MemoryError,
        ) as e:
            result_holder["exc"] = e
        except BaseException as e:
            result_holder["exc"] = e

    worker = threading.Thread(
        target=_execute_in_thread,
        name=f"cadquery-exec-{task_id}",
        daemon=True,
    )
    worker.start()
    worker.join(timeout=timeout_seconds)

    if worker.is_alive():
        # 超时：子线程仍在运行，尝试通过异步异常中断
        _async_raise_thread(worker, KeyboardInterrupt)
        # 给子线程短暂时间清理
        worker.join(timeout=2.0)
        error_msg = f"CadQuery script execution timed out after {timeout_seconds}s (task {task_id})"
        logger.error(error_msg)
        raise CadQueryScriptError(error_msg)

    # 子线程已结束，检查异常
    exc = result_holder.get("exc")
    if exc is not None:
        if isinstance(exc, MemoryError):
            error_msg = f"Script execution exceeded memory limit {memory_limit_mb}MB (task {task_id}): {exc}"
            logger.error(error_msg, exc_info=True)
        elif isinstance(exc, KeyboardInterrupt):
            error_msg = f"Script execution interrupted (task {task_id}): {exc}"
            logger.error(error_msg, exc_info=True)
        elif isinstance(exc, (ValueError, KeyError, TypeError, OSError, RuntimeError, SyntaxError, NameError)):
            error_msg = f"Script execution failed (task {task_id}): {exc}"
            logger.error(error_msg, exc_info=True)
        else:
            error_msg = f"Script execution failed with unexpected exception (task {task_id}): {exc}"
            logger.error(error_msg, exc_info=True)
        raise CadQueryScriptError(error_msg) from exc

    logger.debug("Script for task %s completed successfully", task_id)


# 子进程引导脚本：仅做环境标记、stdin 元数据解析与模块回环调用，
# 自身不包含任何动态代码构造；真正的沙箱执行全部复用本模块原有逻辑。
_WORKER_BOOTSTRAP = """\
import json
import os
import sys

os.environ["_LNN_CADQUERY_IN_CHILD"] = "1"
meta = json.loads(sys.stdin.readline())
sys.path.insert(0, meta["python_root"])
from app.cad.cadquery_gen import CadQueryScriptError
from app.cad._cadquery_helpers import _run_cadquery_script

try:
    _run_cadquery_script(meta["script"], meta.get("task_id", "worker"))
except CadQueryScriptError as exc:
    print(json.dumps({"status": "error", "error": str(exc)}))
except BaseException as exc:
    print(json.dumps({"status": "error", "error": "%s: %s" % (type(exc).__name__, exc)}))
else:
    print(json.dumps({"status": "ok"}))
"""


def _run_in_killable_child(script: str, task_id: str) -> None:
    """把脚本派发到一次性子进程执行，超时强杀（见 _run_cadquery_script docstring）。"""
    from app.cad.cadquery_gen import CadQueryScriptError

    timeout_seconds = float(os.environ.get("LNN_CADQUERY_TIMEOUT", "30"))
    python_root = str(Path(__file__).resolve().parents[2])
    envelope = json.dumps({"script": script, "task_id": task_id, "python_root": python_root})

    try:
        proc = subprocess.run(
            [sys.executable, "-c", _WORKER_BOOTSTRAP],
            input=envelope,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        # subprocess.run 超时时已强杀并回收子进程，无需（也无法）再清理
        error_msg = f"CadQuery script execution timed out after {timeout_seconds}s (task {task_id})"
        logger.error(error_msg)
        raise CadQueryScriptError(error_msg) from None
    except OSError as e:
        error_msg = (
            f"CadQuery 沙箱子进程启动失败（task {task_id}）：{e}。"
            f"建议检查 sys.executable（当前为 {sys.executable!r}）是否可用。"
        )
        logger.error(error_msg, exc_info=True)
        raise CadQueryScriptError(error_msg) from e

    result = _parse_worker_result(proc.stdout, proc.stderr)
    if result.get("status") == "ok":
        logger.debug("Script for task %s completed in child process", task_id)
        return

    error_msg = f"Script execution failed (task {task_id}): {result.get('error', 'unknown error')}"
    logger.error("%s | exit=%s | stderr tail: %s", error_msg, proc.returncode, proc.stderr[-2000:])
    raise CadQueryScriptError(error_msg)


def _parse_worker_result(stdout: str | None, stderr: str | None) -> dict[str, Any]:
    """解析子进程回传结果。

    协议：stdout 的最后一个非空行为 JSON ``{"status": "ok"|"error", ...}``；
    脚本自身的 print 输出出现在更早的行，按行倒序跳过。子进程未按协议输出
    （如解释器或 cadquery 导入即崩溃）时返回失败占位，附 stderr 尾部辅助定位。
    """
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            break
        if isinstance(parsed, dict):
            return parsed
        break
    stderr_tail = (stderr or "").strip()[-500:]
    detail = (
        f"cadquery 沙箱子进程未按协议回传即退出；stderr: {stderr_tail}"
        if stderr_tail
        else ("cadquery 沙箱子进程未按协议回传即退出")
    )
    return {"status": "error", "error": detail}


def _async_raise_thread(thread: threading.Thread, exc_type: type) -> None:
    """向指定线程异步抛出异常以中断其执行。

    S5 修复：使用 ctypes 调用 PyThreadState_SetAsyncExc 私有 API，
    让目标线程在下一个 Python 字节码边界抛出指定异常。
    这是 CPython 下唯一能从外部中断 exec() 的可靠手段。

    注意：
    - 该 API 不会立即中断 C 扩展调用（如 cadquery 的 OCCT 调用），
      只在控制权回到 Python 层后才生效。
    - 异常类型优先使用 KeyboardInterrupt（语义清晰），不可用 BaseException
      以外的类型（如 Exception 会被宽 except 吞掉）。
    """
    tid = thread.ident
    if tid is None:
        return
    try:
        # _async_raise(exc_type) set async exception
        # PyThreadState_SetAsyncExc(tid, exc_type) 返回线程数
        # 0 表示线程已退出；>1 表示异常状态异常（罕见，重置为 0）
        ret = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(tid), ctypes.py_object(exc_type))
        if ret == 0:
            # 线程已退出，无需中断
            return
        if ret > 1:
            # 异常状态：多个线程被标记，需要重置
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_long(tid), ctypes.c_long(0))
            logger.warning(
                "PyThreadState_SetAsyncExc returned %d for thread %s; reset",
                ret,
                thread.name,
            )
    except (ValueError, OSError, AttributeError) as e:
        logger.warning("Failed to async-raise in thread %s: %s", thread.name, e)


def _preprocess_image_for_cv(filepath: Path) -> np.ndarray:
    """Load image, convert to grayscale, and apply edge detection.

    Uses Pillow for image IO and basic filtering. Returns a 2D numpy
    uint8 array representing the edge map, suitable for contour analysis.
    """
    with Image.open(filepath) as img:
        gray = ImageOps.grayscale(img.convert("RGB"))
        # Resize very large images to keep downstream numpy ops cheap.
        max_side = 1024
        if max(gray.size) > max_side:
            ratio = max_side / max(gray.size)
            new_size = (int(gray.size[0] * ratio), int(gray.size[1] * ratio))
            gray = gray.resize(new_size, Image.Resampling.LANCZOS)
        # Light blur to suppress sensor noise before edge detection.
        blurred = gray.filter(ImageFilter.GaussianBlur(radius=1.0))
        edges = blurred.filter(ImageFilter.FIND_EDGES)
        # Auto-threshold to binary edge map (Otsu-style: mean of nonzero).
        arr = np.asarray(edges, dtype=np.uint8)
        nonzero = arr[arr > 0]
        threshold = int(nonzero.mean()) if nonzero.size else 1
        binary = (arr >= max(threshold, 8)).astype(np.uint8)
    return binary


def _find_connected_regions(binary: np.ndarray) -> list[dict[str, Any]]:
    """Find connected foreground regions in a binary image using a
    two-pass connected-component labelling algorithm (4-connectivity).

    Returns a list of region descriptors, each containing:
        - ``bbox``: (x0, y0, x1, y1)
        - ``area``: pixel count
        - ``centroid``: (cx, cy)
    Regions smaller than 0.5% of the image area are discarded as noise.
    """
    h, w = binary.shape
    labels = np.zeros((h, w), dtype=np.int32)
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    next_label = 1
    # First pass: assign tentative labels.
    for y in range(h):
        for x in range(w):
            if binary[y, x] == 0:
                continue
            neighbours = []
            if x > 0 and labels[y, x - 1] > 0:
                neighbours.append(labels[y, x - 1])
            if y > 0 and labels[y - 1, x] > 0:
                neighbours.append(labels[y - 1, x])
            if not neighbours:
                labels[y, x] = next_label
                parent[next_label] = next_label
                next_label += 1
            else:
                min_label = min(neighbours)
                labels[y, x] = min_label
                for n in neighbours:
                    union(n, min_label)

    # Second pass: flatten equivalence classes.
    root_to_id: dict[int, int] = {}
    id_counter = 0
    flat = np.zeros_like(labels)
    for y in range(h):
        for x in range(w):
            if labels[y, x] == 0:
                continue
            root = find(labels[y, x])
            if root not in root_to_id:
                root_to_id[root] = id_counter
                id_counter += 1
            flat[y, x] = root_to_id[root] + 1

    total_pixels = h * w
    min_area = max(int(total_pixels * 0.005), 20)
    regions: list[dict[str, Any]] = []
    for region_id in range(1, id_counter + 1):
        ys, xs = np.where(flat == region_id)
        area = int(xs.size)
        if area < min_area:
            continue
        x0, y0 = int(xs.min()), int(ys.min())
        x1, y1 = int(xs.max()), int(ys.max())
        regions.append(
            {
                "bbox": (x0, y0, x1, y1),
                "area": area,
                "centroid": (float(xs.mean()), float(ys.mean())),
            }
        )
    # Sort by descending area so the largest region (the part silhouette)
    # is always regions[0].
    regions.sort(key=lambda r: r["area"], reverse=True)
    return regions


def _classify_shape_from_region(region: dict[str, Any], binary: np.ndarray) -> tuple[str, float]:
    """Heuristic shape classifier for a single connected region.

    Uses geometric cues derived from the binary silhouette:
        - Circularity (4*pi*area / perimeter^2) -> sphere / cylinder end-cap
        - Aspect ratio of the bounding box -> box vs cylinder vs cone
        - Taper ratio (top vs bottom width) -> cone vs cylinder

    Returns (shape_type, confidence) where shape_type is one of
    ``"sphere"``, ``"cylinder"``, ``"cone"``, ``"box"``.
    """
    x0, y0, x1, y1 = region["bbox"]
    bw = max(x1 - x0, 1)
    bh = max(y1 - y0, 1)
    aspect = bw / bh
    area = region["area"]

    # Perimeter approximation: count boundary pixels (4-neighbourhood).
    h, w = binary.shape
    sub = binary[y0 : y1 + 1, x0 : x1 + 1]
    # A pixel is on the perimeter if it is foreground and touches a border
    # or a background pixel.
    padded = np.pad(sub, 1, mode="constant", constant_values=0)
    interior = padded[1:-1, 1:-1] & padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
    perimeter_px = int(area - interior.sum())
    perimeter = max(perimeter_px, 4)
    circularity = (4.0 * np.pi * area) / (perimeter * perimeter)

    # Taper ratio: compare top and bottom slice widths.
    slice_h = max(bh // 5, 1)
    top_slice = binary[y0 : y0 + slice_h, x0 : x1 + 1]
    bottom_slice = binary[y1 - slice_h : y1 + 1, x0 : x1 + 1]
    top_width = int(top_slice.sum(axis=0).max()) if top_slice.size else 0
    bottom_width = int(bottom_slice.sum(axis=0).max()) if bottom_slice.size else 0
    taper = min(top_width, bottom_width) / max(top_width, bottom_width, 1)

    # Decision tree.
    if circularity > 0.78 and 0.8 <= aspect <= 1.25:
        return "sphere", float(min(circularity, 1.0))
    if taper < 0.55 and (aspect > 0.6):
        return "cone", float(min(1.0 - taper, 1.0))
    if 0.85 <= aspect <= 1.18 and circularity > 0.65:
        # Squarish + fairly round silhouette -> cylinder end-cap view.
        return "cylinder", float(min(circularity, 1.0))
    return "box", 0.6


def _extract_cv_geometry_params(
    filepath: Path,
) -> dict[str, Any] | None:
    """Run the CV pipeline on a single view image.

    Returns a dict with ``shape_type``, ``confidence`` and view-specific
    ``image_size`` / ``bbox_size`` on success, or ``None`` if the image
    could not be processed or no meaningful region was found.
    """
    try:
        binary = _preprocess_image_for_cv(filepath)
    except (OSError, ValueError) as e:
        logger.warning("CV preprocess failed for %s: %s", filepath, e)
        return None

    if binary.sum() == 0:
        logger.debug("Empty edge map for %s; skipping CV", filepath)
        return None

    regions = _find_connected_regions(binary)
    if not regions:
        logger.debug("No significant regions in %s", filepath)
        return None

    main = regions[0]
    shape, confidence = _classify_shape_from_region(main, binary)
    x0, y0, x1, y1 = main["bbox"]
    return {
        "shape_type": shape,
        "confidence": round(confidence, 3),
        "image_size": (int(binary.shape[1]), int(binary.shape[0])),
        "bbox_size": (int(x1 - x0), int(y1 - y0)),
        "area_px": int(main["area"]),
    }


def _merge_cv_results(
    view_results: dict[str, dict[str, Any]],
    image_sizes: dict[str, tuple[int, int]],
) -> dict[str, Any]:
    """Combine per-view CV outputs into a single geometry parameter dict.

    Shape voting: each view casts a weighted vote (by confidence) for its
    detected shape. The winning shape is used; if no view produced CV
    output we fall back to ``"box"``.

    Dimensions are derived from the bounding-box pixel extents, scaled by
    ``/10.0`` to map onto the same millimetre convention used by the
    legacy header-only path.
    """
    length, width, height = 50.0, 30.0, 20.0
    shape_votes: dict[str, float] = {}

    for view_name, cv in view_results.items():
        shape = cv["shape_type"]
        shape_votes[shape] = shape_votes.get(shape, 0.0) + cv["confidence"]
        bw, bh = cv["bbox_size"]
        scaled_w = max(bw / 10.0, 10.0)
        scaled_h = max(bh / 10.0, 10.0)
        if view_name == "front":
            length = scaled_w
            height = scaled_h
        elif view_name == "top":
            length = max(length, scaled_w)
            width = scaled_h
        elif view_name == "left":
            width = max(width, scaled_w)
            height = max(height, scaled_h)

    # Fall back to header-based image sizes when CV produced no regions
    # for a given view but we still have pixel dimensions available.
    if "front" not in view_results and "front" in image_sizes:
        length = max(image_sizes["front"][0] / 10.0, 10.0)
        height = max(image_sizes["front"][1] / 10.0, 10.0)
    if "top" not in view_results and "top" in image_sizes:
        width = max(image_sizes["top"][1] / 10.0, 10.0)
    if "left" not in view_results and "left" in image_sizes:
        width = max(image_sizes["left"][0] / 10.0, 10.0)

    if shape_votes:
        shape_type = max(shape_votes, key=shape_votes.get)  # type: ignore[arg-type]
    else:
        shape_type = "box"

    return {
        "shape_type": shape_type,
        "dimensions": {
            "length": round(length, 1),
            "width": round(width, 1),
            "height": round(height, 1),
        },
        "position": {"x": 0, "y": 0, "z": 0},
    }


def _get_image_dimensions(filepath: Path) -> tuple[int, int]:
    """Return (width, height) by parsing image file headers (stdlib only).

    Supports PNG, JPEG, GIF, BMP.  Raises ValueError for unsupported formats.
    """
    with open(filepath, "rb") as f:
        header = f.read(32)

    if len(header) < 2:
        raise ValueError(
            "图像文件解析失败：文件大小不足以包含有效的图像文件头。正常图像文件至少需要 2 字节。请确认上传的是有效的图像文件（PNG/JPEG/GIF/BMP 格式），而非空文件或损坏文件。"
        )

    if header[:8] == b"\x89PNG\r\n\x1a\n":
        if len(header) < 24:
            raise ValueError(
                "PNG 图像解析失败：PNG 文件头不完整。"
                "可能原因：1) 文件传输过程中被截断；2) 文件已损坏。"
                "请确认 PNG 文件完整性（标准 PNG 文件头为 8 字节）。"
            )
        w = struct.unpack(">I", header[16:20])[0]
        h = struct.unpack(">I", header[20:24])[0]
        return w, h

    if header[:2] == b"\xff\xd8":
        f.seek(0)
        data = f.read()
        i = 2
        _iter = 0
        while i < len(data) - 9:
            _iter += 1
            if _iter > 1000:
                raise ValueError("JPEG 图像解析失败：解析循环超过最大迭代次数（1000），可能存在损坏或恶意构造的数据。")
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if 0xC0 <= marker <= 0xC2:
                h = struct.unpack(">H", data[i + 5 : i + 7])[0]
                w = struct.unpack(">H", data[i + 7 : i + 9])[0]
                return w, h
            seg_len = struct.unpack(">H", data[i + 2 : i + 4])[0]
            i += 2 + seg_len
        raise ValueError(
            "JPEG 图像解析失败：未找到 SOF（Start Of Frame）标记。"
            "可能原因：1) JPEG 文件已损坏或格式不正确；"
            "2) 文件为渐进式 JPEG 但不支持解析。"
            "请检查 JPEG 文件是否为标准基线格式，"
            "或使用图像编辑工具重新保存。"
        )

    if header[:6] in (b"GIF87a", b"GIF89a"):
        w = struct.unpack("<H", header[6:8])[0]
        h = struct.unpack("<H", header[8:10])[0]
        return w, h

    if header[:2] == b"BM":
        if len(header) < 26:
            raise ValueError(
                "BMP 图像解析失败：BMP 文件头不完整。可能原因：1) 文件传输过程中被截断；2) 文件已损坏。标准 BMP 文件头至少 26 字节。请确认 BMP 文件完整性。"
            )
        w = struct.unpack("<I", header[18:22])[0]
        h = struct.unpack("<I", header[22:26])[0]
        return w, h

    raise ValueError(
        f"图像格式解析失败：不支持的图像格式。"
        f"文件头标识: {header[:4]!r}。"
        "支持的图像格式包括：PNG、JPEG、BMP。"
        "请将图像转换为支持的格式后重试。"
    )
