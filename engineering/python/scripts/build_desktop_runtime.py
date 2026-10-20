#!/usr/bin/env python3
"""构建桌面端嵌入式 Python 运行时（阶段 2 桌面开箱即用）。

把独立 Python 发行版（python-build-standalone，即 uv 下载的完整版）与
后端依赖（site-packages）、后端代码组装为**完全自包含**的目录，
供 Tauri ``bundle.resources`` 打包——目标机器无需预装任何 Python。

目录结构（产出）::

    <runtime-dir>/
    ├── runtime/            # 自包含 Python（python.exe + 标准库 + 依赖）
    │   └── Lib/site-packages/   # 后端依赖（含 CPU 版 torch，用于文本嵌入）
    ├── backend/            # 后端源码（app/、start_server.py、alembic、config）
    │   └── start_server.py
    └── runtime.json        # 元信息（版本、构建时间）

用法::

    # 本机（Windows）：
    python scripts/build_desktop_runtime.py
    # 指定来源：
    python scripts/build_desktop_runtime.py \\
        --python-src <uv-python-dir> --site-packages <venv>/Lib/site-packages \\
        --backend-src ../.. --runtime-dir ../desktop_runtime
    # 无本地依赖缓存时用 pip 安装（CI 用）：
    python scripts/build_desktop_runtime.py --install-deps

依赖来源：显式 --site-packages 参数复制；否则默认 pip 安装 requirements.txt（CI 与本机一致，避免宿主 venv 污染）。
"""
from __future__ import annotations

import argparse
import json
import hashlib
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # engineering/python
ENGINEERING = PROJECT_ROOT.parent                    # engineering

# CI Windows runner 的 stdout 默认是 cp1252，中文日志会 UnicodeEncodeError；
# 本地旧控制台同理。统一重配置为 UTF-8（errors=replace 兜底）。
for _stream in (sys.stdout, sys.stderr):
    _enc = getattr(_stream, "encoding", "") or ""
    if _enc.lower().replace("-", "") != "utf8":
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def log(msg: str) -> None:
    print(f"[build-runtime] {msg}", flush=True)


def find_uv_python() -> Path | None:
    """探测 uv 下载的完整版 Python（python-build-standalone，优先非 install_only）"""
    if sys.platform == "win32":
        base = Path.home() / "AppData/Roaming/uv/python"
        if base.is_dir():
            candidates = sorted((p for p in base.iterdir() if p.name.startswith("cpython-3.12")), reverse=True)
            # 优先完整版（含 Scripts/pip），install_only 仅作回退
            for p in candidates:
                if (p / "python.exe").exists() and (p / "Scripts").is_dir():
                    return p
            for p in candidates:
                if (p / "python.exe").exists():
                    return p
    else:
        base = Path.home() / ".local/share/uv/python"
        if base.is_dir():
            candidates = sorted((p for p in base.iterdir() if p.name.startswith("cpython-3.12")), reverse=True)
            for p in candidates:
                if (p / "bin" / "python3").exists() and (p / "bin" / "pip").exists():
                    return p
            for p in candidates:
                if (p / "bin" / "python3").exists():
                    return p
    return None


def copy_tree(src: Path, dst: Path, ignore: tuple[str, ...] = ()) -> int:
    """复制目录树，返回复制的文件数"""
    count = 0

    for root, dirs, files in shutil.os.walk(src, topdown=True):
        dirs[:] = [name for name in dirs if name not in ignore]
        rel = Path(root).relative_to(src)
        for f in files:
            if f in ignore or Path(f).suffix in ignore:
                continue
            s = Path(root) / f
            d = dst / rel / f
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, d)
            count += 1
    return count



def clear_dir(target: Path, *, allowed_root: Path) -> None:
    """清空目录内容（保留目录本身）。

    相比 shutil.rmtree + mkdir 的「先删后建」，本实现逐文件删除、
    逐子目录递归，避免在受限环境（如带安全删除守卫的沙箱）中
    因无法删除目录而失败；也避免构建中断时产物整体丢失。
    """
    target = target.absolute()
    root = allowed_root.resolve()
    if not target.resolve().is_relative_to(root) or target.resolve() == root:
        raise ValueError(f"Refusing to clear outside a runtime subdirectory: {target}")
    if not target.exists():
        target.mkdir(parents=True, exist_ok=True)
        return
    for child in target.iterdir():
        if child.is_symlink():
            child.unlink()
        elif getattr(child, "is_junction", lambda: False)():
            child.rmdir()
        elif child.is_dir():
            clear_dir(child, allowed_root=root)
            child.rmdir()
        else:
            child.unlink()


