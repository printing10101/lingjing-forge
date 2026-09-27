"""提示词版本持久化测试（自进化 M1）。

覆盖：读写 roundtrip、upsert 覆盖、状态机更新、applied 过滤、
损坏文件降级、seed_registry 叠加 applied 覆盖层。
"""

import json

import pytest

from app.ai.prompts import (
    ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID,
    get_prompt_registry,
    reset_prompt_registry,
)
from app.ai.prompts import persistence as pv


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMPT_VERSIONS_STORE", str(tmp_path / "prompt_versions.json"))
    reset_prompt_registry()
    yield
    reset_prompt_registry()


class TestPersistence:
    def test_empty_store(self):
        assert pv.load_records() == []
        assert pv.list_applied() == []

    def test_upsert_and_find(self):
        pv.upsert_record({"prompt_id": "a.b", "version": 2, "template": "t2", "status": "proposed"})
        pv.upsert_record({"prompt_id": "a.b", "version": 1, "template": "t1", "status": "applied"})
        rec = pv.find_record("a.b", 2)
        assert rec["template"] == "t2"
        assert rec["created_at"] > 0
        # 同键覆盖不产生重复
        pv.upsert_record({"prompt_id": "a.b", "version": 2, "template": "t2'", "status": "proposed"})
        records = pv.load_records()
        assert len(records) == 2
        assert {r["template"] for r in records} == {"t1", "t2'"}

    def test_update_status_validates(self):
        pv.upsert_record({"prompt_id": "a.b", "version": 1, "template": "t", "status": "proposed"})
        with pytest.raises(ValueError):
            pv.update_status("a.b", 1, "bogus")
        assert pv.find_record("a.b", 1)["status"] == "proposed"

    def test_update_status_with_gate(self):
        pv.upsert_record({"prompt_id": "a.b", "version": 1, "template": "t", "status": "applied"})
        updated = pv.update_status("a.b", 1, "rolled_back", gate={"passed": False})
        assert updated["status"] == "rolled_back"
        assert updated["gate"]["passed"] is False
        assert pv.find_record("a.b", 1)["status"] == "rolled_back"

    def test_update_status_missing_returns_none(self):
        assert pv.update_status("ghost", 9, "applied") is None

    def test_list_applied_filter(self):
        pv.upsert_record({"prompt_id": "a.b", "version": 1, "template": "t1", "status": "applied"})
        pv.upsert_record({"prompt_id": "c.d", "version": 2, "template": "t2", "status": "proposed"})
        pv.upsert_record({"prompt_id": "e.f", "version": 3, "template": "t3", "status": "rejected"})
        applied = pv.list_applied()
        assert len(applied) == 1
        assert applied[0]["prompt_id"] == "a.b"

    def test_corrupt_file_degrades_to_empty(self, tmp_path):
        store = tmp_path / "prompt_versions.json"
        store.write_text("{broken json", encoding="utf-8")
        assert pv.load_records() == []

    def test_non_list_top_level_degrades(self, tmp_path):
        store = tmp_path / "prompt_versions.json"
        store.write_text('{"not": "a list"}', encoding="utf-8")
        assert pv.load_records() == []


class TestSeedOverlay:
    def test_applied_version_overrides_default_after_restart(self):
        """applied 演化版本在注册表重建（模拟重启）后覆盖默认条目。"""
        template_v2 = "修复助手 v2：只修列出的错误码。"
        pv.upsert_record(
            {
                "prompt_id": ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID,
                "version": 2,
                "template": template_v2,
                "status": "applied",
            }
        )
        registry = get_prompt_registry()
        # get() 取最新版本 → v2（覆盖层生效）
        entry = registry.get(ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID)
        assert entry.version == 2
        assert entry.template == template_v2
        # 默认 v1 仍在（回滚目标）
        assert registry.get(ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID, version=1).version == 1

    def test_corrupt_applied_record_skipped(self, tmp_path):
        store = tmp_path / "prompt_versions.json"
        store.write_text(
            json.dumps([{"prompt_id": 123, "version": "x"}]), encoding="utf-8"  # 非法记录
        )
        registry = get_prompt_registry()
        assert ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID in registry.list_ids()  # 默认条目不受影响
