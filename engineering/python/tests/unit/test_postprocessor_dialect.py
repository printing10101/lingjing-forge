"""方言声明化测试：声明加载 / 编译 / 注册 / 黄金一致性。

对应 docs/development/postprocessor-方言声明化设计.md P1 验收标准：
「内置方言经"声明镜像"渲染输出 = 黄金测试逐字符一致」。

关键断言：``postprocessor-plugins/knd_1000_2000_3000/`` 的声明式方言，
其标准序列输出必须与内置 ``KNDPostProcessor`` 的输出逐字符一致
（即与 golden 文件 ``knd_1000_2000_3000.nc`` 一致）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.postprocessor.base import BasePostProcessor
from app.postprocessor.dialect.compiler import DialectCompileError, DialectCompiler
from app.postprocessor.dialect.declaration import (
    DialectDeclaration,
    DialectDeclarationError,
)
from app.postprocessor.dialect.registry import DialectRegistry
from app.postprocessor.knd import KNDPostProcessor
from tests.regression.test_postprocessor_golden import (
    FIXED_DATE,
    build_standard_program,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
PLUGIN_ROOT = REPO_ROOT / "postprocessor-plugins"
KND_PLUGIN_DIR = PLUGIN_ROOT / "knd_1000_2000_3000"


@pytest.fixture(autouse=True)
def _fixed_date(monkeypatch):
    """固定日期：与 golden 测试一致，消除 header 日期不确定性。"""
    monkeypatch.setattr(BasePostProcessor, "_date_string", staticmethod(lambda: FIXED_DATE))


# 声明加载与校验


class TestDeclarationLoading:
    def test_load_knd_declaration(self):
        decl = DialectDeclaration.from_yaml(KND_PLUGIN_DIR / "dialect.yaml")
        assert decl.id == "knd_1000_2000_3000"
        assert decl.extends == "fanuc_0i"
        assert decl.version == "1.0.0"
        assert "format_header" in decl.templates
        assert decl.templates["format_header"].name == "header.j2"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(DialectDeclarationError):
            DialectDeclaration.from_yaml(tmp_path / "nope.yaml")

    def test_invalid_extends_rejected(self, tmp_path):
        bad = tmp_path / "dialect.yaml"
        bad.write_text(
            "id: x\nname: X\nversion: 1.0.0\nextends: nonexistent_dialect\n",
            encoding="utf-8",
        )
        with pytest.raises(DialectDeclarationError):
            DialectDeclaration.from_yaml(bad)

    def test_invalid_template_method_rejected(self, tmp_path):
        bad = tmp_path / "dialect.yaml"
        bad.write_text(
            "id: x\nname: X\nversion: 1.0.0\nextends: fanuc_0i\ntemplates:\n  format_evil: templates/evil.j2\n",
            encoding="utf-8",
        )
        with pytest.raises(DialectDeclarationError):
            DialectDeclaration.from_yaml(bad)

    def test_missing_template_file_rejected(self, tmp_path):
        bad = tmp_path / "dialect.yaml"
        bad.write_text(
            "id: x\nname: X\nversion: 1.0.0\nextends: fanuc_0i\ntemplates:\n  format_header: templates/absent.j2\n",
            encoding="utf-8",
        )
        with pytest.raises(DialectDeclarationError):
            DialectDeclaration.from_yaml(bad)

    def test_invalid_hooks_rejected(self, tmp_path):
        bad = tmp_path / "dialect.yaml"
        bad.write_text(
            "id: x\nname: X\nversion: 1.0.0\nextends: fanuc_0i\nhooks: not-a-path\n",
            encoding="utf-8",
        )
        with pytest.raises(DialectDeclarationError):
            DialectDeclaration.from_yaml(bad)

    def test_missing_required_fields_rejected(self, tmp_path):
        bad = tmp_path / "dialect.yaml"
        bad.write_text("id: x\n", encoding="utf-8")
        with pytest.raises(DialectDeclarationError):
            DialectDeclaration.from_yaml(bad)


# 编译


class TestCompilation:
    def test_compile_knd_declaration(self):
        decl = DialectDeclaration.from_yaml(KND_PLUGIN_DIR / "dialect.yaml")
        compiler = DialectCompiler()
        cls = compiler.compile(decl)
        assert issubclass(cls, BasePostProcessor)
        # extends fanuc_0i 编译类继承 FanucPostProcessor（含全部 Fanuc 方法）
        from app.postprocessor.fanuc import FanucPostProcessor

        assert issubclass(cls, FanucPostProcessor)
        assert cls.CONTROLLER_ID == "knd_1000_2000_3000"

    def test_compile_without_extends_raises(self):
        decl = DialectDeclaration(
            id="x",
            name="X",
            version="1.0.0",
            extends=None,
            templates={"format_header": KND_PLUGIN_DIR / "templates" / "header.j2"},
        )
        with pytest.raises(DialectCompileError):
            DialectCompiler().compile(decl)

    def test_hooks_load_and_override(self):
        """hooks 方法覆盖基类实现（代码钩子表达模板难表达的逻辑）。"""

        decl = DialectDeclaration(
            id="hooky",
            name="Hooky",
            version="1.0.0",
            extends="fanuc_0i",
            hooks="tests.utils.test_dialect_hooks:CustomCycleHooks",
        )
        cls = DialectCompiler().compile(decl)
        pp = cls()

        # hooks 覆盖了 format_cycle_drill（自定义格式）
        drill = pp.format_cycle_drill(x=10.0, y=10.0, z=0.0, depth=15.0)
        assert "CUSTOM CYCLE" in drill
        # 其余方法继承基类
        header = pp.format_header(program_number=1)
        assert "G21 G17 G40" in header

    def test_hooks_priority_over_templates(self):
        """hooks 优先级 > 模板：同名方法由 hooks 提供，模板被跳过。"""

        tpl = KND_PLUGIN_DIR / "templates" / "cycle_drill.j2"
        decl = DialectDeclaration(
            id="hooky2",
            name="Hooky2",
            version="1.0.0",
            extends="fanuc_0i",
            hooks="tests.utils.test_dialect_hooks:CustomCycleHooks",
            templates={"format_cycle_drill": tpl},
        )
        cls = DialectCompiler().compile(decl)
        pp = cls()
        drill = pp.format_cycle_drill(x=10.0, y=10.0, z=0.0, depth=15.0)
        assert "CUSTOM CYCLE" in drill  # hooks 优先，模板未生效

    def test_hooks_extension_method(self):
        """hooks 可提供基类 MRO 之外的新方法。"""

        decl = DialectDeclaration(
            id="hooky3",
            name="Hooky3",
            version="1.0.0",
            extends="fanuc_0i",
            hooks="tests.utils.test_dialect_hooks:CustomCycleHooks",
        )
        cls = DialectCompiler().compile(decl)
        pp = cls()
        out = pp.format_special_cycle(value=3.14)
        assert out == "SPECIAL 3.140"

    def test_hooks_bad_format_raises(self):
        decl = DialectDeclaration(
            id="x",
            name="X",
            version="1.0.0",
            extends="fanuc_0i",
            hooks="no-colon-here",
        )
        with pytest.raises(DialectCompileError):
            DialectCompiler().compile(decl)

    def test_hooks_module_not_found_raises(self):
        decl = DialectDeclaration(
            id="x",
            name="X",
            version="1.0.0",
            extends="fanuc_0i",
            hooks="nonexistent.module:Hooks",
        )
        with pytest.raises(DialectCompileError):
            DialectCompiler().compile(decl)

    def test_hooks_no_format_methods_raises(self):
        decl = DialectDeclaration(
            id="x",
            name="X",
            version="1.0.0",
            extends="fanuc_0i",
            hooks="tests.utils.test_dialect_hooks:ProbeHooks",  # ProbeHooks 只有 format_probe（非 format_* 前缀白名单）
        )
        # ProbeHooks 定义了 format_probe（以 format_ 开头），应能加载
        cls = DialectCompiler().compile(decl)
        pp = cls()
        out = pp.format_probe(probe_number=2, x_pos=5.0)
        assert out == "PROBE 2 X5.000"

    def test_template_method_absent_in_base_raises(self, tmp_path):
        # 构造一个声明模板方法在基类 MRO 中不存在的情况
        tpl = tmp_path / "bad.j2"
        tpl.write_text("NOPE\n", encoding="utf-8")
        decl = DialectDeclaration(
            id="x",
            name="X",
            version="1.0.0",
            extends="fanuc_0i",
            templates={"format_nonexistent": tpl},
        )
        with pytest.raises(DialectCompileError):
            DialectCompiler().compile(decl)

    def test_template_renders_with_fmt_filter(self):
        decl = DialectDeclaration.from_yaml(KND_PLUGIN_DIR / "dialect.yaml")
        cls = DialectCompiler().compile(decl)
        pp = cls()
        header = pp.format_header(program_number=42)
        assert "O0042" in header
        assert "KND1000/2000/3000" in header
        assert "G21 G17 G40 G49 G80 G90 G94" in header


# 注册表


class TestRegistry:
    def test_discover_finds_knd(self):
        registry = DialectRegistry(plugin_root=PLUGIN_ROOT)
        found = registry.discover()
        assert "knd_1000_2000_3000" in found

    def test_discover_nonexistent_root_is_empty(self, tmp_path):
        registry = DialectRegistry(plugin_root=tmp_path / "absent")
        assert registry.discover() == []

    def test_compile_and_register(self):
        from app.postprocessor.registry import PostProcessorRegistry

        target = PostProcessorRegistry()
        registry = DialectRegistry(plugin_root=PLUGIN_ROOT)
        registry.discover()
        registry.compile_all()
        count = registry.register_to(target)
        assert count >= 1

        # 方言已注册：get_processor 返回编译方言实例
        pp = target.get_processor("knd_1000_2000_3000")
        assert isinstance(pp, BasePostProcessor)

    def test_register_without_compile_raises(self):
        from app.postprocessor.registry import PostProcessorRegistry

        registry = DialectRegistry(plugin_root=PLUGIN_ROOT)
        registry.discover()
        with pytest.raises(DialectCompileError):
            registry.register_to(PostProcessorRegistry())

    def test_load_dialects_helper(self):
        from app.postprocessor.dialect.registry import load_dialects
        from app.postprocessor.registry import PostProcessorRegistry

        target = PostProcessorRegistry()
        count = load_dialects(plugin_root=PLUGIN_ROOT, target=target)
        assert count >= 1


# 黄金一致性（P1 验收核心）

# 声明镜像 内置类对照表（P2 迁移逐个追加）
MIRROR_BUILTIN_CLASSES = {
    "knd_1000_2000_3000": KNDPostProcessor,
}

# GSK / HNC / Mitsubishi / Fagor 是 P2 多方法声明镜像
from app.postprocessor.gsk import GSKPostProcessor  # noqa: E402
from app.postprocessor.hnc import HNCPostProcessor  # noqa: E402
from app.postprocessor.mitsubishi import MitsubishiPostProcessor  # noqa: E402
from app.postprocessor.fagor import FagorPostProcessor  # noqa: E402

MIRROR_BUILTIN_CLASSES["gsk_980_25i"] = GSKPostProcessor
MIRROR_BUILTIN_CLASSES["hnc_848_22"] = HNCPostProcessor
MIRROR_BUILTIN_CLASSES["mitsubishi_m70_m80"] = MitsubishiPostProcessor
MIRROR_BUILTIN_CLASSES["fagor_8055"] = FagorPostProcessor


class TestGoldenConsistency:
    @pytest.mark.parametrize("dialect_id", sorted(MIRROR_BUILTIN_CLASSES.keys()))
    def test_compiled_dialect_matches_builtin(self, dialect_id):
        """声明式方言的标准序列输出 == 内置方言逐字符一致。"""
        builtin_cls = MIRROR_BUILTIN_CLASSES[dialect_id]
        decl = DialectDeclaration.from_yaml(PLUGIN_ROOT / dialect_id / "dialect.yaml")
        compiled_cls = DialectCompiler().compile(decl)

        compiled_output = build_standard_program(compiled_cls())
        builtin_output = build_standard_program(builtin_cls())

        assert compiled_output == builtin_output, (
            f"声明式 {dialect_id} 方言与内置 {builtin_cls.__name__} 输出不一致。"
            "建议操作：检查 postprocessor-plugins/<id>/templates/ 是否精确复刻了"
            "内置方言对应方法的输出。"
        )

    @pytest.mark.parametrize("dialect_id", sorted(MIRROR_BUILTIN_CLASSES.keys()))
    def test_compiled_dialect_matches_golden_file(self, dialect_id):
        """声明式方言输出 == golden 文件逐字符一致。"""
        from tests.regression.test_postprocessor_golden import GOLDEN_DIR

        decl = DialectDeclaration.from_yaml(PLUGIN_ROOT / dialect_id / "dialect.yaml")
        compiled_cls = DialectCompiler().compile(decl)

        output = build_standard_program(compiled_cls())
        golden = (GOLDEN_DIR / f"{dialect_id}.nc").read_text(encoding="utf-8")
        assert output == golden

    @pytest.mark.parametrize("dialect_id", sorted(MIRROR_BUILTIN_CLASSES.keys()))
    def test_compiled_dialect_extended_matches_builtin(self, dialect_id):
        """扩展序列同样一致（模板方法外的能力继承基类）。"""
        from tests.regression.test_postprocessor_golden import build_extended_program

        builtin_cls = MIRROR_BUILTIN_CLASSES[dialect_id]
        decl = DialectDeclaration.from_yaml(PLUGIN_ROOT / dialect_id / "dialect.yaml")
        compiled_cls = DialectCompiler().compile(decl)

        compiled_output = build_extended_program(compiled_cls())
        builtin_output = build_extended_program(builtin_cls())

        assert compiled_output == builtin_output