def validate_build_paths(
    python_src: Path, runtime_dir: Path, backend_src: Path,
    site_packages: Path | None = None,
) -> None:
    """Reject source/output overlap before any cleanup, including linked paths."""
    root = runtime_dir.resolve()
    destinations = [root / "runtime", root / "backend"]
    sources = [python_src.resolve(), *(backend_src.resolve() / sub for sub in ("app", "alembic", "config"))]
    if site_packages is not None:
        if not site_packages.is_dir():
            raise ValueError(f"Dependency source does not exist: {site_packages}")
        sources.append(site_packages.resolve())
    for destination in destinations:
        resolved = destination.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Output link escapes runtime directory: {destination}")
        for source in sources:
            if resolved.is_relative_to(source) or source.is_relative_to(resolved):
                raise ValueError(f"Build source and destination overlap: {source} / {resolved}")


RUNTIME_VALIDATION_CODE = """
import importlib, json, sys, time
names = ['fastapi', 'uvicorn', 'pydantic', 'ezdxf', 'cadquery', 'onnxruntime',
         'torch', 'sentence_transformers', 'chromadb']
modules, timings = {}, {}
for name in names:
    started = time.perf_counter()
    print('Validating import: ' + name, file=sys.stderr, flush=True)
    modules[name] = importlib.import_module(name)
    timings[name] = round(time.perf_counter() - started, 3)
import numpy as np
import casadi
assert casadi.__version__ == '3.8.1'
assert sys.version_info[:2] == (3, 12)
volume = modules['cadquery'].Workplane('XY').box(1, 2, 3).val().Volume()
assert abs(volume - 6.0) < 1e-8
assert modules['torch'].from_numpy(np.array([1.0, 2.0])).tolist() == [1.0, 2.0]
print(json.dumps({'python': sys.version.split()[0], 'cad_volume': volume,
                  'import_duration_seconds': timings,
                  'native_versions': {'casadi': casadi.__version__, 'numpy': np.__version__},
                  'imports': {name: str(getattr(mod, '__version__', 'unknown'))
                              for name, mod in modules.items()}}), flush=True)
"""


