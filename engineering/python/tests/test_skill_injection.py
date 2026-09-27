import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.plugins.skill_loader import (  # noqa: E402
    Skill,
    SkillLevel,
    SkillMetadata,
    SkillPriority,
    SkillVersion,
    MarkdownSkillParser,
    SkillRegistry,
    SkillLoader,
    SkillFileWatcher,
    init_skill_loader,
    PRIORITY_MAP,
)


SAMPLE_YAML_SKILL = """---
skill_id: sample_test
name: 示例测试技能
display_name: 示例测试技能
version: 1.0.0
applicable_tasks: ["prediction", "analysis"]
required_context: ["material", "tool_type"]
tags: ["test", "sample"]
---

# 示例测试技能

## 适用场景
用于测试。

## 输入参数
- material: 工件材料
- tool_type: 刀具类型

## 执行步骤
1. 第一步
2. 第二步
"""

SAMPLE_SKILL_WITH_CODE = """---
skill_id: code_test
name: 代码测试
version: 1.0.0
applicable_tasks: ["*"]
required_context: []
---

# Test

```python
print("hello")
```

```json
{"key": "value"}
```
"""


class TestSkillVersion:
    def test_create_version(self):
        v = SkillVersion(
            version="1.0.0", content_hash="abc123", file_path="/tmp/test.md"
        )
        assert v.version == "1.0.0"
        assert v.content_hash == "abc123"
        assert v.file_path == "/tmp/test.md"
        assert v.created_at is not None
        assert isinstance(v.metadata, dict)

    def test_version_with_metadata(self):
        v = SkillVersion(
            version="1.0.0",
            content_hash="abc123",
            file_path="/tmp/test.md",
            metadata={"param": "value"},
        )
        assert v.metadata == {"param": "value"}


class TestSkillMetadata:
    def test_create_metadata(self):
        m = SkillMetadata(
            skill_id="test_skill",
            name="测试技能",
            version="1.0.0",
            applicable_tasks=["prediction"],
            required_context=["material", "tool_type"],
        )
        assert m.skill_id == "test_skill"
        assert m.name == "测试技能"
        assert m.version == "1.0.0"
        assert m.applicable_tasks == ["prediction"]
        assert m.required_context == ["material", "tool_type"]

    def test_applicable_to_wildcard(self):
        m = SkillMetadata(
            skill_id="t1", name="t1", version="1.0.0", applicable_tasks=["*"]
        )
        assert m.applicable_to("lnn_training") is True
        assert m.applicable_to("unknown") is True

    def test_applicable_to_exact(self):
        m = SkillMetadata(
            skill_id="t2", name="t2", version="1.0.0", applicable_tasks=["prediction"]
        )
        assert m.applicable_to("prediction") is True
        assert m.applicable_to("training") is False

    def test_contexts_satisfied_empty(self):
        m = SkillMetadata(
            skill_id="t3", name="t3", version="1.0.0", required_context=[]
        )
        ok, missing = m.contexts_satisfied(set())
        assert ok is True
        ok2, _ = m.contexts_satisfied({"a"})
        assert ok2 is True

    def test_contexts_satisfied_partial(self):
        m = SkillMetadata(
            skill_id="t4", name="t4", version="1.0.0", required_context=["a", "b"]
        )
        ok, _ = m.contexts_satisfied({"a", "b"})
        assert ok is True
        ok2, _ = m.contexts_satisfied({"a"})
        assert ok2 is False
        ok3, _ = m.contexts_satisfied(set())
        assert ok3 is False

    def test_contexts_satisfied_superset(self):
        m = SkillMetadata(
            skill_id="t5", name="t5", version="1.0.0", required_context=["a"]
        )
        ok, _ = m.contexts_satisfied({"a", "b", "c"})
        assert ok is True

    def test_to_dict(self):
        m = SkillMetadata(
            skill_id="dict_test",
            name="字典测试",
            version="1.0.0",
            applicable_tasks=["prediction"],
            required_context=["material"],
            tags=["tag1"],
            parameters={"max": 100},
        )
        d = m.to_dict()
        assert d["skill_id"] == "dict_test"
        assert d["applicable_tasks"] == ["prediction"]
        assert d["required_context"] == ["material"]


