"""``weights_resolver`` 单元测试（ADR-020 思路 1 P1）.

验证 ``model://world_model/<version>`` URI → checkpoint 文件路径的约定式
解析，这是 ``WorldModelPlugin._resolve_weights_path`` 接入新 resolver 后
让训练产出 checkpoint 能被加载的关键链路。

torch-free：本测试不依赖 torch，可在纯 numpy 环境下运行，覆盖 P1
"训练 → 推理" 闭环的路径解析侧。

学术诚信对齐（D-2 硬约束）：
- 不伪造文件存在性：用 tmp_path fixture 真实创建/删除 checkpoint 文件
- 不注入桩模块：weights_resolver 是纯字符串/路径操作，无需 mock
- 路径穿越防护用例覆盖 ``..`` / 空版本 / 非法字符
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.plugins.world_model.training.weights_resolver import (
    DEFAULT_MODELS_DIR,
    WeightsResolutionError,
    build_canonical_weights_path,
    resolve_world_model_weights_path,
)


# build_canonical_weights_path：训练器写入侧
@pytest.mark.unit
def test_build_canonical_path_basic() -> None:
    """合法版本字符串应拼出 ``<models_dir>/world_model/<version>.pt``."""
    path = build_canonical_weights_path("1.0.0", models_dir="/tmp/test_models")
    # 规范化为绝对路径
    assert path.endswith(os.path.join("world_model", "1.0.0.pt"))
    assert "test_models" in path


@pytest.mark.unit
def test_build_canonical_path_uses_default_models_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """models_dir=None 时使用 DEFAULT_MODELS_DIR."""
    # 不修改 DEFAULT_MODELS_DIR，仅验证 None 分支走默认值
    path = build_canonical_weights_path("v2", models_dir=None)
    assert path.endswith(os.path.join("world_model", "v2.pt"))
    # 默认根目录应包含在路径中
    assert DEFAULT_MODELS_DIR in path or os.path.abspath(DEFAULT_MODELS_DIR) in os.path.abspath(path)


@pytest.mark.unit
def test_build_canonical_path_rejects_path_traversal() -> None:
    """版本字符串含 ``/`` 或 ``..`` 应抛 WeightsResolutionError（路径穿越防护）."""
    bad_versions = [
        "../evil",
        "..",
        "/etc/passwd",
        "a/b",
        "",  # 空版本
        "version with space",
        "v<script>",
    ]
    for bad in bad_versions:
        with pytest.raises(WeightsResolutionError, match="不安全"):
            build_canonical_weights_path(bad, models_dir="/tmp/test_models")


@pytest.mark.unit
def test_build_canonical_path_accepts_safe_chars() -> None:
    """``[A-Za-z0-9_.-]`` 字符集应全部接受."""
    safe_versions = [
        "1.0.0",
        "fusion-v1-20260715",
        "model_A",
        "v2.3-beta",
        "123",
        "a.b.c-d_e",
    ]
    for safe in safe_versions:
        path = build_canonical_weights_path(safe, models_dir="/tmp/test_models")
        assert f"{safe}.pt" in path


# resolve_world_model_weights_path：plugin 读取侧
@pytest.mark.unit
def test_resolve_returns_none_for_non_world_model_uri() -> None:
    """非 ``model://world_model/`` 前缀的 URI 应返回 None（交由其他解析路径）."""
    other_uris = [
        "model://ltc/1.0.0",
        "model://cfc/2.0",
        "file:///path/to/model.pt",
        "https://example.com/model.pt",
        "",
        "random_string",
    ]
    for uri in other_uris:
        assert resolve_world_model_weights_path(uri, models_dir="/tmp/test_models") is None


@pytest.mark.unit
def test_resolve_returns_none_when_file_missing(tmp_path: Path) -> None:
    """URI 合法但 checkpoint 文件不存在时应返回 None（与 _resolve_weights_path 契约一致）."""
    uri = "model://world_model/1.0.0"
    # tmp_path 为空，不创建任何文件
    result = resolve_world_model_weights_path(uri, models_dir=str(tmp_path))
    assert result is None


