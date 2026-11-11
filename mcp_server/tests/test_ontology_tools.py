"""本体扩展提案 MCP 工具的模块自测（不依赖 mcp / httpx）。

用途：验证"候选 → 待审提案 → 人工批准 → 生效"这条对外链路的注册与返回格式，
不需要拉起 MCP 服务或网关。

运行：
    python -m unittest mcp_server.tests.test_ontology_tools -v

刻意验证的两条治理约束：
1. 提案产出后 ``needs_review`` 语义明确——工具只登记待审，不生效；
2. 工具组不提供任何审批入口（函数名里不得出现 approve/reject）。
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path


class _FakeServer:
    """最小 FastMCP 替身：只记录 name → 函数。"""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, *, name: str, description: str = ""):
        def decorator(fn):
            self.tools[name] = fn
            return fn

        return decorator


class OntologyToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["LINGJING_ONTOLOGY_DB"] = str(Path(self._tmp.name) / "ontology.sqlite")

        from mcp_server.ontology_tools import register_ontology_tools

        self.server = _FakeServer()
        register_ontology_tools(self.server)

    def tearDown(self) -> None:
        os.environ.pop("LINGJING_ONTOLOGY_DB", None)
        self._tmp.cleanup()

    def _call(self, name: str, *args, **kwargs) -> dict:
        import asyncio

        result = asyncio.run(self.server.tools[name](*args, **kwargs))
        return json.loads(result[0]["text"])

    def test_三种工具均已注册(self) -> None:
        self.assertEqual(
            set(self.server.tools),
            {
                "cam_propose_ontology_extension",
                "cam_list_ontology_proposals",
                "cam_get_ontology_registry",
            },
        )

    def test_不提供审批入口(self) -> None:
        names = " ".join(self.server.tools).lower()
        self.assertNotIn("approve", names)
        self.assertNotIn("reject", names)

    def test_扫描候选产出待审提案且未生效(self) -> None:
        payload = self._call(
            "cam_propose_ontology_extension",
            {"entity_types": ["CoolantType"], "parameters": {"tool_life_min": 45}},
            "cand-001",
        )
        kinds = {(p["kind"], p["name"]) for p in payload["proposals"]}
        self.assertIn(("entity_type", "CoolantType"), kinds)
        self.assertIn(("property_key", "tool_life_min"), kinds)
        self.assertTrue(payload["requires_human_review"])

        registry = self._call("cam_get_ontology_registry")
        self.assertNotIn("CoolantType", registry["entity_types"])

        listed = self._call("cam_list_ontology_proposals")
        self.assertEqual(listed["count"], 2)

    def test_已登记类型不重复提案(self) -> None:
        payload = self._call(
            "cam_propose_ontology_extension",
            {"relation_types": ["suitable_for"]},
            "cand-002",
        )
        self.assertEqual(payload["proposals"], [])
        self.assertIn("未检出", payload["message"])

    def test_空submission_id被拒收(self) -> None:
        payload = self._call("cam_propose_ontology_extension", {"entity_types": ["X"]}, "  ")
        self.assertTrue(payload.get("error"))
        self.assertIn("submission_id", payload["message"])

    def test_批准后进入注册表(self) -> None:
        from mcp_server.ontology_tools import _registry

        payload = self._call(
            "cam_propose_ontology_extension",
            {"entity_types": ["Fixture"]},
            "cand-003",
        )
        proposal_id = payload["proposals"][0]["proposal_id"]

        changed = _registry().approve(proposal_id, reviewer="engineer-01")
        self.assertTrue(changed)

        registry = self._call("cam_get_ontology_registry")
        self.assertIn("Fixture", registry["entity_types"])

        reviewed = self._call("cam_list_ontology_proposals", "", True)
        self.assertEqual(reviewed["count"], 1)
        self.assertEqual(reviewed["proposals"][0]["status"], "approved")


if __name__ == "__main__":
    unittest.main()