class TestMarkdownSkillParser:
    def _write_skill_file(self, tmp_path, content, name="test_skill.md"):
        file_path = tmp_path / name
        file_path.write_text(content, encoding="utf-8")
        return str(file_path)

    def test_parse_yaml_frontmatter(self, tmp_path):
        file_path = self._write_skill_file(tmp_path, SAMPLE_YAML_SKILL)
        result = MarkdownSkillParser.parse(file_path)

        assert result is not None
        meta = result["metadata"]
        assert meta["skill_id"] == "sample_test"
        assert meta["name"] == "示例测试技能"
        assert meta["version"] == "1.0.0"
        assert meta["applicable_tasks"] == ["prediction", "analysis"]
        assert meta["required_context"] == ["material", "tool_type"]
        assert meta["tags"] == ["test", "sample"]

    def test_parse_body_extraction(self, tmp_path):
        file_path = self._write_skill_file(tmp_path, SAMPLE_YAML_SKILL)
        result = MarkdownSkillParser.parse(file_path)

        body = result.get("body", "")
        assert "适用场景" in body
        assert "用于测试" in body
        assert "输入参数" in body
        assert "执行步骤" in body

    def test_parse_empty_file(self, tmp_path):
        file_path = self._write_skill_file(tmp_path, "")
        result = MarkdownSkillParser.parse(file_path)
        assert result is not None
        assert result["metadata"]["skill_id"] is not None

    def test_parse_no_frontmatter(self, tmp_path):
        file_path = self._write_skill_file(
            tmp_path, "# Just a heading\n\nContent here."
        )
        result = MarkdownSkillParser.parse(file_path)
        assert result is not None
        assert result["metadata"]["skill_id"] is not None

    def test_parse_code_blocks(self, tmp_path):
        file_path = self._write_skill_file(tmp_path, SAMPLE_SKILL_WITH_CODE)
        result = MarkdownSkillParser.parse(file_path)

        assert len(result["code_blocks"]) == 2
        assert result["code_blocks"][0] == ("python", 'print("hello")')
        assert result["code_blocks"][1] == ("json", '{"key": "value"}')

    def test_parse_nonexistent_file(self):
        result = MarkdownSkillParser.parse("/nonexistent/path/to/skill.md")
        assert result is None

    def test_create_skill_from_parsed(self, tmp_path):
        file_path = self._write_skill_file(tmp_path, SAMPLE_YAML_SKILL)
        result = MarkdownSkillParser.parse(file_path)

        assert result is not None
        meta = result["metadata"]
        assert meta["skill_id"] == "sample_test"
        assert meta["name"] == "示例测试技能"
        assert meta["version"] == "1.0.0"
        assert "body" in result
        assert "code_blocks" in result
        assert "raw_content" in result
        assert "适用场景" in result["body"]


