"""后处理器手动锚定合规测试（独立于生成器的语法校验层）。

与 test_postprocessor_golden.py 的关系：
    golden 测试是「自生成自比对」，防重构漂移；本测试用**独立实现**的
    方言合规校验器（tests/utils/nc_dialect_checker.py，纯标准库、零复用
    app.postprocessor 代码）对 golden 基线做语法合规判定——防止「生成器
    与黄金文件一起变错」（例：Heidenhain 会话式程序泄漏 G00/G01 行、
    hooks 方言静默加载失败，均为本层设计前已发生的真实缺陷）。

覆盖：
    - 全部 22 个黄金文件（9 内置 × 标准/扩展 + 2 声明方言 × 标准/扩展）
      标准 = strict 全规则；扩展 = structural（能力探针不做型号白名单，
      型号级复核属编程站实测，见 docs/development/postprocessor-验证矩阵.md）
    - 负样本对照：变异程序必须被拦截（防止校验器退化成永远通过的空转检查）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.utils.nc_dialect_checker import NcDialectChecker

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden" / "postprocessor"

# 全部 golden 控制器 id（标准基线文件存在即纳入）
CONTROLLER_IDS = sorted({f.name.removesuffix("_extended.nc").removesuffix(".nc") for f in GOLDEN_DIR.glob("*.nc")})


@pytest.mark.regression
@pytest.mark.postprocessor
@pytest.mark.parametrize("controller_id", CONTROLLER_IDS)
def test_golden_standard_program_is_dialect_compliant(controller_id: str):
    """标准程序黄金基线：strict 全规则合规（结构 + 词法 + 白名单 + 精度 + 进给）。"""
    golden_path = GOLDEN_DIR / f"{controller_id}.nc"
    assert golden_path.exists(), f"标准黄金文件缺失: {golden_path}"
    nc_text = golden_path.read_text(encoding="utf-8")

    issues = NcDialectChecker(controller_id).check(nc_text, tier="strict")
    assert not issues, (
        f"{controller_id} 标准程序存在方言合规问题:\n"
        + "\n".join(f"  - {issue}" for issue in issues)
        + "\n建议操作：修复后处理器输出（不要直接改黄金文件）；"
        "若属控制器型号差异，在 nc_dialect_checker 的 profile 中登记依据。"
    )


@pytest.mark.regression
@pytest.mark.postprocessor
@pytest.mark.parametrize("controller_id", CONTROLLER_IDS)
def test_golden_extended_program_is_structurally_compliant(controller_id: str):
    """扩展程序黄金基线：structural 级合规（结构 + 词法；白名单留给编程站实测）。"""
    golden_path = GOLDEN_DIR / f"{controller_id}_extended.nc"
    assert golden_path.exists(), f"扩展黄金文件缺失: {golden_path}"
    nc_text = golden_path.read_text(encoding="utf-8")

    issues = NcDialectChecker(controller_id).check(nc_text, tier="structural")
    assert not issues, f"{controller_id} 扩展程序存在结构性合规问题:\n" + "\n".join(f"  - {issue}" for issue in issues)


# ── 负样本对照：校验器必须能拦住已知错误形态（防空转） ──────────────


@pytest.mark.regression
@pytest.mark.postprocessor
class TestCheckerCatchesViolations:
    """对每个方言族注入典型违规，断言校验器产生 issue。"""

    def _load_standard(self, controller_id: str) -> str:
        return (GOLDEN_DIR / f"{controller_id}.nc").read_text(encoding="utf-8")

    def test_fanuc_unknown_mcode_rejected(self):
        text = self._load_standard("fanuc_0i").replace("M30", "M999", 1)
        issues = NcDialectChecker("fanuc_0i").check(text)
        assert any(i.rule == "unknown_gcode" for i in issues)

    def test_fanuc_illegal_word_rejected(self):
        text = self._load_standard("fanuc_0i") + "GOTO 100\n"
        issues = NcDialectChecker("fanuc_0i").check(text)
        assert any(i.rule == "illegal_word" for i in issues)

    def test_fanuc_missing_program_end_rejected(self):
        text = self._load_standard("fanuc_0i").replace("M30", "M09", 1)
        issues = NcDialectChecker("fanuc_0i").check(text)
        assert any(i.rule == "structure" for i in issues)

    def test_fanuc_missing_o_number_rejected(self):
        text = self._load_standard("fanuc_0i")
        text = "\n".join(ln for ln in text.splitlines() if not ln.startswith("O1000"))
        issues = NcDialectChecker("fanuc_0i").check(text)
        assert any(i.rule == "structure" for i in issues)

    def test_heidenhain_gcode_leak_rejected(self):
        """核心回归样本：对话式程序中泄漏 G00 行必须被拦截
        （内置 Heidenhain 曾因未覆写 format_rapid_move 产生此缺陷）。"""
        text = self._load_standard("heidenhain_tnc") + "G00 X0.000 Y0.000 Z30.000\n"
        issues = NcDialectChecker("heidenhain_tnc").check(text)
        assert any(i.rule == "gcode_in_dialog" for i in issues)

    def test_heidenhain_missing_end_pgm_rejected(self):
        text = self._load_standard("heidenhain_tnc")
        text = "\n".join(ln for ln in text.splitlines() if "END PGM" not in ln)
        issues = NcDialectChecker("heidenhain_tnc").check(text)
        assert any(i.rule == "structure" for i in issues)

    def test_siemens_percent_wrapper_rejected(self):
        text = "%" + self._load_standard("siemens_840d") + "%\n"
        issues = NcDialectChecker("siemens_840d").check(text)
        assert any(i.rule == "structure" for i in issues)

    def test_siemens_unknown_gcode_rejected(self):
        text = self._load_standard("siemens_840d").replace("G90", "G68", 1)
        issues = NcDialectChecker("siemens_840d").check(text)
        assert any(i.rule == "unknown_gcode" for i in issues)

    def test_gsk_exclusive_code_rejected_for_gsk(self):
        """GSK 白名单不含五轴动态补偿 G43.4——注入必须被拦截。"""
        text = self._load_standard("gsk_980_25i").replace("G43 H01", "G43.4 H01", 1)
        issues = NcDialectChecker("gsk_980_25i").check(text)
        assert any(i.rule == "unknown_gcode" for i in issues)
