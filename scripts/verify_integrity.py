#!/usr/bin/env python3
"""
全局导入链完整性验证脚本 (Global Import Integrity Checker)

作用：
1. 扫描 app/ 下所有 Python 模块。
2. 尝试导入每一个模块，捕获 ImportError 和 AttributeError。
3. 检测缺失的 __init__.py 文件。
4. 确保所有修复都在同一条“执行线”上。

使用方法：
    cd python
    python ../scripts/verify_integrity.py
"""

import sys
import os
import importlib
import pkgutil
from pathlib import Path

# 将 engineering/python/ 目录加入 sys.path（2.7 起后端位于 engineering/python）
project_root = Path(__file__).resolve().parent.parent / "engineering" / "python"
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 切换到 engineering/python 目录以模拟真实运行环境
os.chdir(project_root)

# 加载 .env 文件（如果存在）
env_file = project_root.parent.parent / ".env"
if env_file.exists():
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                # 移除引号
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                elif value.startswith("'") and value.endswith("'"):
                    value = value[1:-1]
                # 设置环境变量（如果未设置）
                if key and key not in os.environ:
                    os.environ[key] = value


def check_missing_inits(base_dir: Path):
    """检查包含 .py 文件但缺少 __init__.py 的目录（非命名空间包）"""
    missing = []
    for root, dirs, files in os.walk(base_dir):
        # 跳过测试目录和虚拟环境
        if "tests" in root or "venv" in root or "__pycache__" in root:
            continue

        py_files = [f for f in files if f.endswith(".py") and f != "__init__.py"]
        has_init = "__init__.py" in files

        if py_files and not has_init:
            # 这是一个包含代码但没有 __init__.py 的包
            missing.append(root)
    return missing


def check_imports():
    """尝试导入 app 包下的所有模块"""
    errors = []

    try:
        import app
    except ImportError as e:
        print(f"致命错误: 无法导入根包 'app': {e}")
        return False

    # 遍历 app 下的所有子模块
    for importer, modname, ispkg in pkgutil.walk_packages(path=app.__path__, prefix="app.", onerror=lambda x: None):
        try:
            # 尝试导入模块
            mod = importlib.import_module(modname)

            # 如果是包，检查其 __all__ 或属性是否可访问
            if ispkg and hasattr(mod, "__all__"):
                for name in mod.__all__:
                    try:
                        getattr(mod, name)
                    except AttributeError as e:
                        errors.append(f"[属性缺失] {modname}.{name} -> {e}")

        except ImportError as e:
            errors.append(f"[导入失败] {modname} -> {e}")
        except Exception as e:
            # 捕获其他初始化错误
            errors.append(f"[初始化错误] {modname} -> {type(e).__name__}: {e}")

    return errors


def main():
    print("=" * 60)
    print("灵境制造 - 全局导入链完整性验证")
    print("=" * 60)

    app_dir = project_root / "app"
    if not app_dir.exists():
        print(f"错误: 找不到 app 目录 ({app_dir})")
        sys.exit(1)

    # 1. 检查缺失的 __init__.py
    print("\n[1/2] 检查缺失的 __init__.py ...")
    missing_inits = check_missing_inits(app_dir)
    if missing_inits:
        print("  发现以下目录包含 .py 文件但缺少 __init__.py:")
        for m in missing_inits:
            print(f"     - {m}")
        print("  建议: 使用 `touch <dir>/__init__.py` 修复")
    else:
        print("  所有包目录均包含 __init__.py")

    # 2. 检查导入链
    print("\n[2/2] 扫描并尝试导入所有模块 ...")
    errors = check_imports()

    if errors:
        print(f"  发现 {len(errors)} 个导入/属性错误:")
        for err in errors:
            print(f"     - {err}")
    else:
        print("  所有模块导入成功，属性引用正确")

    # 总结
    print("\n" + "=" * 60)
    if missing_inits or errors:
        print("验证失败: 存在结构性问题，请修复后重试。")
        sys.exit(1)
    else:
        print("验证通过: 导入链完整，类名匹配，无缺失文件。")
        sys.exit(0)


if __name__ == "__main__":
    main()
