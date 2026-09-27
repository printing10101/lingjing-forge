"""提示词注册表测试（自进化 M0 · Prompt Registry v0）。

覆盖：注册/读取/最新版本语义、渲染字面替换（JSON 花括号安全）、
未注册报错、默认条目播种、热更新覆盖。
"""

import pytest

from app.ai.prompts import (
    AUGMENTER_PARAM_PROPOSAL_SYSTEM_ID,
    ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID,
    ORCHESTRATOR_PLANNING_SYSTEM_ID,
    ORCHESTRATOR_PLANNING_USER_ID,
    PromptRegistry,
    get_prompt_registry,
    reset_prompt_registry,
)


@pytest.fixture()
def registry():
    return PromptRegistry()


class TestRegistryMechanics:
    def test_register_and_get(self, registry):
        registry.register("a.b", 1, "hello {name}", "测试")
        entry = registry.get("a.b")
        assert entry.prompt_id == "a.b"
        assert entry.version == 1
        assert entry.template == "hello {name}"

    def test_get_latest_version(self, registry):
        registry.register("a.b", 1, "v1")
        registry.register("a.b", 2, "v2")
        assert registry.get("a.b").version == 2
        assert registry.get("a.b", version=1).template == "v1"

    def test_get_unknown_raises(self, registry):
        with pytest.raises(KeyError):
            registry.get("nope.nope")

    def test_get_unknown_version_raises(self, registry):
        registry.register("a.b", 1, "v1")
        with pytest.raises(KeyError):
            registry.get("a.b", version=9)

    def test_register_validation(self, registry):
        with pytest.raises(ValueError):
            registry.register("", 1, "t")
        with pytest.raises(ValueError):
            registry.register("a.b", 1, "")
        with pytest.raises(ValueError):
            registry.register("a.b", 0, "t")
        with pytest.raises(ValueError):
            registry.register("a.b", "1", "t")  # type: ignore[arg-type]

    def test_reregister_same_key_overwrites(self, registry):
        registry.register("a.b", 1, "old")
        registry.register("a.b", 1, "new")
        assert registry.get("a.b").template == "new"

    def test_render_literal_replacement_keeps_json_braces(self, registry):
        """正文含 JSON 示例花括号时渲染必须安全（str.format 会炸）。"""
        registry.register("a.b", 1, '输出 JSON：{"k": 1}，候选: {data}')
        text, entry = registry.render("a.b", data="[1,2]")
        assert text == '输出 JSON：{"k": 1}，候选: [1,2]'
        assert entry.version == 1

    def test_render_missing_placeholder_kept_verbatim(self, registry):
        registry.register("a.b", 1, "x={a} y={b}")
        text, _ = registry.render("a.b", a="1")
        assert text == "x=1 y={b}"

    def test_list_ids(self, registry):
        registry.register("b.c", 1, "t")
        registry.register("a.b", 1, "t")
        registry.register("a.b", 2, "t")
        assert registry.list_ids() == ["a.b", "b.c"]


class TestDefaultEntries:
    @pytest.fixture(autouse=True)
    def _fresh_default(self):
        reset_prompt_registry()
        yield
        reset_prompt_registry()

    def test_defaults_seeded(self):
        reg = get_prompt_registry()
        assert ORCHESTRATOR_PLANNING_SYSTEM_ID in reg.list_ids()
        assert ORCHESTRATOR_PLANNING_USER_ID in reg.list_ids()
        assert ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID in reg.list_ids()
        assert AUGMENTER_PARAM_PROPOSAL_SYSTEM_ID in reg.list_ids()

    def test_planning_user_renders_placeholders(self):
        text, _ = get_prompt_registry().render(
            ORCHESTRATOR_PLANNING_USER_ID, catalog_json="[1]", summary_json="{}"
        )
        assert "[1]" in text and "{}" in text
        assert "{catalog_json}" not in text

    def test_v1_texts_match_pre_migration_inline_versions(self):
        """v0 条目必须与迁移前内联提示词逐字一致（行为不变的等价迁移）。"""
        reg = get_prompt_registry()
        assert reg.get(ORCHESTRATOR_PLANNING_SYSTEM_ID).template == "你是严格的 JSON 输出规划器，无 markdown 围栏。"
        assert (
            reg.get(ORCHESTRATOR_GCODE_REPAIR_SYSTEM_ID).template
            == "你是数控安全修复助手。给出的 G 代码未通过安全校验，"
            "请只修复报告列出的问题，严禁改动任何其他行、严禁增删功能。"
            "直接输出修复后的完整 G 代码纯文本（无 markdown 围栏、无解释）。"
            "若无法在不改动其他内容的前提下修复，输出原样代码。"
        )
        assert "adjustments" in reg.get(AUGMENTER_PARAM_PROPOSAL_SYSTEM_ID).template

    def test_reset_reseeds_defaults(self):
        get_prompt_registry().register("custom.x", 1, "t")
        reset_prompt_registry()
        reg = get_prompt_registry()
        assert "custom.x" not in reg.list_ids()
        assert ORCHESTRATOR_PLANNING_SYSTEM_ID in reg.list_ids()
