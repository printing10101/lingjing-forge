#!/usr/bin/env bash
# 本地 CI 门禁模拟（对齐 .github/workflows/pr.yml + ci.yml 的 Python 侧检查）
#
# 用法:
#   scripts/ci_check.sh                 # 完整检查（version_sync + ruff + 单元测试）
#   scripts/ci_check.sh -m unit -q ... # 追加参数透传给 pytest
#
# 覆盖:
#   1. version_sync.py --check   （版本一致性，ci.yml 门禁）
#   2. ruff check + format check （pr.yml 门禁，工程侧 app/）
#   3. 单元测试                  （pr.yml/ci.yml 门禁）
set -euo pipefail
cd "$(dirname "$0")/.."

unset PYTHONPATH

# 与 run_tests.sh 相同的解释器探测：优先 py -3.14（系统 Python 3.14，见 AGENTS.md 环境说明）
PY() {
    if command -v py >/dev/null 2>&1 && py -3.14 -m pytest --version >/dev/null 2>&1; then
        py -3.14 "$@"
    else
        python "$@"
    fi
}

echo "==> [1/3] 版本一致性 (scripts/version_sync.py --check)"
PY scripts/version_sync.py --check

echo "==> [2/3] ruff 静态检查 (app/ check + format)"
if PY -m ruff --version >/dev/null 2>&1; then
    (cd engineering/python && PY -m ruff check app/ && PY -m ruff format --check app/)
else
    echo "    WARN: ruff 不可用，跳过（CI 会执行此项，本地请安装）"
fi

echo "==> [3/3] 单元测试"
# 覆盖率由 CI 显式传 --cov 启用（pytest.ini 已不再全局注入），本地默认全速运行
bash scripts/run_tests.sh -m unit -q "$@"

echo "==> 全部通过 ✓"
