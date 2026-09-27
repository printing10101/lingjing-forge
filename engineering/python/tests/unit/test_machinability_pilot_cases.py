"""machinability pilot 案例族的最小回归测试（F2 M1）。

每个设计族在中间难度档（d3）实际构建一次并导出，保证真值可复现；
全量 50 案例的批量生成由 CLI manifest 把关，不在单测里跑（避免拖慢 CI）。
"""

from __future__ import annotations

import pytest

from app.benchmarks.machinability_pilot.case_families import (
    DIFFICULTY_TIERS,
    FAMILY_REGISTRY,
    all_case_ids,
)
from app.benchmarks.machinability_pilot.generate_cases import generate_case

MIN_VOLUME_MM3 = 100.0


def test_registry_has_50_cases() -> None:
    assert len(FAMILY_REGISTRY) == 10
    assert len(all_case_ids()) == 50
    assert DIFFICULTY_TIERS == ("d1", "d2", "d3", "d4", "d5")


@pytest.mark.parametrize("family_id", sorted(FAMILY_REGISTRY))
def test_family_builds_and_exports_at_d3(family_id: str, tmp_path) -> None:
    family = FAMILY_REGISTRY[family_id]
    result = generate_case(family, "d3", tmp_path)

    assert result.ok, f"{family_id} d3 构建失败: {result.error}"
    assert result.brep_valid
    assert result.volume_mm3 is not None and result.volume_mm3 > MIN_VOLUME_MM3

    case_dir = tmp_path / f"{family_id}-d3"
    for filename in ("spec.json", "prompt.txt", "truth.step", "truth.stl"):
        assert (case_dir / filename).exists(), f"缺少产物 {filename}"

    prompt = (case_dir / "prompt.txt").read_text(encoding="utf-8")
    assert len(prompt) > 40, "几何描述过短，不足以作为 LLM 生成输入"


def test_invalid_difficulty_is_rejected() -> None:
    family = FAMILY_REGISTRY["F01"]
    with pytest.raises(ValueError):
        family.params_for("d9")  # type: ignore[arg-type]