class TestSkillRegistry:
    def test_register_and_get(self):
        registry = SkillRegistry()
        meta = SkillMetadata(skill_id="reg1", name="reg1", version="1.0.0")
        skill = Skill(metadata=meta)

        registry.register(skill)
        assert registry.get("reg1") is skill
        assert registry.get("missing") is None

    def test_list_all(self):
        registry = SkillRegistry()
        s1 = Skill(metadata=SkillMetadata(skill_id="s1", name="s1", version="1.0.0"))
        s2 = Skill(metadata=SkillMetadata(skill_id="s2", name="s2", version="1.0.0"))
        registry.register(s1)
        registry.register(s2)

        assert len(registry.list_all()) == 2

    def test_get_by_level(self):
        registry = SkillRegistry()
        s1 = Skill(
            metadata=SkillMetadata(
                skill_id="g1",
                name="g1",
                version="1.0.0",
                level=SkillLevel.GLOBAL,
                priority=SkillPriority.GLOBAL,
            )
        )
        s2 = Skill(
            metadata=SkillMetadata(
                skill_id="p1",
                name="p1",
                version="1.0.0",
                level=SkillLevel.PROJECT,
                priority=SkillPriority.PROJECT,
            )
        )
        registry.register(s1)
        registry.register(s2)

        global_skills = registry.get_by_level(SkillLevel.GLOBAL)
        assert len(global_skills) == 1
        assert global_skills[0].metadata.skill_id == "g1"

    def test_get_by_task(self):
        registry = SkillRegistry()
        s1 = Skill(
            metadata=SkillMetadata(
                skill_id="pred_skill",
                name="pred",
                version="1.0.0",
                applicable_tasks=["prediction"],
            )
        )
        s2 = Skill(
            metadata=SkillMetadata(
                skill_id="train_skill",
                name="train",
                version="1.0.0",
                applicable_tasks=["training"],
            )
        )
        s3 = Skill(
            metadata=SkillMetadata(
                skill_id="all_skill",
                name="all",
                version="1.0.0",
                applicable_tasks=["*"],
            )
        )
        registry.register(s1)
        registry.register(s2)
        registry.register(s3)

        pred_skills = registry.get_by_task("prediction")
        assert len(pred_skills) == 2  # s1 and s3

        train_skills = registry.get_by_task("training")
        assert len(train_skills) == 2  # s2 and s3

    def test_activate_deactivate(self):
        registry = SkillRegistry()
        s = Skill(metadata=SkillMetadata(skill_id="act1", name="act1", version="1.0.0"))
        registry.register(s)
        assert s.is_active is True

        registry.deactivate("act1")
        assert s.is_active is False

        registry.activate("act1")
        assert s.is_active is True

    def test_remove(self):
        registry = SkillRegistry()
        s = Skill(metadata=SkillMetadata(skill_id="rem1", name="rem1", version="1.0.0"))
        registry.register(s)
        assert registry.get("rem1") is s

        result = registry.remove("rem1")
        assert result is True
        assert registry.get("rem1") is None

    def test_remove_missing(self):
        registry = SkillRegistry()
        result = registry.remove("nonexistent")
        assert result is False

    def test_clear_level(self):
        registry = SkillRegistry()
        s1 = Skill(
            metadata=SkillMetadata(
                skill_id="g2",
                name="g2",
                version="1.0.0",
                level=SkillLevel.GLOBAL,
            )
        )
        s2 = Skill(
            metadata=SkillMetadata(
                skill_id="p2",
                name="p2",
                version="1.0.0",
                level=SkillLevel.PROJECT,
            )
        )
        registry.register(s1)
        registry.register(s2)

        removed = registry.clear_level(SkillLevel.GLOBAL)
        assert removed == 1
        assert registry.get("g2") is None
        assert registry.get("p2") is s2

    def test_get_stats(self):
        registry = SkillRegistry()
        s = Skill(
            metadata=SkillMetadata(skill_id="stat1", name="stat1", version="1.0.0")
        )
        registry.register(s)

        stats = registry.get_stats()
        assert stats["total"] == 1
        assert stats["active"] == 1
        assert "by_level" in stats


class TestSkillLoaderBasic:
    def test_init_with_temp_dir(self, tmp_path):
        skills_dir = str(tmp_path / "skills")
        loader = SkillLoader(skills_base_dir=skills_dir)

        global_dir = os.path.join(skills_dir, "global")
        project_dir = os.path.join(skills_dir, "projects")
        agent_dir = os.path.join(skills_dir, "agents")

        assert os.path.exists(global_dir)
        assert os.path.exists(project_dir)
        assert os.path.exists(agent_dir)
        loader.stop_watcher()

    def test_create_builtin_skills(self, tmp_path):
        skills_dir = str(tmp_path / "skills")
        loader = SkillLoader(skills_base_dir=skills_dir)

        builtin_ids = [s.metadata.skill_id for s in loader.registry.list_all()]
        assert "error_handling" in builtin_ids
        assert "safety_guidelines" in builtin_ids
        assert "constraint_checking" in builtin_ids
        loader.stop_watcher()

    def test_load_skill_from_file(self, tmp_path):
        skills_dir = tmp_path / "skills2"
        skills_dir.mkdir()
        project_dir = skills_dir / "projects" / "testproj"
        project_dir.mkdir(parents=True)

        skill_file = project_dir / "sample_test.md"
        skill_file.write_text(SAMPLE_YAML_SKILL, encoding="utf-8")

        loader = SkillLoader(skills_base_dir=str(skills_dir))
        skill = loader._load_skill_from_file(str(skill_file), SkillLevel.PROJECT)

        assert skill is not None
        assert skill.metadata.skill_id == "sample_test"
        assert skill.metadata.level == SkillLevel.PROJECT
        loader.stop_watcher()


