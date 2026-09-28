"""expand_cutting_parameters 切参扩容脚本 单元测试。

覆盖：
- 矩阵展开：条数达验收线（≥200）、生成确定性（两次展开一致）
- 单条校验：正数性 / 区间顺序 / feed_unit 枚举 / source 必填
- 组合合法性：ceramic 仅车削、PCD 仅有色金属与塑料
- 合并去重：既有 (material, series, tool_material) 不重复生成，幂等（重跑新增 0）
- 原子写回：正常写回内容一致；序列化失败时原文件不动且无临时文件残留
- 仓库真实数据文件：≥200 条且全部通过校验（只读）

测试设计原则（与 test_sovereignty_ratio.py 一致）：直接 sys.path 载入脚本；
写盘用例一律走 tmp_path，不触碰仓库数据。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import expand_cutting_parameters as ec  # noqa: E402

REAL_DATA_FILE = (
    Path(__file__).resolve().parents[2] / "app" / "data" / "cutting_parameters.json"
)


class TestGenerate:
    def test_count_meets_target(self):
        entries = ec.generate_entries()
        assert len(entries) >= 200

    def test_generation_deterministic(self):
        assert ec.generate_entries() == ec.generate_entries()

    def test_all_entries_valid_with_source(self):
        for i, entry in enumerate(ec.generate_entries()):
            ec.validate_entry(entry, i)  # 非法即抛

    def test_ceramic_only_turning(self):
        bad = [
            e for e in ec.generate_entries()
            if e["tool_material"] == "ceramic"
            and e["tool_series"] not in ec.CERAMIC_ALLOWED_SERIES
        ]
        assert bad == []

    def test_pcd_only_nonferrous_and_plastic(self):
        allowed = {"nonferrous", "plastic"}
        bad = [
            e for e in ec.generate_entries()
            if e["tool_material"] == "PCD"
            and ec.MATERIAL_CLASS[e["material_id"]] not in allowed
        ]
        assert bad == []

    def test_every_material_has_full_hss_carbide_coated(self):
        for material_id in ec.MATERIAL_NAMES:
            for tool in ("HSS", "carbide", "coated_carbide"):
                for series in ec.TOOL_SERIES:
                    key = (material_id, series, tool)
                    assert any(ec.dedup_key(e) == key for e in ec.generate_entries())


class TestValidate:
    def _base_entry(self) -> dict:
        return ec.generate_entries()[0]

    def test_rejects_non_positive_value(self):
        entry = self._base_entry()
        entry["cutting_speed_min_mpm"] = 0
        with pytest.raises(ValueError, match="正数"):
            ec.validate_entry(entry, 0)

    def test_rejects_inverted_speed_range(self):
        entry = self._base_entry()
        entry["cutting_speed_min_mpm"], entry["cutting_speed_max_mpm"] = (
            entry["cutting_speed_max_mpm"],
            entry["cutting_speed_min_mpm"],
        )
        with pytest.raises(ValueError, match="区间颠倒"):
            ec.validate_entry(entry, 0)

    def test_rejects_bad_feed_unit(self):
        entry = self._base_entry()
        entry["feed_unit"] = "mm/s"
        with pytest.raises(ValueError, match="feed_unit"):
            ec.validate_entry(entry, 0)

    def test_rejects_missing_source(self):
        entry = self._base_entry()
        del entry["source"]
        with pytest.raises(ValueError, match="source"):
            ec.validate_entry(entry, 0)

    def test_rejects_missing_required_field(self):
        entry = self._base_entry()
        del entry["description"]
        with pytest.raises(ValueError, match="缺少字段"):
            ec.validate_entry(entry, 0)


class TestMerge:
    def test_merge_idempotent(self):
        existing = ec.generate_entries()[:12]
        merged_once = ec.merge(existing, ec.generate_entries())
        merged_twice = ec.merge(merged_once, ec.generate_entries())
        assert merged_once == merged_twice

    def test_merge_preserves_existing_entries(self):
        existing = [{"id": "keep_me", "material_id": "material_45steel",
                     "tool_series": "twist_drill", "tool_material": "HSS"}]
        merged = ec.merge(existing, ec.generate_entries())
        assert merged[0] == existing[0]
        keys = [ec.dedup_key(e) for e in merged[1:]]
        assert ec.dedup_key(existing[0]) not in keys

    def test_merge_rejects_duplicate_id(self):
        # 同键候选被幂等跳过（不抛）；同 id 不同键才是真正的 id 冲突
        existing = [dict(ec.generate_entries()[0])]
        same_key = dict(ec.generate_entries()[0])
        same_key["id"] = "another_id"
        assert ec.merge(existing, [same_key]) == existing  # 幂等跳过

        conflict = dict(ec.generate_entries()[0])
        conflict["tool_series"] = "endmill"  # 换键不换 id
        with pytest.raises(ValueError, match="id 重复"):
            ec.merge(existing, [conflict])


class TestAtomicWrite:
    def test_write_round_trip(self, tmp_path: Path):
        target = tmp_path / "out.json"
        payload = [{"id": "a", "value": 1}]
        ec.atomic_write(target, payload)
        assert json.loads(target.read_text(encoding="utf-8")) == payload

    def test_failure_leaves_original_intact(self, tmp_path: Path):
        target = tmp_path / "out.json"
        original = [{"id": "original"}]
        target.write_text(json.dumps(original), encoding="utf-8")
        before = sorted(p.name for p in tmp_path.iterdir())
        with pytest.raises(TypeError):
            ec.atomic_write(target, [{"bad": object()}])  # JSON 不可序列化
        assert json.loads(target.read_text(encoding="utf-8")) == original
        after = sorted(p.name for p in tmp_path.iterdir())
        assert before == after  # 临时文件已清理，无残留


class TestRealDataFile:
    def test_real_file_meets_target_and_valid(self):
        data = json.loads(REAL_DATA_FILE.read_text(encoding="utf-8"))
        assert len(data) >= 200
        for i, entry in enumerate(data):
            ec.validate_entry(entry, i)
        keys = [ec.dedup_key(e) for e in data]
        assert len(keys) == len(set(keys)), "真实文件存在重复 (material, series, tool)"
