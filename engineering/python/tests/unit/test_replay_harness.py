"""灰度重放工具（M5）单元测试。

覆盖：种子输入集确定性生成、真实 pipeline 端到端重放（隔离灰度库）、
门控从双库判定串联。重放走真实 ChatterReportLoader / GeneratorAdapter /
SafetyValidator，不用 mock。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.gcode_generation.failure_case_store import FailureCaseStore
from app.gcode_generation.gcode_store import GCodeGenerationTaskStatus
from app.gcode_generation.regression_gate import GateThresholds
from app.gcode_generation.replay_harness import (
    gate_from_dbs,
    run_replay,
    write_seed_input_set,
)


@pytest.fixture()
def input_set(tmp_path: Path) -> list:
    return write_seed_input_set(tmp_path / "inputs")


# ----------------------------------------------------------------------
# 种子输入集
# ----------------------------------------------------------------------


class TestSeedInputSet:
    def test_ten_cases_written(self, input_set) -> None:
        assert [c.case_id for c in input_set] == [
            "case_a_stable",
            "case_b_unstable",
            "case_c_rejected",
            "case_d_plane_alu",
            "case_e_hole_alu",
            "case_f_profile_steel",
            "case_g_plane_titanium",
            "case_h_combo_steel",
            "case_i_profile_alu",
            "case_j_boss_steel",
        ]
        for c in input_set:
            assert c.chatter_path.exists()
            assert c.plan_path.exists()

    def test_sample_count_reaches_gate_min(self, input_set) -> None:
        """10 例达到回归门控 min_samples=10，一轮重放即可出可信判定。"""
        assert len(input_set) >= GateThresholds().min_samples

    def test_expected_outcomes_consistent(self, input_set) -> None:
        by_id = {c.case_id: c for c in input_set}
        assert by_id["case_a_stable"].expected_outcome == "success"
        assert by_id["case_b_unstable"].expected_hint == "UNSTABLE_FEATURES"
        assert by_id["case_c_rejected"].expected_hint == "ChatterReportLoadError"
        successes = [c for c in input_set if c.expected_outcome == "success"]
        assert len(successes) == 8

    def test_json_payloads_valid(self, input_set) -> None:
        report = json.loads(input_set[0].chatter_path.read_text(encoding="utf-8"))
        assert report["task_status"] == "succeeded"  # loader 校验的是小写值
        assert len(report["feature_results"]) == 2
        plan = json.loads(input_set[0].plan_path.read_text(encoding="utf-8"))
        assert plan["operations"], "operations 不能为空"
        # operations 的 feature_name 与 chatter feature_id 对齐（checkpoint 匹配依赖）
        op_features = {op["feature_name"] for op in plan["operations"]}
        ch_features = {f["feature_id"] for f in report["feature_results"]}
        assert op_features == ch_features

    def test_rejected_case_has_bad_status(self, input_set) -> None:
        report = json.loads(input_set[2].chatter_path.read_text(encoding="utf-8"))
        assert report["task_status"] != "SUCCEEDED"

    def test_deterministic(self, tmp_path: Path) -> None:
        c1 = write_seed_input_set(tmp_path / "r1")
        c2 = write_seed_input_set(tmp_path / "r2")
        for a, b in zip(c1, c2):
            assert a.chatter_path.read_bytes() == b.chatter_path.read_bytes()
            assert a.plan_path.read_bytes() == b.plan_path.read_bytes()


# ----------------------------------------------------------------------
# 真实重放（端到端，无 mock）
# ----------------------------------------------------------------------


class TestRunReplay:
    def test_replay_e2e(self, tmp_path: Path) -> None:
        write_seed_input_set(tmp_path / "inputs")
        report = run_replay(
            tmp_path / "inputs",
            tmp_path / "replay.db",
            work_dir=tmp_path / "work",
        )

        per_case = {c["case_id"]: c for c in report["per_case"]}
        # 全部案例走完真实管道
        assert set(per_case) == {
            "case_a_stable",
            "case_b_unstable",
            "case_c_rejected",
            "case_d_plane_alu",
            "case_e_hole_alu",
            "case_f_profile_steel",
            "case_g_plane_titanium",
            "case_h_combo_steel",
            "case_i_profile_alu",
            "case_j_boss_steel",
        }

        # case_a：预期成功 GENERATED
        assert per_case["case_a_stable"]["actual_outcome"] == (
            GCodeGenerationTaskStatus.GENERATED.value
        )

        # case_b：不稳定特征 → FAILED
        assert per_case["case_b_unstable"]["actual_outcome"] == (
            GCodeGenerationTaskStatus.FAILED.value
        )

        # case_c：报告拒载 → FAILED
        assert per_case["case_c_rejected"]["actual_outcome"] == (
            GCodeGenerationTaskStatus.FAILED.value
        )

        # 成功变体全部 GENERATED
        for cid in ["case_d_plane_alu", "case_e_hole_alu", "case_f_profile_steel",
                    "case_g_plane_titanium", "case_h_combo_steel", "case_i_profile_alu",
                    "case_j_boss_steel"]:
            assert per_case[cid]["actual_outcome"] == (
                GCodeGenerationTaskStatus.GENERATED.value
            ), f"{cid} 未按预期成功: {per_case[cid]}"

        # 灰度库落盘 10 条案例（隔离于生产库）
        store = FailureCaseStore(db_path=tmp_path / "replay.db")
        assert store.count() == 10

        # stats 报表结构完整：8 成功 2 失败
        stats = report["stats"]
        assert stats["total"] == 10
        assert stats["successes"] == 8
        assert stats["failures"] == 2
        assert stats["one_pass_rate"] == pytest.approx(0.8)
        assert "UNSTABLE_FEATURES" in stats["by_code"]
        assert "ChatterReportLoadError" in stats["by_code"]

    def test_replay_is_repeatable(self, tmp_path: Path) -> None:
        write_seed_input_set(tmp_path / "inputs")
        db = tmp_path / "replay.db"
        r1 = run_replay(tmp_path / "inputs", db, work_dir=tmp_path / "w1")
        r2 = run_replay(tmp_path / "inputs", db, work_dir=tmp_path / "w2")
        # 重放清库重来：两轮 stats 一致（确定性）
        assert r1["stats"]["total"] == r2["stats"]["total"]
        assert r1["stats"]["by_code"] == r2["stats"]["by_code"]

    def test_empty_input_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            run_replay(tmp_path / "nope", tmp_path / "x.db", work_dir=tmp_path / "w")


# ----------------------------------------------------------------------
# 门控串联
# ----------------------------------------------------------------------


class TestGateFromDbs:
    def test_gate_real_verdict_with_seed_samples(self, tmp_path: Path) -> None:
        """种子集 10 例达到 min_samples，两轮一致重放 → 真实判定（非 inconclusive）。"""
        write_seed_input_set(tmp_path / "inputs")
        base_db, cand_db = tmp_path / "b.db", tmp_path / "c.db"
        run_replay(tmp_path / "inputs", base_db, work_dir=tmp_path / "w1")
        run_replay(tmp_path / "inputs", cand_db, work_dir=tmp_path / "w2")

        gate = gate_from_dbs(base_db, cand_db)
        assert gate.passed
        assert not gate.inconclusive  # 样本足够，给出真实判定
        assert gate.baseline_summary["total"] == 10
        assert gate.current_summary["one_pass_rate"] == gate.baseline_summary["one_pass_rate"]

    def test_gate_blocks_real_regression(self, tmp_path: Path) -> None:
        """构造两期差异库验证拦截路径（不走重放，直接写库）。"""
        from app.gcode_generation.failure_case_store import FailureCase

        base = FailureCaseStore(db_path=tmp_path / "b.db")
        cand = FailureCaseStore(db_path=tmp_path / "c.db")
        # 基线：10 成功 0 失败；候选：10 中 5 失败 → 通过率 1.0 → 0.5
        for i in range(10):
            base.record(FailureCase(task_id=f"b-ok-{i}", outcome="success"))
        for i in range(5):
            cand.record(
                FailureCase(
                    task_id=f"c-fail-{i}",
                    outcome="failure",
                    source="safety_validator",
                    error_codes=["L3-DEPTH"],
                )
            )
        for i in range(5):
            cand.record(FailureCase(task_id=f"c-ok-{i}", outcome="success"))

        gate = gate_from_dbs(
            tmp_path / "b.db", tmp_path / "c.db", thresholds=GateThresholds(min_samples=10)
        )
        assert not gate.passed
        assert not gate.inconclusive
        assert any("一次通过率退化" in r for r in gate.reasons)