class TestSkillLoaderSkillsForTask:
    def _make_loader(self, tmp_path):
        skills_dir = str(tmp_path / "skills")
        loader = SkillLoader(skills_base_dir=skills_dir)
        for lvl in [SkillLevel.GLOBAL, SkillLevel.PROJECT, SkillLevel.AGENT]:
            loader.registry.clear_level(lvl)
        return loader

    def test_get_skills_with_context(self, tmp_path):
        loader = self._make_loader(tmp_path)

        s1 = Skill(
            metadata=SkillMetadata(
                skill_id="ctx_all",
                name="ctx_all",
                version="1.0.0",
                applicable_tasks=["*"],
                required_context=[],
                level=SkillLevel.GLOBAL,
                priority=SkillPriority.GLOBAL,
            )
        )
        s2 = Skill(
            metadata=SkillMetadata(
                skill_id="ctx_full",
                name="ctx_full",
                version="1.0.0",
                applicable_tasks=["*"],
                required_context=["material", "tool_type"],
                level=SkillLevel.GLOBAL,
                priority=SkillPriority.PROJECT,
            )
        )
        loader.registry.register(s1)
        loader.registry.register(s2)

        skills = loader.get_skills_for_task(
            task_type="prediction",
            available_context={"material", "tool_type"},
        )
        skill_ids = [s.metadata.skill_id for s in skills]
        assert "ctx_all" in skill_ids
        assert "ctx_full" in skill_ids

        skills2 = loader.get_skills_for_task(
            task_type="prediction",
            available_context=set(),
        )
        skill_ids2 = [s.metadata.skill_id for s in skills2]
        assert "ctx_all" in skill_ids2
        assert "ctx_full" not in skill_ids2
        loader.stop_watcher()

    def test_get_skills_by_project_id(self, tmp_path):
        skills_dir = tmp_path / "skills_proj"
        proj_dir = skills_dir / "projects" / "projA"
        proj_dir.mkdir(parents=True)

        skill_file = proj_dir / "proj_skill.md"
        skill_file.write_text(
            """---
skill_id: proj_only
name: 项目专属
version: 1.0.0
applicable_tasks: ["prediction"]
required_context: []
---
# 项目专属
""",
            encoding="utf-8",
        )

        loader = SkillLoader(skills_base_dir=str(skills_dir))
        loader.load_project_skills("projA")

        skills = loader.get_skills_for_task(
            task_type="prediction",
            project_id="projA",
        )
        skill_ids = [s.metadata.skill_id for s in skills]
        assert "proj_only" in skill_ids
        loader.stop_watcher()

    def test_get_skills_by_agent_id(self, tmp_path):
        skills_dir = tmp_path / "skills_agent"
        agent_dir = skills_dir / "agents" / "agentX"
        agent_dir.mkdir(parents=True)

        skill_file = agent_dir / "agent_skill.md"
        skill_file.write_text(
            """---
skill_id: agent_only
name: 代理专属
version: 1.0.0
applicable_tasks: ["analysis"]
required_context: []
---
# 代理专属
""",
            encoding="utf-8",
        )

        loader = SkillLoader(skills_base_dir=str(skills_dir))
        loader.load_agent_skills("agentX")

        skills = loader.get_skills_for_task(
            task_type="analysis",
            agent_id="agentX",
        )
        assert len([s for s in skills if s.metadata.skill_id == "agent_only"]) == 1
        loader.stop_watcher()

    def test_priority_ordering(self, tmp_path):
        loader = self._make_loader(tmp_path)

        g = Skill(
            metadata=SkillMetadata(
                skill_id="g",
                name="g",
                version="1.0.0",
                applicable_tasks=["*"],
                required_context=[],
                level=SkillLevel.GLOBAL,
                priority=SkillPriority.GLOBAL,
            )
        )
        p = Skill(
            metadata=SkillMetadata(
                skill_id="p",
                name="p",
                version="1.0.0",
                applicable_tasks=["*"],
                required_context=[],
                level=SkillLevel.GLOBAL,
                priority=SkillPriority.PROJECT,
            )
        )
        a = Skill(
            metadata=SkillMetadata(
                skill_id="a",
                name="a",
                version="1.0.0",
                applicable_tasks=["*"],
                required_context=[],
                level=SkillLevel.GLOBAL,
                priority=SkillPriority.AGENT,
            )
        )
        loader.registry.register(g)
        loader.registry.register(p)
        loader.registry.register(a)

        skills = loader.get_skills_for_task(task_type="prediction")
        order = [s.metadata.skill_id for s in skills]
        assert order.index("a") < order.index("p") < order.index("g")
        loader.stop_watcher()


