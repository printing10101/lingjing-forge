"""本体扩展提案的单测（stdlib unittest，可用 pytest 直接收集）。

覆盖点：
1. 固定本体之外的实体类型会被检出为 pending 提案，且不影响 is_registered；
2. 人工批准后才登记；空 reviewer 拒收；
3. 同一类型重复检出合并计数，不重复开提案；
4. 已登记类型再次被提议 → 抛错（状态不一致不静默）；
5. 拒绝后不再被静默重开；
6. SQLite 持久化后可恢复提案与批准状态；
7. parameters 中的现场条件键不误判为属性扩展。
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

# 本模块刻意零第三方依赖（只用 stdlib）。这里按文件路径直接加载，
# 避免经 app.knowledge_graph.__init__ 触发 networkx / sqlalchemy 的包级导入，
# 使本体提案的门禁逻辑可以在最小环境（含 CI 精简镜像）里单独验证。
_MODULE_PATH = Path(__file__).resolve().parents[1] / "ontology_proposal.py"
_MODULE_NAME = "ontology_proposal_under_test"
_spec = importlib.util.spec_from_file_location(_MODULE_NAME, _MODULE_PATH)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules[_MODULE_NAME] = _module  # dataclass 需要能在 sys.modules 中找到本模块
_spec.loader.exec_module(_module)

Evidence = _module.Evidence
OntologyRegistry = _module.OntologyRegistry


class OntologyProposalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = OntologyRegistry.fixed_default()

    def test_new_entity_type_is_pending_and_not_registered(self) -> None:
        p = self.reg.propose(
            kind="entity_type",
            name="coolant_type",
            evidence=Evidence(source_id="cand-001", field="content.coolant"),
        )
        self.assertEqual(p.status, "pending")
        self.assertFalse(self.reg.is_registered("entity_type", "coolant_type"))

    def test_approve_registers_and_requires_reviewer(self) -> None:
        p = self.reg.propose(kind="relation_type", name="preceded_by")
        with self.assertRaises(ValueError):
            self.reg.approve(p.proposal_id, reviewer="   ")
        self.assertTrue(self.reg.approve(p.proposal_id, reviewer="engineer-01"))
        self.assertTrue(self.reg.is_registered("relation_type", "preceded_by"))
        # 重复批准返回 False，不重复记账
        self.assertFalse(self.reg.approve(p.proposal_id, reviewer="engineer-01"))

    def test_duplicate_detection_merges_counts(self) -> None:
        self.reg.propose(kind="entity_type", name="fixture")
        second = self.reg.propose(
            kind="entity_type",
            name="fixture",
            evidence=Evidence(source_id="cand-002", field="content.entity_types"),
        )
        self.assertEqual(second.occurrences, 2)
        self.assertEqual(len(self.reg.pending()), 1)

    def test_proposing_registered_type_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.reg.propose(kind="entity_type", name="Material")

    def test_rejected_not_reopened(self) -> None:
        p = self.reg.propose(kind="property_key", name="surface_roughness_ra")
        self.reg.reject(p.proposal_id, reviewer="engineer-01", reason="与 Ra 别名重复")
        again = self.reg.propose(kind="property_key", name="surface_roughness_ra")
        self.assertEqual(again.status, "rejected")
        self.assertFalse(self.reg.is_registered("property_key", "surface_roughness_ra"))

    def test_scan_candidate_detects_only_unregistered(self) -> None:
        candidate = {
            "feature": "hole",
            "material": "45steel",
            "entity_types": ["CoolantType"],
            "relation_types": ["suitable_for", "requires_coolant"],
            "parameters": {"feed": 0.16, "unit": "mm/rev", "tool_life_min": 45},
        }
        produced = self.reg.scan_candidate(candidate, source_id="cand-003")
        names = {(p.kind, p.name) for p in produced}
        self.assertIn(("entity_type", "CoolantType"), names)
        self.assertIn(("relation_type", "requires_coolant"), names)
        self.assertIn(("property_key", "tool_life_min"), names)
        # 已登记的关系与现场条件键不产生提案
        self.assertNotIn(("relation_type", "suitable_for"), names)
        self.assertNotIn(("property_key", "feed"), names)
        # 具体牌号不当作新的本体实体类型
        self.assertNotIn(("entity_type", "45steel"), names)

    def test_sqlite_roundtrip_keeps_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ontology.sqlite"
            reg = OntologyRegistry.fixed_default(db_path=db)
            p = reg.propose(kind="entity_type", name="CoolantType")
            reg.approve(p.proposal_id, reviewer="engineer-01")

            reloaded = OntologyRegistry.fixed_default(db_path=db)
            self.assertTrue(reloaded.is_registered("entity_type", "CoolantType"))
            self.assertEqual(reloaded.pending(), [])
            restored = reloaded.get(p.proposal_id)
            assert restored is not None
            self.assertEqual(restored.reviewer, "engineer-01")


if __name__ == "__main__":
    unittest.main()
