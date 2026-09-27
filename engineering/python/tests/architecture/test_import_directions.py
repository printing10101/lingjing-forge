"""架构导入方向测试 —— 轻量级（无外部依赖，纯文本匹配）。

验证重构后的关键导入规则：
  1. 域层不再从 API 层导入 get_current_user
  2. auth 层不再从 agent 层顶层导入（延迟导入除外）
  3. contracts 层不导入 config
"""

from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[2] / "app"


def _search_import(filepath: Path, pattern: str) -> bool:
    """检查文件是否包含指定的导入模式。"""
    try:
        text = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return pattern in text


# 规则 1: 域层不得从 app.api.v1.auth 导入 get_current_user

_DOMAIN_FILES = [
    "simulation/api.py",
    "dxf/api.py",
    "projects/project_api.py",
    "rag/routes.py",
    "rules/api.py",
]


@pytest.mark.parametrize("rel_path", _DOMAIN_FILES)
def test_domain_no_api_auth_import(rel_path: str) -> None:
    """验证域层文件不使用 from app.api.v1.auth import get_current_user。"""
    fp = APP_ROOT / rel_path
    if not fp.exists():
        pytest.skip(f"{rel_path} 不存在")
    has_violation = _search_import(fp, "from app.api.v1.auth import")
    assert not has_violation, (
        f"{rel_path} 仍从 API 层导入认证依赖，应改为 from app.auth.dependencies import get_current_user"
    )


# 规则 2: auth/audit, idempotency, rate_limiter 使用延迟导入

_AUTH_SHIM_FILES = [
    "auth/audit.py",
    "auth/idempotency.py",
    "auth/rate_limiter.py",
]


@pytest.mark.parametrize("rel_path", _AUTH_SHIM_FILES)
def test_auth_shim_uses_lazy_import(rel_path: str) -> None:
    """验证 auth re-export shim 使用延迟导入模式。"""
    fp = APP_ROOT / rel_path
    text = fp.read_text(encoding="utf-8")
    # 应有 __getattr__ 函数（延迟导入模式）
    has_lazy = "def __getattr__" in text
    # 不应有顶层 from app.agent 导入（违反单向依赖）；
    # 仅统计行首（无缩进）的导入语句——函数内懒导入不算
    has_top_level = any(line.startswith("from app.agent") for line in text.splitlines())
    assert has_lazy, f"{rel_path} 缺少 __getattr__ 延迟导入"
    assert not has_top_level, f"{rel_path} 仍有顶层 from app.agent 导入"


# 规则 3: shared/ 无重依赖

_FORBIDDEN_SHARED = ["import torch", "import numpy", "from pydantic", "from fastapi"]


def test_shared_no_heavy_imports() -> None:
    """验证 shared/ 层不导入重依赖。"""
    shared_dir = APP_ROOT.parents[2] / "shared"
    if not shared_dir.exists():
        pytest.skip("shared/ 不存在")
    violations = []
    for f in shared_dir.rglob("*.py"):
        for forbidden in _FORBIDDEN_SHARED:
            if _search_import(f, forbidden):
                violations.append(f"{f.name}: {forbidden}")
    assert not violations, f"shared/ 包含重依赖: {violations}"


# 规则 4: main.py 已将 shared/ 加入 sys.path


def test_main_adds_shared_to_path() -> None:
    """验证 main.py 将 monorepo 根目录加入 sys.path。"""
    main_py = APP_ROOT / "main.py"
    text = main_py.read_text(encoding="utf-8")
    has_sys_path = "sys.path.insert(0" in text or "sys.path.append(" in text
    has_repo_root = "_REPO_ROOT" in text or "parents[" in text
    assert has_sys_path and has_repo_root, "main.py 应确保 shared/ 在 Python 路径中"
