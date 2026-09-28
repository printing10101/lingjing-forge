"""batch_evolution_cases 批量自产进化案例脚本 单元测试。

覆盖：
- 矩阵展开：条数达验收线（≥200）、确定性、批次前缀
- A 轴：切深比 >1 判 unstable、<1 判 stable、边缘带低裕度
- B 轴：参数越界为 clamp 正样本（预期成功）
- C 轴：非法 task_status 案例
- 输入集落盘：chatter/plan JSON 必备键齐全（tmp_path，不触生产库）

不跑管线（那是批量脚本 --write-only 与验收的职责），只测矩阵与文件形状。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import batch_evolution_cases as bc  # noqa: E402


class TestMatrix:
    def test_count_meets_target(self):
        assert len(bc.build_matrix()) >= 200

    def test_deterministic(self):
        assert bc.build_matrix() == bc.build_matrix()

    def test_all_task_ids_carry_batch_prefix(self):
        for case in bc.build_matrix():
            assert case.chatter["task_id"].startswith(bc.BATCH_PREFIX + "-")

    def test_axis_a_ratio_sweep_marks_stability(self):
        unstable = [c for c in bc.build_matrix() if c.chatter["feature_results"][0]["stable"] is False]
        assert unstable, "切深比 >1 的案例应标记 unstable"
        for case in unstable:
            feat = case.chatter["feature_results"][0]
            assert feat["axial_depth_mm"] >= feat["limit_depth_mm"]
            assert case.expected == "failure"

    def test_axis_b_boundary_cases_expect_clamped_success(self):
        b_cases = [c for c in bc.build_matrix() if c.case_id.startswith("b_")]
        assert len(b_cases) == len(bc.FEED_VARIANTS) * len(bc.SPEED_VARIANTS) * len(bc.FAMILIES)
        assert all(c.expected == "success" for c in b_cases)

    def test_axis_c_bad_status_cases(self):
        c_cases = [c for c in bc.build_matrix() if c.case_id.startswith("c_")]
        assert {c.chatter["task_status"] for c in c_cases} == set(bc.BAD_STATUSES)


class TestInputSetShapes:
    def test_write_input_set_produces_valid_pairs(self, tmp_path: Path):
        cases = bc.build_matrix()[:3]
        root = bc.write_input_set(cases, tmp_path)
        for case in cases:
            chatter = json.loads((root / case.case_id / "chatter_report.json").read_text(encoding="utf-8"))
            plan = json.loads((root / case.case_id / "operation_plan.json").read_text(encoding="utf-8"))
            assert chatter["task_status"] in ("succeeded", *bc.BAD_STATUSES)
            assert chatter["feature_results"]
            assert plan["operations"] and plan["setups"]
            op = plan["operations"][0]
            assert op["machining_method"] and op["cutting_params"]["geometry"]