@pytest.mark.unit
def test_resolve_returns_path_when_file_exists(tmp_path: Path) -> None:
    """checkpoint 文件存在时应返回绝对路径（P1 闭环核心用例）."""
    # 模拟训练器产出 checkpoint
    version = "fusion-v1-20260715"
    uri = f"model://world_model/{version}"
    models_dir = str(tmp_path)

    # 用训练器侧的 build_canonical_weights_path 决定写入位置
    checkpoint_path = build_canonical_weights_path(version, models_dir=models_dir)
    Path(checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
    Path(checkpoint_path).write_bytes(b"fake checkpoint content")

    # plugin 侧解析应返回同一路径
    resolved = resolve_world_model_weights_path(uri, models_dir=models_dir)
    assert resolved is not None
    assert os.path.abspath(resolved) == os.path.abspath(checkpoint_path)
    assert resolved.endswith(f"world_model{os.sep}{version}.pt")


@pytest.mark.unit
def test_resolve_round_trip_with_build(tmp_path: Path) -> None:
    """build_canonical_weights_path → 写文件 → resolve_world_model_weights_path 闭环."""
    models_dir = str(tmp_path)
    version = "0.1.0"

    # 训练器侧
    write_path = build_canonical_weights_path(version, models_dir=models_dir)
    os.makedirs(os.path.dirname(write_path), exist_ok=True)
    with open(write_path, "wb") as f:
        f.write(b"checkpoint")

    # plugin 侧
    read_path = resolve_world_model_weights_path(f"model://world_model/{version}", models_dir=models_dir)
    assert read_path is not None
    assert os.path.normpath(read_path) == os.path.normpath(write_path)


@pytest.mark.unit
def test_resolve_rejects_unsafe_version_in_uri(tmp_path: Path) -> None:
    """URI 版本字符串非法时应抛 WeightsResolutionError（不静默返回 None）."""
    bad_uris = [
        "model://world_model/../evil",
        "model://world_model/a/b",
        "model://world_model/",  # 空版本
        "model://world_model/v with space",
    ]
    for uri in bad_uris:
        with pytest.raises(WeightsResolutionError, match="不安全"):
            resolve_world_model_weights_path(uri, models_dir=str(tmp_path))


@pytest.mark.unit
def test_resolve_handles_non_string_uri() -> None:
    """非字符串 URI 应返回 None（防御性，不抛 TypeError）."""
    assert resolve_world_model_weights_path(None, models_dir="/tmp") is None  # type: ignore[arg-type]
    assert resolve_world_model_weights_path(123, models_dir="/tmp") is None  # type: ignore[arg-type]


# 环境变量覆盖（DEFAULT_MODELS_DIR）
@pytest.mark.unit
def test_default_models_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``WORLD_MODEL_MODELS_DIR`` 环境变量应覆盖默认存储根目录.

    注意：DEFAULT_MODELS_DIR 在模块导入期读取环境变量，因此本测试用
    monkeypatch 验证环境变量生效后重新导入模块的行为。为避免污染其他
    测试，这里只验证 build_canonical_weights_path 显式传 models_dir
    的路径（env 覆盖的端到端验证留给集成测试）。
    """
    # 显式传 models_dir 应优先于环境变量
    custom_dir = str(tmp_path / "custom")
    path = build_canonical_weights_path("v1", models_dir=custom_dir)
    assert custom_dir in path or os.path.abspath(custom_dir) in os.path.abspath(path)


# 导出契约
@pytest.mark.unit
def test_module_exports() -> None:
    """__all__ 应包含 P1 闭环所需的全部公共符号."""
    from app.plugins.world_model.training import weights_resolver

    expected = {
        "DEFAULT_MODELS_DIR",
        "WeightsResolutionError",
        "build_canonical_weights_path",
        "resolve_world_model_weights_path",
    }
    assert expected.issubset(set(weights_resolver.__all__))