class TestInjectSkills:
    def test_inject_skills_string(self, tmp_path):
        skills_dir = str(tmp_path / "skills")
        loader = SkillLoader(skills_base_dir=skills_dir)
        for lvl in [SkillLevel.GLOBAL, SkillLevel.PROJECT, SkillLevel.AGENT]:
            loader.registry.clear_level(lvl)

        s = Skill(
            metadata=SkillMetadata(
                skill_id="inject_test",
                name="注入测试",
                version="1.0.0",
                applicable_tasks=["*"],
                required_context=[],
                level=SkillLevel.GLOBAL,
                priority=SkillPriority.GLOBAL,
            )
        )
        s.body = "## 测试内容\n测试步骤：\n1. 做A\n2. 做B\n"
        loader.registry.register(s)

        ctx = loader._merge_skills_to_context(loader.registry.list_all())
        assert "已注入技能指南" in ctx
        loader.stop_watcher()

    def test_merge_empty_skills(self, tmp_path):
        skills_dir = str(tmp_path / "skills")
        loader = SkillLoader(skills_base_dir=skills_dir)
        for lvl in [SkillLevel.GLOBAL, SkillLevel.PROJECT, SkillLevel.AGENT]:
            loader.registry.clear_level(lvl)

        ctx = loader._merge_skills_to_context([])
        assert ctx == ""
        loader.stop_watcher()


class TestVersionManagement:
    def test_version_history(self, tmp_path):
        skills_dir = str(tmp_path / "skills")
        loader = SkillLoader(skills_base_dir=skills_dir)
        for lvl in [SkillLevel.GLOBAL, SkillLevel.PROJECT, SkillLevel.AGENT]:
            loader.registry.clear_level(lvl)

        s = Skill(
            metadata=SkillMetadata(
                skill_id="ver_test", name="ver_test", version="1.0.0"
            ),
            raw_content="# v1",
            body="v1",
        )
        s.versions["1.0.0"] = SkillVersion(
            version="1.0.0", content_hash="hash_v1", file_path="/t/v1.md"
        )
        s.versions["1.1.0"] = SkillVersion(
            version="1.1.0", content_hash="hash_v2", file_path="/t/v2.md"
        )
        s.versions["2.0.0"] = SkillVersion(
            version="2.0.0", content_hash="hash_v3", file_path="/t/v3.md"
        )
        loader.registry.register(s)

        history = loader.get_version_history("ver_test")
        assert history is not None
        assert len(history) == 3
        loader.stop_watcher()

    def test_version_history_missing(self, tmp_path):
        skills_dir = str(tmp_path / "skills")
        loader = SkillLoader(skills_base_dir=skills_dir)

        history = loader.get_version_history("nonexistent")
        assert history is None
        loader.stop_watcher()

    def test_save_with_backup(self, tmp_path):
        skills_dir = str(tmp_path / "skills_v")
        loader = SkillLoader(skills_base_dir=skills_dir)
        for lvl in [SkillLevel.GLOBAL, SkillLevel.PROJECT, SkillLevel.AGENT]:
            loader.registry.clear_level(lvl)

        path = loader.save_skill_file(
            skill_id="backup_test",
            content="# Backup Test v1",
            level=SkillLevel.PROJECT,
            sub_id="testproj",
        )
        assert path is not None
        assert "backup_test.md" in path

        path2 = loader.save_skill_file(
            skill_id="backup_test",
            content="# Backup Test v2",
            level=SkillLevel.PROJECT,
            sub_id="testproj",
        )
        assert path2 is not None
        loader.stop_watcher()

    def test_save_missing_sub_id(self, tmp_path):
        skills_dir = str(tmp_path / "skills_v2")
        loader = SkillLoader(skills_base_dir=skills_dir)

        with pytest.raises(ValueError):
            loader.save_skill_file(
                skill_id="test",
                content="# Test",
                level=SkillLevel.PROJECT,
            )
        loader.stop_watcher()


