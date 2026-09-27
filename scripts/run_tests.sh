#!/usr/bin/env bash
# 灵境制造 工程侧测试运行器
#
# 用法（参数透传给 pytest）:
#   scripts/run_tests.sh                      # 全量（默认收集 engineering/python/tests）
#   scripts/run_tests.sh -m unit              # 只跑单元测试
#   scripts/run_tests.sh -q engineering/python/tests/unit/test_data_flywheel_plugin.py
#
# 为什么必须用这个脚本而不是裸 pytest:
#   1. 桌面宿主环境注入的 PYTHONPATH 可能含额外目录，
#      会遮蔽 tests.utils 命名空间 → ModuleNotFoundError
#   2. PATH 首位的 python 可能是桌面宿主 venv（无 pytest）；
#      仓库 .venv 的 pydantic_core 已损坏 → 必须用系统 Python 3.11
#
# 科研侧测试（research/）是独立环境，不适用本脚本：
#   cd research && pytest tests/
set -euo pipefail
cd "$(dirname "$0")/.."

unset PYTHONPATH

# 解释器探测（优先级从高到低）:
#   1. PYTHON_BIN 环境变量显式指定
#   2. py -3.14（Windows launcher → 系统 Python 3.14，OCP/pydantic_core 依赖可加载）
#   3. python（回退；注意 PATH 中的 python 可能是桌面宿主 venv，没有 pytest）
run_pytest() {
    if [ -n "${PYTHON_BIN:-}" ]; then
        exec "$PYTHON_BIN" -m pytest "$@"
    fi
    if command -v py >/dev/null 2>&1 && py -3.14 -m pytest --version >/dev/null 2>&1; then
        exec py -3.14 -m pytest "$@"
    fi
    exec python -m pytest "$@"
}

run_pytest "$@"
