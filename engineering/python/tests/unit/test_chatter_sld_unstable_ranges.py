"""SLD 不稳定转速区间（下包络判定）回归测试。

背景：2026-10-23 前 `_compute_unstable_ranges` 逐叶瓣独立扫描并分别报区间，
导致同一转速被重复报（vmc_850/d10、depth=40 mm 时 lobe5 的 1000–1062 与
lobe4 的 1000–1274 完全重叠，输出 6 段而实际只有 4 段），且在重叠转速段上
"某叶瓣稳定、另一叶瓣不稳定"时会给出互相矛盾的结论。改为按下包络判定。
"""

from __future__ import annotations

import pytest

from app.simulation.chatter.api import _compute_unstable_ranges
from app.simulation.chatter.stability import (
    DEFAULT_TOOL_PARAMS,
    ChatterParams,
    ToolParams,
    compute_stability_limit,
    compute_stability_lobe,
    get_machine_params,
)

pytestmark = pytest.mark.unit

MACHINE, TOOL = "vmc_850", "endmill_d10"


@pytest.fixture(scope="module")
def lobe_data():
    mp = get_machine_params(MACHINE)
    tp = ToolParams(tool_id=TOOL, **DEFAULT_TOOL_PARAMS[TOOL])
    lobe = compute_stability_lobe(mp, tp, speed_range=(1000, 10000), num_points=400, num_lobes=6)
    envelope = compute_stability_limit(ChatterParams(spindle_rpm=8000, machine=mp, tool=tp))
    return lobe, envelope


def _total(rs: list[dict]) -> float:
    return sum(r["speed_end"] - r["speed_start"] for r in rs)


def test_no_ranges_below_envelope(lobe_data):
    """切深低于绝对包络最低极限时，不存在不稳定转速。"""
    _, envelope = lobe_data
    for depth in (1.0, 2.0, 5.0, envelope * 0.99):
        assert _compute_unstable_ranges(lobe_data[0], depth) == []


def test_ranges_appear_just_above_envelope(lobe_data):
    """切深刚超过包络即必须报出不稳定区间（不能漏报）。"""
    lob, envelope = lobe_data
    rs = _compute_unstable_ranges(lob, envelope * 1.01)
    assert len(rs) >= 1
    # 每个区间都应是有限、正宽度的转速段
    for r in rs:
        assert r["speed_end"] > r["speed_start"] > 0


def test_ranges_never_overlap(lobe_data):
    """任何切深下区间互不重叠——这是逐叶瓣实现会违反的不变量。"""
    lob, _ = lobe_data
    for depth in (10.6, 15.0, 20.0, 40.0, 60.0):
        segs = sorted((r["speed_start"], r["speed_end"]) for r in _compute_unstable_ranges(lob, depth))
        for a, b in zip(segs, segs[1:]):
            assert b[0] >= a[1], f"depth={depth} 出现重叠：{a} / {b}"


def test_envelope_collapses_redundant_lobes(lobe_data):
    """深切时应把重叠叶瓣合并：区间数少于叶瓣数。"""
    lob, _ = lobe_data
    rs = _compute_unstable_ranges(lob, 40.0)
    assert len(rs) <= len([l for l in lob["lobes"] if l[0]])
    # lobe5 最低限深显著高于其余叶瓣，其转速段应被更低的包络接管
    assert len(rs) < len([l for l in lob["lobes"] if l[0]])


def test_monotonic_in_depth(lobe_data):
    """切深越大，不稳定总转速跨度不减。"""
    lob, _ = lobe_data
    totals = [_total(_compute_unstable_ranges(lob, d)) for d in (10.6, 15.0, 20.0, 40.0)]
    for a, b in zip(totals, totals[1:]):
        assert b >= a - 1e-6


def test_ranges_within_covered_speed_span(lobe_data):
    """区间不得越出叶瓣实际覆盖的转速范围（覆盖外是"未计算"，不是"稳定"）。"""
    lob, _ = lobe_data
    speeds = [s for l in lob["lobes"] if l[0] for s in l[0]]
    lo, hi = min(speeds), max(speeds)
    for depth in (10.6, 20.0, 40.0):
        for r in _compute_unstable_ranges(lob, depth):
            assert lo <= r["speed_start"] and r["speed_end"] <= hi


def test_empty_input_is_safe():
    """空叶瓣输入返回空列表，不抛异常。"""
    assert _compute_unstable_ranges({"lobes": []}, 40.0) == []
    assert _compute_unstable_ranges({"lobes": [([], [])]}, 40.0) == []


def test_merged_ranges_marked_as_envelope(lobe_data):
    """合并结果的 lobe 字段为 -1，表明不隶属单一叶瓣。"""
    lob, _ = lobe_data
    rs = _compute_unstable_ranges(lob, 20.0)
    assert rs and all(r["lobe"] == -1 for r in rs)