class TestSkillExportImport:
    def test_export_skill(self, tmp_path):
        skills_dir = str(tmp_path / "skills")
        loader = SkillLoader(skills_base_dir=skills_dir)
        for lvl in [SkillLevel.GLOBAL, SkillLevel.PROJECT, SkillLevel.AGENT]:
            loader.registry.clear_level(lvl)

        s = Skill(
            metadata=SkillMetadata(
                skill_id="export_test",
                name="导出测试",
                version="1.0.0",
                applicable_tasks=["prediction"],
                required_context=["material"],
                tags=["export"],
            ),
            raw_content=SAMPLE_YAML_SKILL,
            body="# 导出测试\n\n内容...",
        )
        loader.registry.register(s)

        package = loader.export_skill("export_test")
        assert package is not None
        assert package["skill_id"] == "export_test"
        assert "raw_content" in package
        assert "metadata" in package
        assert package["metadata"]["name"] == "导出测试"
        loader.stop_watcher()

    def test_export_nonexistent(self, tmp_path):
        skills_dir = str(tmp_path / "skills")
        loader = SkillLoader(skills_base_dir=skills_dir)

        package = loader.export_skill("nonexistent")
        assert package is None
        loader.stop_watcher()

    def test_import_skill(self, tmp_path):
        skills_dir = str(tmp_path / "skills_import")
        loader = SkillLoader(skills_base_dir=skills_dir)
        for lvl in [SkillLevel.GLOBAL, SkillLevel.PROJECT, SkillLevel.AGENT]:
            loader.registry.clear_level(lvl)

        package = {
            "skill_id": "import_test",
            "name": "导入测试",
            "version": "1.0.0",
            "raw_content": """---
skill_id: import_test
name: 导入测试
version: 1.0.0
applicable_tasks: ["*"]
required_context: []
tags: ["imported"]
---
# 导入测试
""",
            "metadata": {
                "skill_id": "import_test",
                "name": "导入测试",
                "version": "1.0.0",
                "applicable_tasks": ["*"],
                "required_context": [],
                "tags": ["imported"],
            },
        }

        imported = loader.import_skill(package, SkillLevel.PROJECT, "testproj")
        assert imported is not None
        assert imported.metadata.skill_id == "import_test"
        assert "imported" in imported.metadata.tags
        loader.stop_watcher()

    def test_import_invalid_package(self, tmp_path):
        skills_dir = str(tmp_path / "skills_imp2")
        loader = SkillLoader(skills_base_dir=skills_dir)

        with pytest.raises(ValueError):
            loader.import_skill({}, SkillLevel.PROJECT, "testproj")
        loader.stop_watcher()


