"""行业标准对齐的门禁单测（stdlib unittest，零第三方依赖）。

覆盖点：
1. 日期感知有效性：GB 15760-2025 在实施日前后状态不同；
2. 废止引用被拦截，并给出替代标准；
3. 条款级未取得全文时 `clause_level` 恒为 unverified、`may_claim_compliance` 恒为 False；
4. 无对应国标的规则记为 gap，不硬凑映射；
5. 合规表述检测能命中"符合国标""依据第 X 条"这类越权写法；
6. 全量审计的计数与规则数一致。
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent / "standard_alignment.py"
_MODULE_NAME = "standard_alignment_under_test"
_spec = importlib.util.spec_from_file_location(_MODULE_NAME, _MODULE_PATH)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules[_MODULE_NAME] = _module
_spec.loader.exec_module(_module)

StandardRegistry = _module.StandardRegistry
VALID = _module.VALID
NOT_YET = _module.NOT_YET
REPEALED = _module.REPEALED


class StandardAlignmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = StandardRegistry.load()

    def test_实施日前后状态不同(self) -> None:
        # GB 15760-2025：发布 2025-08-01，实施 2026-08-01
        self.assertEqual(self.reg.get("GB 15760-2025").status_as_of(__import__("datetime").date(2026, 7, 1)), NOT_YET)
        self.assertEqual(self.reg.get("GB 15760-2025").status_as_of(__import__("datetime").date(2026, 11, 10)), VALID)

    def test_废止标准引用被拦截并给出替代(self) -> None:
        result = self.reg.resolve_rule("R-SAFE-02", "2026-11-10")
        self.assertFalse(result["usable"])
        self.assertEqual(result["state"], REPEALED)
        self.assertEqual(result["standard"]["replaced_by"], "GB 15760-2025")
        self.assertIn("已废止", result["message"])

    def test_现行标准可用但不得声称合规(self) -> None:
        result = self.reg.resolve_rule("R-SAFE-01", "2026-11-10")
        self.assertTrue(result["usable"])
        self.assertEqual(result["state"], VALID)
        self.assertEqual(result["clause_level"], "unverified")
        self.assertFalse(result["may_claim_compliance"])
        self.assertIsNone(result["clause_ref"])

    def test_无国标规则记为缺口(self) -> None:
        for rule_id in ("R-CHATTER-01", "R-KIN-01"):
            result = self.reg.resolve_rule(rule_id, "2026-11-10")
            self.assertEqual(result["state"], "gap")
            self.assertFalse(result["usable"])
            self.assertFalse(result["may_claim_compliance"])

    def test_合规表述检测(self) -> None:
        bad = "本方案符合 GB 15760-2025 第 5.2 条要求"
        hits = self.reg.assert_no_compliance_claim(bad)
        self.assertIn("符合 GB", hits)

        ok = "本方案的机床安全防护主题对齐 GB 15760-2025，条款级引用待取得全文后补充"
        self.assertEqual(self.reg.assert_no_compliance_claim(ok), [])

    def test_全量审计计数一致(self) -> None:
        report = self.reg.audit("2026-11-10")
        counts = report["counts"]
        self.assertEqual(counts["total"], len(self.reg.rules()))
        self.assertEqual(
            counts["usable"] + counts["blocked"] + counts["gaps"],
            counts["total"],
        )
        # 本轮全部标准均未取得全文：凡引用标准者（含被拦截的废止引用）都是 unverified
        self.assertEqual(
            counts["clause_level_unverified"],
            counts["usable"] + counts["blocked"],
        )

    def test_未登记规则与标准报错(self) -> None:
        with self.assertRaises(KeyError):
            self.reg.resolve_rule("R-NOT-EXIST")
        with self.assertRaises(KeyError):
            self.reg.get("GB/T 99999-2099")


if __name__ == "__main__":
    unittest.main()
