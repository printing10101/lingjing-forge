"""ProcessPlanningDataManager 材料查询 单元测试。

回归背景（2026-09 全链条验证发现）：知识库材料规范名为"45钢"，而
run_dxf_pipeline / ProcessPlanningPipeline 等调用方默认值是"45#钢"，
子串匹配因井号失配，导致默认参数下整条 DXF→NC 链在知识库查询阶段
失败。修复：归一化（去井号与空白）下沉到查询层，与
agent/orchestrator.py 的入口归一化规则一致。

注意：本文件刻意使用真实知识库数据（不打 mock）——此前
test_process_planning_pipeline.py 全程 mock 数据管理器，掩盖了该断裂。
"""

from __future__ import annotations

import pytest

from app.data.process_data_manager import (
    ProcessPlanningDataManager,
    QueryError,
)


@pytest.fixture(scope="module")
def data_manager() -> ProcessPlanningDataManager:
    return ProcessPlanningDataManager()


class TestGetMaterialByName:
    def test_hash_alias_matches_canonical(self, data_manager):
        """复现：'45#钢'（各调用方默认值）必须命中知识库的'45钢'。"""
        entry = data_manager.get_material_by_name("45#钢")
        assert entry is not None
        assert entry.name == "45钢"

    def test_canonical_name(self, data_manager):
        entry = data_manager.get_material_by_name("45钢")
        assert entry is not None
        assert entry.name == "45钢"

    def test_whitespace_ignored(self, data_manager):
        entry = data_manager.get_material_by_name(" 45 钢 ")
        assert entry is not None
        assert entry.name == "45钢"

    def test_other_materials_unaffected(self, data_manager):
        assert data_manager.get_material_by_name("6061铝合金") is not None
        assert data_manager.get_material_by_name("304不锈钢") is not None

    def test_english_alias_steel(self, data_manager):
        """nl2cad 端点默认材料是英文 'steel'，必须命中 45钢。"""
        entry = data_manager.get_material_by_name("steel")
        assert entry is not None
        assert entry.id == "steel_45"

    def test_english_alias_variants(self, data_manager):
        assert data_manager.get_material_by_name("aluminum").id == "al_6061"
        assert data_manager.get_material_by_name("stainless steel").id == "ss_304"
        assert data_manager.get_material_by_name("Stainless_Steel").id == "ss_304"

    def test_unknown_material_returns_none(self, data_manager):
        assert data_manager.get_material_by_name("unobtanium") is None

    def test_empty_name_raises(self, data_manager):
        with pytest.raises(QueryError):
            data_manager.get_material_by_name("")


class TestProcessPipelineWithHashAlias:
    """链路级复现：默认材料 '45#钢' 不应再卡死在知识库查询阶段。

    part_description 形状复刻 app/dxf/pipeline.py::_build_part_description
    对孔特征的组装方式（id/type/position/diameter/depth/tolerance_grade/
    surface）。
    """

    def test_pipeline_kb_stage_succeeds_with_hash_alias(self):
        from app.process_planning.pipeline import ProcessPlanningPipeline

        pipeline = ProcessPlanningPipeline()
        part_description = {
            "material": "45#钢",
            "part_type": "plate",
            "holes": [
                {
                    "id": "H001",
                    "type": "through_hole",
                    "position": [30.0, 30.0, 0.0],
                    "diameter": 8.0,
                    "depth": 10.0,
                    "tolerance_grade": "H8",
                    "surface": "A",
                },
            ],
        }
        result = pipeline.run(part_description=part_description)

        kb_stage = next(s for s in result.stages if s.name == "知识库查询")
        assert kb_stage.status == "success", (
            f"知识库查询阶段失败: {kb_stage.errors}"
        )
        assert "知识库查询阶段失败" not in result.summary