class TestSkillRating:
    def test_rate_skill(self, tmp_path):
        skills_dir = str(tmp_path / "skills")
        loader = SkillLoader(skills_base_dir=skills_dir)
        for lvl in [SkillLevel.GLOBAL, SkillLevel.PROJECT, SkillLevel.AGENT]:
            loader.registry.clear_level(lvl)

        s = Skill(
            metadata=SkillMetadata(
                skill_id="rate_test",
                name="rate_test",
                version="1.0.0",
            )
        )
        loader.registry.register(s)

        result = loader.rate_skill("rate_test", 4.0)
        assert result["avg_rating"] == 4.0
        assert result["rating_count"] == 1
        loader.stop_watcher()

    def test_rate_skill_average(self, tmp_path):
        skills_dir = str(tmp_path / "skills")
        loader = SkillLoader(skills_base_dir=skills_dir)
        for lvl in [SkillLevel.GLOBAL, SkillLevel.PROJECT, SkillLevel.AGENT]:
            loader.registry.clear_level(lvl)

        s = Skill(
            metadata=SkillMetadata(
                skill_id="rate_avg",
                name="rate_avg",
                version="1.0.0",
            )
        )
        loader.registry.register(s)

        loader.rate_skill("rate_avg", 4.0)
        result = loader.rate_skill("rate_avg", 2.0)
        assert result["avg_rating"] == 3.0
        assert result["rating_count"] == 2
        loader.stop_watcher()

    def test_rate_skill_invalid_range(self, tmp_path):
        skills_dir = str(tmp_path / "skills")
        loader = SkillLoader(skills_base_dir=skills_dir)

        s = Skill(
            metadata=SkillMetadata(
                skill_id="rate_invalid",
                name="rate_invalid",
                version="1.0.0",
            )
        )
        loader.registry.register(s)

        with pytest.raises(ValueError):
            loader.rate_skill("rate_invalid", 6.0)
        with pytest.raises(ValueError):
            loader.rate_skill("rate_invalid", -1.0)
        loader.stop_watcher()

    def test_rate_nonexistent_skill(self, tmp_path):
        skills_dir = str(tmp_path / "skills")
        loader = SkillLoader(skills_base_dir=skills_dir)

        with pytest.raises(KeyError):
            loader.rate_skill("nonexistent", 3.0)
        loader.stop_watcher()


class TestSkillFileWatcher:
    def test_infer_level_from_path(self, tmp_path):
        skills_dir = str(tmp_path / "skills")
        loader = SkillLoader(skills_base_dir=skills_dir)
        watcher = SkillFileWatcher(skills_dir, loader)

        assert (
            watcher._infer_level(
                os.path.join(skills_dir, "global", "error_handling.md")
            )
            == SkillLevel.GLOBAL
        )
        assert (
            watcher._infer_level(os.path.join(skills_dir, "projects", "p1", "skill.md"))
            == SkillLevel.PROJECT
        )
        assert (
            watcher._infer_level(os.path.join(skills_dir, "agents", "a1", "skill.md"))
            == SkillLevel.AGENT
        )
        loader.stop_watcher()

    def test_infer_level_unknown(self, tmp_path):
        skills_dir = str(tmp_path / "skills")
        loader = SkillLoader(skills_base_dir=skills_dir)
        watcher = SkillFileWatcher(skills_dir, loader)

        assert (
            watcher._infer_level(os.path.join(skills_dir, "other", "skill.md"))
            == SkillLevel.GLOBAL
        )
        loader.stop_watcher()


class TestSkillEnumMapping:
    def test_skill_level_values(self):
        assert SkillLevel.GLOBAL.value == "global"
        assert SkillLevel.PROJECT.value == "project"
        assert SkillLevel.AGENT.value == "agent"

    def test_skill_priority_values(self):
        assert SkillPriority.GLOBAL.value == 100
        assert SkillPriority.PROJECT.value == 50
        assert SkillPriority.AGENT.value == 10

    def test_priority_map(self):
        assert PRIORITY_MAP[SkillLevel.GLOBAL] == SkillPriority.GLOBAL
        assert PRIORITY_MAP[SkillLevel.PROJECT] == SkillPriority.PROJECT
        assert PRIORITY_MAP[SkillLevel.AGENT] == SkillPriority.AGENT


class TestSkillProperties:
    def test_current_version(self):
        s = Skill(
            metadata=SkillMetadata(skill_id="vtest", name="vtest", version="1.0.0")
        )
        s.versions["1.0.0"] = SkillVersion(
            version="1.0.0", content_hash="hash1", file_path="/t/v1.md"
        )
        s.versions["2.0.0"] = SkillVersion(
            version="2.0.0", content_hash="hash2", file_path="/t/v2.md"
        )
        s.versions["1.5.0"] = SkillVersion(
            version="1.5.0", content_hash="hash15", file_path="/t/v15.md"
        )

        assert s.current_version.version == "2.0.0"

    def test_current_version_empty(self):
        s = Skill(
            metadata=SkillMetadata(skill_id="vtest2", name="vtest2", version="1.0.0")
        )
        assert s.current_version is None


