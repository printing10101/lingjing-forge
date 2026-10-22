"""回归测试：世界模型 checkpoint 默认解析链（P0-1 生产接入）.

背景
----
``DEFAULT_MODELS_DIR`` 以 **cwd** 为基准。训练脚本在仓库根写
``<repo>/data/models``，而后端 sidecar 的 cwd 是 ``engineering/python``，
默认解析会落到 ``engineering/python/data/models`` —— "训练好了但生产加载不到"。
本测试锁定修复后的候选目录链：显式目录 > env/cwd > 仓库根。

不依赖 torch（纯路径解析）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.plugins.world_model.training import weights_resolver as wr


def test_candidate_dirs_include_repo_root_data_models() -> None:
    """默认链应包含仓库根 data/models（目录存在时）。"""
    dirs = wr._candidate_model_dirs(None)
    assert dirs, "候选目录不能为空"

    repo_dir = wr._repo_root_models_dir()
    if repo_dir is None:
        pytest.skip("未定位到仓库根 data/models（目录不存在）")

    resolved = {d.resolve() for d in dirs}
    assert repo_dir.resolve() in resolved


def test_explicit_models_dir_takes_precedence(tmp_path: Path) -> None:
    """显式传入 models_dir 时只使用该目录（不混入默认链）。"""
    dirs = wr._candidate_model_dirs(str(tmp_path))
    assert dirs == [tmp_path]


def test_resolve_finds_checkpoint_under_repo_root() -> None:
    """默认解析链应能找到已生成的 1.0.0 checkpoint。"""
    resolved = wr.resolve_world_model_weights_path("model://world_model/1.0.0")
    if resolved is None:
        pytest.skip("checkpoint 尚未生成（构建产物缺失），跳过")
    assert Path(resolved).name == "1.0.0.pt"
    assert Path(resolved).is_file()


def test_resolve_returns_none_for_missing_version(tmp_path: Path) -> None:
    """版本不存在时返回 None（保持既有 None = 随机初始化契约）。"""
    assert wr.resolve_world_model_weights_path("model://world_model/9.9.9", str(tmp_path)) is None


def test_resolve_rejects_unsafe_version(tmp_path: Path) -> None:
    """路径穿越版本号必须抛错，不能静默降级。"""
    with pytest.raises(wr.WeightsResolutionError):
        wr.resolve_world_model_weights_path("model://world_model/../../etc/passwd", str(tmp_path))


def test_build_canonical_path_matches_resolve(tmp_path: Path) -> None:
    """写入路径与解析路径必须互逆（训练 → 推理闭环）。"""
    target = Path(wr.build_canonical_weights_path("1.2.3", str(tmp_path)))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"stub")

    resolved = wr.resolve_world_model_weights_path("model://world_model/1.2.3", str(tmp_path))
    assert resolved is not None
    assert Path(resolved).resolve() == target.resolve()
