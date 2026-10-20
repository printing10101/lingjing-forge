"""Process knowledge must survive restart and never imply a successful failed write."""
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from app.rag import process_quadruple as module

pytestmark = pytest.mark.unit


def recipe(**overrides):
    return module.ProcessQuadruple.from_dict({
        "feature": "hole", "process": "drill", "tool": "drill_d8",
        "material": "steel_45", "parameters": {"spindle_rpm": 1200},
        "source": "synthetic_test", "chunk_ids": ["fixture:manual:page1"], **overrides,
    })


@pytest.mark.parametrize("batch", [False, True])
def test_acknowledged_write_survives_fresh_process(tmp_path, batch):
    index = module.ProcessQuadrupleIndex(str(tmp_path))
    quad = recipe()
    if batch:
        assert index.add_batch([quad]) == 1
    else:
        index.add(quad)
    code = """
import json,sys
sys.path.insert(0,sys.argv[1])
from app.rag.process_quadruple import ProcessQuadrupleIndex
index=ProcessQuadrupleIndex(sys.argv[2])
print(json.dumps(index.find_similar('hole','steel_45')))
"""
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run([sys.executable, "-c", code, str(Path(module.__file__).resolve().parents[2]), str(tmp_path)],
                            env=env, capture_output=True, text=True, check=True, timeout=30)
    restored = json.loads(result.stdout)
    assert len(restored) == 1
    assert restored[0]["parameters"] == quad.parameters
    assert restored[0]["chunk_ids"] == quad.chunk_ids
    assert restored[0]["source"] == "synthetic_test"


@pytest.mark.parametrize("batch", [False, True])
def test_failed_replace_rolls_back_all_indexes_and_external_links(tmp_path, monkeypatch, batch):
    entity_index = Mock()
    index = module.ProcessQuadrupleIndex(str(tmp_path), entity_index=entity_index)
    index.add(recipe())
    before = (tmp_path / "process_quadruple.json").read_bytes()
    entity_index.reset_mock()
    monkeypatch.setattr(module.os, "replace", Mock(side_effect=PermissionError("fixture lock")))
    new = recipe(feature="pocket", material="aluminum", tool="endmill_d10")
    with pytest.raises(RuntimeError, match="新增已撤回"):
        index.add_batch([new]) if batch else index.add(new)
    assert index.get_stats()["total_quadruples"] == 1
    assert index.get_features() == ["hole"]
    assert index.find_by_feature_process("pocket", "drill") == []
    assert index.find_similar("pocket", "aluminum") == []
    assert (tmp_path / "process_quadruple.json").read_bytes() == before
    entity_index.add.assert_not_called()
    entity_index.add_batch.assert_not_called()


def test_material_mismatch_is_reference_only():
    index = module.ProcessQuadrupleIndex()
    index.add(recipe(material="aluminum"))
    assert index.recommend_process("hole", "steel_45") == []
    assert index.find_similar("hole", "steel_45")[0]["match_level"] == "feature_only"
    index.add(recipe(material="general"))
    assert index.recommend_process("hole", "steel_45") == []
    assert any(row["material"] == "general" for row in index.find_similar("hole", "steel_45"))


def test_explicit_examples_are_not_manual_recommendations():
    index = module.ProcessQuadrupleIndex()
    assert module.seed_default_quadruples(index) > 0
    assert all(q.source == "example" for q in index._all)
    for q in index._all:
        assert index.recommend_process(q.feature, q.material) == []


def test_singleton_uses_configured_directory_and_late_dependencies(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_singleton", None)
    monkeypatch.setenv("LINGJING_PROCESS_KNOWLEDGE_DIR", str(tmp_path))
    first = module.get_process_quadruple_index(entity_index=Mock())
    first.add(recipe())
    kb = Mock()
    assert module.get_process_quadruple_index(knowledge_base=kb) is first
    assert first._knowledge_base is kb
    monkeypatch.setattr(module, "_singleton", None)
    restarted = module.get_process_quadruple_index(entity_index=Mock())
    assert restarted.find_similar("hole", "steel_45")[0]["chunk_ids"] == ["fixture:manual:page1"]
    assert restarted.recommend_process("hole", "steel_45") == []


def test_default_test_writes_are_isolated_from_deployment(monkeypatch):
    monkeypatch.setattr(module, "_singleton", None)
    index = module.get_process_quadruple_index(entity_index=Mock())
    directory = Path(index._persist_dir).resolve()
    assert directory.name.startswith("lingjing-pytest-process-")
    assert directory != Path(module.__file__).resolve().parents[2] / "data/entity_index"
    index.add(recipe(feature="fixture_only"))
    assert (directory / "process_quadruple.json").exists()


def test_read_service_does_not_seed_empty_knowledge(tmp_path, monkeypatch):
    from app.rag import service
    index = module.ProcessQuadrupleIndex(str(tmp_path))
    monkeypatch.setattr(module, "get_process_quadruple_index", lambda **kwargs: index)
    assert service._get_process_index().get_stats()["total_quadruples"] == 0
    assert not (tmp_path / "process_quadruple.json").exists()


@pytest.mark.parametrize("content", ["{broken", "[]", '{"quadruples": null}',
                                    '{"quadruples": [{"feature": "hole"}]}'])
def test_corrupt_knowledge_fails_closed_without_overwriting(tmp_path, content):
    target = tmp_path / "process_quadruple.json"
    target.write_text(content, encoding="utf-8")
    with pytest.raises(RuntimeError, match="停止加载"):
        module.ProcessQuadrupleIndex(str(tmp_path))
    assert target.read_text(encoding="utf-8") == content