def validate_runtime(python_exe: Path) -> dict:
    """Readiness requires imports, native operations AND a clean process exit."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("PYTHONPATH", None)
    probe = subprocess.run([str(python_exe), "-X", "faulthandler", "-c", RUNTIME_VALIDATION_CODE],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=180, env=env)
    if probe.returncode != 0:
        raise RuntimeError(f"Runtime validation failed (exit={probe.returncode}): {probe.stderr[-4000:]}")
    try:
        result = json.loads(probe.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise RuntimeError("Runtime validation returned no valid report") from exc
    required_imports = {
        "fastapi", "uvicorn", "pydantic", "ezdxf", "cadquery", "onnxruntime",
        "torch", "sentence_transformers", "chromadb",
    }
    if not isinstance(result, dict):
        raise RuntimeError("Runtime validation report is incomplete")
    volume = result.get("cad_volume")
    imports = result.get("imports")
    if (not isinstance(imports, dict) or set(imports) != required_imports
            or not isinstance(volume, (int, float)) or isinstance(volume, bool)
            or not math.isfinite(volume) or abs(volume - 6.0) > 1e-8
            or not str(result.get("python", "")).startswith("3.12.")):
        raise RuntimeError("Runtime validation report is incomplete")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="构建桌面端嵌入式 Python 运行时")
    parser.add_argument("--runtime-dir", type=Path,
                        default=PROJECT_ROOT / "desktop_runtime",
                        help="运行时输出目录（默认 engineering/python/desktop_runtime）")
    parser.add_argument("--python-src", type=Path, default=None,
                        help="完整版 Python 目录（默认自动探测 uv 安装）")
    parser.add_argument("--site-packages", type=Path, default=None,
                        help="已安装依赖的 site-packages 目录（默认相邻 venv）")
    parser.add_argument("--backend-src", type=Path, default=PROJECT_ROOT,
                        help="后端源码根（默认 engineering/python）")
    parser.add_argument("--install-deps", action="store_true",
                        help="[兼容保留] 默认即用 pip 安装 requirements.txt（无 --site-packages 时）")
    parser.add_argument("--pip-index", default="https://mirrors.aliyun.com/pypi/simple/",
                        help="--install-deps 时的 pip 源")
    args = parser.parse_args()

    runtime_dir = args.runtime_dir.resolve()
    runtime_py = runtime_dir / "runtime"
    backend_dst = runtime_dir / "backend"

    # 1. 定位 Python 完整版
    python_src = args.python_src
    if python_src is None:
        python_src = find_uv_python()
    # 可执行文件名按平台区分：win32 为根目录 python.exe，posix 为 bin/python3。
    # 2026-09-10 修复：原判断在所有平台硬编码 python.exe，导致 Linux/macOS
    # 即使探测成功也必然报"未找到完整版 Python"（桌面构建 CI 连红的根因）。
    _py_entry = "python.exe" if sys.platform == "win32" else "bin/python3"
    if python_src is None or not (python_src / _py_entry).exists():
        log("ERROR: 未找到完整版 Python（可用 --python-src 指定 python-build-standalone 目录）")
        return 1
    log(f"Python 来源: {python_src}")
    validate_build_paths(python_src, runtime_dir, args.backend_src, args.site_packages)
    # An interrupted rebuild must not leave an old readiness manifest behind.
    (runtime_dir / "runtime.json").unlink(missing_ok=True)

    # 2. 复制 Python 运行时（排除冗余目录）
    log("复制 Python 运行时（含标准库）...")
    clear_dir(runtime_py, allowed_root=runtime_dir)
    n = copy_tree(python_src, runtime_py,
                  ignore=("__pycache__", "include", "libs", "tcl"))
    log(f"复制 Python 文件 {n} 个")

    # 2.5 裁剪 Tk/Tcl：后端为 headless FastAPI（UI 由 Tauri WebView 承载），
    # tkinter 纯属死重；且 linuxdeploy 会为 AppDir 内所有 ELF 解析依赖，
    # _tkinter 引用的 libtcl9tk9.0.so 在 runner 上无法解析，直接导致
    # AppImage 打包失败（run 35275632884 --verbose 日志实锤）
    log("裁剪 tkinter / tcl-tk 运行库...")
    prune_patterns = [
        # Python 扩展与包（Windows: DLLs + Lib；Linux: lib-dynload + stdlib）
        "DLLs/_tkinter*",
        "lib/python3*/lib-dynload/_tkinter*",
        "Lib/tkinter",
        "lib/python3*/tkinter",
        # Tcl/Tk 运行库与脚本（Windows: tcl/；Linux: lib/ 下散件与子目录）
        # 数字锚定防止 Windows 大小写不敏感 glob 误伤 Lib/threading.py
        "tcl",
        "lib/tcl[0-9]*",
        "lib/tk[0-9]*",
        "lib/itcl[0-9]*",
        "lib/thread[0-9]*",
        "DLLs/tcl*.dll",
        "DLLs/tk*.dll",
    ]
    pruned = 0
    for pat in prune_patterns:
        for p in runtime_py.glob(pat):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                try:
                    p.unlink()
                except OSError:
                    continue
            pruned += 1
    log(f"裁剪 {pruned} 项 tkinter/tcl-tk 组件")

    # 3. 依赖
    py_exe = runtime_py / ("python.exe" if sys.platform == "win32" else "bin/python3")
    if sys.platform == "win32":
        sp_dst = runtime_py / "Lib/site-packages"
    else:
        lib = runtime_py / "lib"
        pyver_dirs = sorted(lib.glob("python*")) if lib.is_dir() else []
        sp_dst = (pyver_dirs[0] / "site-packages") if pyver_dirs else lib / "site-packages"
    sp_dst.mkdir(parents=True, exist_ok=True)

    if args.site_packages is not None and args.site_packages.is_dir():
        # 显式指定 site-packages → 复制（开发快速路径，调用方对内容负责）
        n = copy_tree(args.site_packages, sp_dst, ignore=("__pycache__",))
        log(f"复制依赖 {n} 个文件（来自 {args.site_packages}）")
    else:
        # 默认（含 --install-deps 兼容标志）：pip 从 requirements.txt 安装。
        # 注意：不复制宿主 venv（2026-08-03 曾因 venv 混入 torch/casadi 等
        # 训练向包污染运行时 → NSIS/WiX 打包失败），requirements.txt 才是真源。
        req = PROJECT_ROOT / "requirements.txt"
        # torch 必须取 CPU 轮子：PyPI 的 torch 默认捆绑整套 CUDA 依赖
        # （nvidia-cudnn/nccl/cublas 等 ~3GB，实测 CI 下载 >1.5h）。用约束文件
        # 固定经过验证的 CPU 版本，并添加官方 CPU 索引供 pip 获取该轮子。
        # 注意 pip 的 --target 不会把已装进目标的包视为"已满足"，因此不能用
        # "先预装再装 requirements"的方式实现（2026-09-17 CI 实测无效）。
        # 工程侧 torch 仅用于 RAG 嵌入（见 requirements.txt 头注释），CPU 版即够。
        log(f"安装依赖到运行时（torch 取 CPU 轮子，其余来自 {args.pip_index}）...")
        constraints = PROJECT_ROOT / "requirements-desktop-constraints.txt"
        r = subprocess.run(
            [str(py_exe), "-m", "pip", "install", "--no-cache-dir",
             "--break-system-packages",  # python-build-standalone 带 PEP668 标记
             "--target", str(sp_dst), "-r", str(req),
             "-c", str(constraints),
             "--index-url", args.pip_index,
             "--extra-index-url", "https://download.pytorch.org/whl/cpu"],
        )
        if r.returncode != 0:
            log("ERROR: 依赖安装失败")
            return r.returncode

    # 3.5 site-packages 瘦身（2026-08-24 打包修复）：
    # 目标：把运行时从 ~2.2GB / 6.1 万文件 压到 ~1.76GB / 4.1 万文件，
    # 以适配 32 位 makensis（无 LARGEADDRESSAWARE，2GB 地址空间上限）
    # 与 Tauri 资源枚举耗时。三类可安全裁剪内容：
    #   a) >240 字符超长路径文件（Windows MAX_PATH 限制，NSIS 无法读取）：
    #      多来自 torch 的 licenses/third_party 嵌套许可证树，无法被 Python 导入。
    #   b) kubernetes / kubernetes_asyncio（≈76MB）：chromadb 仅在其分布式
    #      segment_directory 顶层 import（config.py 中以字符串默认值懒加载，
    #      桌面单机 LocalSegment 从不实例化），删除不影响 import chromadb。
    #   c) __pycache__ / *.pyc（≈373MB / 19803 文件）：Python 会在首次导入时
    #      按需重新生成字节码缓存，删除安全。
    if sys.platform == "win32":
        removed = 0
        for root, _dirs, files in os.walk(sp_dst):
            for name in files:
                p = Path(root) / name
                if len(str(p)) > 240:
                    try:
                        p.unlink()
                        removed += 1
                    except OSError:
                        pass
        if removed:
            log(f"清理 {removed} 个超长路径文件（Windows MAX_PATH，多来自 torch 许可证树）")

        for name in list(sp_dst.glob("kubernetes*")):
            shutil.rmtree(name, ignore_errors=True)
            log(f"移除 {name.name}（chromadb 分布式专用，桌面单机无需）")

        pyc_n = 0
        for root, dirs, _files in os.walk(sp_dst, topdown=True):
            for d in list(dirs):
                if d == "__pycache__":
                    p = Path(root) / d
                    try:
                        shutil.rmtree(p)
                        dirs.remove(d)
                    except OSError:
                        pass
            for f in os.listdir(root):
                if f.endswith(".pyc"):
                    try:
                        os.unlink(Path(root) / f)
                        pyc_n += 1
                    except OSError:
                        pass
        if pyc_n:
            log(f"清理 {pyc_n} 个 .pyc 字节码缓存（__pycache__ 目录一并移除，首次导入按需重建）")

    # 4. 后端代码
    log("复制后端代码...")
    clear_dir(backend_dst, allowed_root=runtime_dir)
    for sub in ("app", "alembic", "config"):
        s = args.backend_src / sub
        if s.is_dir():
            n = copy_tree(s, backend_dst / sub,
                          ignore=("__pycache__", "tests", "data", ".pyc"))
            log(f"复制 {sub}/ {n} 个文件")
    for f in ("start_server.py", "alembic.ini", "requirements.txt", "requirements-desktop-constraints.txt"):
        s = args.backend_src / f
        if s.is_file():
            shutil.copy2(s, backend_dst / f)

    # 5. 元信息 + 验证
    # 注意：python 字段必须是「运行时自带解释器」的版本，而非构建宿主解释器版本。
    # 2026-08-24 修复：此前用 platform.python_version()（宿主 3.11）写入元信息，
    # 而运行时实为 python-build-standalone 3.12，误导排障。
    info = {
        "schema": 1,
        "python_exe": "runtime/python.exe" if sys.platform == "win32" else "runtime/bin/python3",
        "backend_dir": "backend",
        "backend_entry": "backend/start_server.py",
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    log("验证运行时...")
    try:
        info["validation"] = validate_runtime(py_exe)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        log(f"ERROR: {exc}")
        return 1
    info["python"] = info["validation"]["python"]
    info["requirements_sha256"] = hashlib.sha256((PROJECT_ROOT / "requirements.txt").read_bytes()).hexdigest()
    info["constraints_sha256"] = hashlib.sha256((PROJECT_ROOT / "requirements-desktop-constraints.txt").read_bytes()).hexdigest()
    (runtime_dir / "runtime.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"运行时 Python: {info['validation']['python']}（依赖与原生计算验证通过）")

    # 估算体积
    total = sum(f.stat().st_size for f in runtime_dir.rglob("*") if f.is_file())
    log(f"完成: {runtime_dir}（约 {total / 1024 / 1024:.0f} MB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