class TestEdgeCases:
    def _make_loader(self, tmp_path, name="skills"):
        skills_dir = str(tmp_path / name)
        loader = SkillLoader(skills_base_dir=skills_dir)
        for lvl in [SkillLevel.GLOBAL, SkillLevel.PROJECT, SkillLevel.AGENT]:
            loader.registry.clear_level(lvl)
        return loader

    def test_duplicate_skill_register(self, tmp_path):
        loader = self._make_loader(tmp_path)

        s1 = Skill(metadata=SkillMetadata(skill_id="dup", name="d1", version="1.0.0"))
        s2 = Skill(metadata=SkillMetadata(skill_id="dup", name="d2", version="2.0.0"))
        loader.registry.register(s1)
        loader.registry.register(s2)

        assert loader.registry.get("dup") is s2
        loader.stop_watcher()

    def test_deactivated_skill_not_injected(self, tmp_path):
        loader = self._make_loader(tmp_path)

        s = Skill(
            metadata=SkillMetadata(
                skill_id="deactivated",
                name="deactivated",
                version="1.0.0",
                applicable_tasks=["*"],
                required_context=[],
            )
        )
        loader.registry.register(s)
        s.is_active = False

        skills = loader.get_skills_for_task(task_type="prediction")
        assert len([x for x in skills if x.metadata.skill_id == "deactivated"]) == 0
        loader.stop_watcher()

    def test_load_project_skills_nonexistent_dir(self, tmp_path):
        loader = self._make_loader(tmp_path)
        skills = loader.load_project_skills("nonexistent")
        assert skills == []
        loader.stop_watcher()

    def test_load_agent_skills_nonexistent_dir(self, tmp_path):
        loader = self._make_loader(tmp_path)
        skills = loader.load_agent_skills("nonexistent")
        assert skills == []
        loader.stop_watcher()

    def test_get_stats(self, tmp_path):
        loader = self._make_loader(tmp_path)

        s = Skill(
            metadata=SkillMetadata(skill_id="stats1", name="stats1", version="1.0.0")
        )
        loader.registry.register(s)

        stats = loader.get_stats()
        assert "total" in stats
        assert "active" in stats
        assert "skills_base_dir" in stats
        loader.stop_watcher()

    def test_hot_reload_single_skill(self, tmp_path):
        skills_dir = tmp_path / "skills_hot"
        proj_dir = skills_dir / "projects" / "testproj"
        proj_dir.mkdir(parents=True)

        skill_file = proj_dir / "hot_test.md"
        skill_file.write_text(SAMPLE_YAML_SKILL, encoding="utf-8")

        loader = SkillLoader(skills_base_dir=str(skills_dir))
        for lvl in [SkillLevel.GLOBAL, SkillLevel.PROJECT, SkillLevel.AGENT]:
            loader.registry.clear_level(lvl)

        loader.load_project_skills("testproj")
        result = loader.hot_reload("sample_test")
        assert result["status"] in ("reloaded", "not_found", "error")
        loader.stop_watcher()

    def test_hot_reload_full(self, tmp_path):
        skills_dir = str(tmp_path / "skills_hot2")
        loader = SkillLoader(skills_base_dir=skills_dir)
        result = loader.hot_reload()
        assert result["status"] == "full_reload"
        assert "count" in result
        loader.stop_watcher()

    def test_execute_skill_not_found(self, tmp_path):
        loader = self._make_loader(tmp_path)
        with pytest.raises(KeyError):
            loader.execute_skill("nonexistent")
        loader.stop_watcher()

    def test_execute_skill_not_loaded(self, tmp_path):
        loader = self._make_loader(tmp_path)
        s = Skill(
            metadata=SkillMetadata(
                skill_id="unloaded", name="unloaded", version="1.0.0"
            )
        )
        loader.registry.register(s)

        with pytest.raises(RuntimeError):
            s.execute()
        loader.stop_watcher()


class TestInjectSkillsIntegration:
    def test_global_singleton(self, tmp_path):
        skills_dir = str(tmp_path / "skills_integ")
        os.environ["LNN_SKILLS_PATH"] = skills_dir

        loader = init_skill_loader(skills_dir)
        assert loader is not None
        assert loader.skills_base == skills_dir
        loader.stop_watcher()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
