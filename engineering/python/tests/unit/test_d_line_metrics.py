"""D 线指标脚本单测（I4 §3/§6 第 1 周交付）。

覆盖：切参指标纯函数（含历史版本无 source 字段兜底）、快照复现校验的
一致/不一致两路、v1 git 历史锚点读取。脚本经 importlib 按路径加载。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[4] / "engineering" / "python" / "scripts" / "d_line_metrics.py"
)
_spec = importlib.util.spec_from_file_location("d_line_metrics", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _historical_ref_available(ref: str) -> bool:
    """历史锚点是否可达。

    CI 默认是 depth=1 浅克隆，``git show ed31f09:...`` 会以退出码 128 失败
    （2026-10-22 run 39783411501 实测：unit 与 full suite 各挂一次）。
    这里显式探测，缺对象时跳过而不是把环境限制误报成回归。
    """
    try:
        subprocess.run(
            ["git", "-C", str(mod.REPO_ROOT), "cat-file", "-e", f"{ref}^{{commit}}"],
            check=True,
            capture_output=True,
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _records(sources: list[str | None]) -> list[dict]:
    """构造最小切参记录：material_m{i} × endmill，source 可缺省。"""
    return [
        {"material_id": f"material_m{i}", "tool_series": "endmill", "tool_material": "carbide", "source": s}
        for i, s in enumerate(sources)
    ]


class TestCpMetrics:
    def test_counts_and_coverage(self) -> None:
        records = _records(["a", "b", "a"])
        m = mod._cp_metrics_from_records(records)
        assert m["total_entries"] == 3
        assert m["materials"] == 3
        assert m["combos_covered"] == 3
        assert m["combo_domain"] == 3 * mod.TOOL_SERIES_COUNT
        assert m["combo_coverage"] == round(3 / 18, 4)
        assert m["by_source"] == {"a": 2, "b": 1}

    def test_missing_source_field_falls_back(self) -> None:
        """v1 历史锚点（ed31f09）的记录无 source 字段——兜底 no_source_field。"""
        records = [{k: v for k, v in r.items() if k != "source"} for r in _records([None, None])]
        m = mod._cp_metrics_from_records(records)
        assert m["by_source"] == {"no_source_field": 2}

    def test_dedup_by_combo_not_entry(self) -> None:
        # 同 (材料,工序) 不同刀具材料：组合只算 1
        records = [
            {"material_id": "material_x", "tool_series": "endmill", "tool_material": t, "source": "s"}
            for t in ("carbide", "HSS")
        ]
        m = mod._cp_metrics_from_records(records)
        assert m["total_entries"] == 2
        assert m["combos_covered"] == 1


class TestVerifySnapshot:
    def _fresh(self) -> dict:
        return {"level": "v1", "collected_at": "2026-10-15T00:00:00Z", "cutting_params": {"total_entries": 12}}

    def test_identical_ignoring_timestamp(self, tmp_path: Path) -> None:
        snap = tmp_path / "baseline_v1.json"
        snap.write_text(json.dumps(self._fresh()), encoding="utf-8")
        diffs = mod._verify_snapshot("v1", snap, self._fresh())
        assert diffs == []

    def test_mismatch_detected(self, tmp_path: Path) -> None:
        snap = tmp_path / "baseline_v1.json"
        snap.write_text(json.dumps(self._fresh()), encoding="utf-8")
        fresh = self._fresh()
        fresh["cutting_params"]["total_entries"] = 609
        diffs = mod._verify_snapshot("v1", snap, fresh)
        assert len(diffs) == 1
        assert "total_entries" in diffs[0]


class TestV1GitAnchor:
    @pytest.mark.skipif(not Path(mod.REPO_ROOT, ".git").exists(), reason="非 git 仓库")
    @pytest.mark.skipif(
        not _historical_ref_available(mod.V1_GIT_REF),
        reason="历史锚点不可达（浅克隆或缺少对象，CI 需 fetch-depth: 0）",
    )
    def test_v1_git_ref_reads_12_seed_entries(self) -> None:
        """v1 历史锚点（ed31f09）应读到 12 条、无 source 字段的 seed 数据。"""
        m = mod._cutting_param_metrics(git_ref=mod.V1_GIT_REF)
        assert m["total_entries"] == 12
        assert m["by_source"] == {"no_source_field": 12}
        assert m["git_ref"] == mod.V1_GIT_REF


def test_honesty_notes_always_carried() -> None:
    """I4 §3 诚实说明列必须随每个快照输出（缺列视为不合格）。"""
    for level in ("v1", "v2", "v3"):
        assert mod.HONESTY_NOTES, level
        assert any("覆盖" in n for n in mod.HONESTY_NOTES)
