"""独立 NC 方言合规校验器（手动锚定合规层的核心，纯标准库实现）。

定位（与 tests/regression/test_postprocessor_manual_compliance.py 配套）：
    现有 golden 测试是「自生成自比对」——只能防回归，防不了生成器本身
    输出违反控制器语法规范（golden 再生会把错误固化）。本模块用**独立的**
    规则实现（不复用 app.postprocessor 任何代码）对 NC 程序做语法合规判定，
    与生成器形成交叉验证。

规则锚定与诚实声明：
    - ISO 字地址族（Fanuc/GSK/HNC/KND/Mitsubishi/Fagor/xMachine）规则依据
      Fanuc 0i-MF 系列编程手册的公开 G/M 代码集与字地址语法约定；
    - Heidenhain 规则依据 TNC 对话式编程约定（BEGIN/END PGM、L/CC/C 行、
      CYCL DEF/CALL、TOOL CALL；对话式程序中不得混用字地址 G 代码）；
    - Siemens 规则依据 Sinumerik 840D 约定（N 前缀可选、CYCLExx 调用、
      ; 注释、系统变量赋值、M17/M30）。
    每型控制器的个别代码差异（如 HNC 的 G74 参考点返回语义）属「待编程站/
    实机复核」项，登记于 docs/development/postprocessor-验证矩阵.md，
    不在本校验器中断言。

两级严格度：
    - STRICT（标准程序）：结构 + 字地址语法 + 代码白名单 + 精度 + 进给正值
    - STRUCTURAL（扩展序列）：仅结构 + 字地址语法（扩展序列是能力探针，
      可能包含超出该型号手册的基类兜底行，白名单判定留给编程站实测）

背景：本模块曾抓到并推动修复两个真实缺陷——
    1. Heidenhain 内置后处理器未覆写 format_rapid_move/format_linear_move，
       会话式程序中泄漏 G00/G01 行（真机会拒收）；
    2. heidenhain_tnc640 / siemens_840d 两个 hooks 声明方言因
       declaration.py 不支持列表格式而静默加载失败（从未注册）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ComplianceIssue:
    """单条合规问题。

    Attributes:
        line_no: 行号（1-based；0 表示文件级问题）
        line: 原始行文本
        rule: 规则标识（如 "unknown_gcode" / "gcode_in_dialog" / "structure"）
        message: 面向开发者的说明
    """

    line_no: int
    line: str
    rule: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - 调试便利
        return f"[{self.rule}] L{self.line_no}: {self.message} | {self.line.strip()[:60]}"


# ── 各控制器配置（规则锚定见模块 docstring）──────────────────────────

# ISO 字地址族共用 G 代码集（Fanuc 0i-MF 手册公开代码集的全集）
_ISO_G_FANUC_FULL: set[str] = {
    "G00",
    "G01",
    "G02",
    "G03",
    "G04",
    "G05.1",
    "G09",
    "G17",
    "G18",
    "G19",
    "G20",
    "G21",
    "G28",
    "G30",
    "G40",
    "G41",
    "G42",
    "G43",
    "G43.1",
    "G43.4",
    "G43.5",
    "G49",
    "G53",
    "G54",
    "G54.1",
    "G55",
    "G56",
    "G57",
    "G58",
    "G59",
    "G73",
    "G74",
    "G76",
    "G80",
    "G81",
    "G82",
    "G83",
    "G84",
    "G85",
    "G86",
    "G87",
    "G88",
    "G89",
    "G90",
    "G91",
    "G92",
    "G94",
    "G95",
    "G98",
    "G99",
}

_ISO_M_COMMON: set[str] = {
    "M00",
    "M01",
    "M02",
    "M03",
    "M04",
    "M05",
    "M06",
    "M08",
    "M09",
    "M30",
    "M98",
    "M99",
}


def _iso_subset(*codes: str) -> set[str]:
    return {f"G{c}" for c in codes}


def _norm_gm(prefix: str, digits: str) -> str:
    """G/M 代码归一化：各段去前导零（G00→G0，G43.4→G43.4，G01→G1）。"""
    return prefix + ".".join(str(int(p)) for p in digits.split("."))


@dataclass
class ControllerProfile:
    """控制器校验配置。

    Attributes:
        family: iso / heidenhain / siemens
        allowed_letters: 字地址合法字母全集（ISO 族）
        g_whitelist / m_whitelist: 代码白名单（STRICT 级）
        program_wrapper: 程序是否以 % 包裹（iso）
        o_number_required: 是否要求 O 程序号（fanuc 族 True；fagor 用 %01000 形式）
        comment_styles: 支持的注释风格（括号 / 分号）
    """

    family: str
    allowed_letters: str = "ABCDEFGHIJKLMNOPQRSTUWXYZ0123456789"
    g_whitelist: set[str] = field(default_factory=set)
    m_whitelist: set[str] = field(default_factory=set)
    program_wrapper: bool = True
    o_number_required: bool = True
    comment_paren: bool = True
    comment_semicolon: bool = False
    # Fagor 形式程序头（%01000 = wrapper + 程序号合一，无结尾 %）
    fagor_header: bool = False
    # 额外合法行模式（整行 regex，子程序调用等非字地址行）
    extra_line_patterns: list[str] = field(default_factory=list)


_PROFILES: dict[str, ControllerProfile] = {
    # Fanuc 0i-MF：全集（含 5 轴动态补偿 G43.4/43.5、高精度 G05.1）
    "fanuc_0i": ControllerProfile(family="iso", g_whitelist=_ISO_G_FANUC_FULL, m_whitelist=_ISO_M_COMMON),
    # GSK 980MDa 钻攻中心：Fanuc 兼容子集（无 43.4/43.5/05.1/54.1 五轴项；
    # 保留 G30 换刀点返回，与内置方言的 G30 用法一致）
    "gsk_980_25i": ControllerProfile(
        family="iso",
        g_whitelist=_ISO_G_FANUC_FULL - {"G05.1", "G43.4", "G43.5", "G54.1"},
        m_whitelist=_ISO_M_COMMON,
    ),
    # 华中 HNC-8 系列：Fanuc 兼容集 + G74（内置方言将 G74 用于参考点返回，
    # 该语义与华中手册的差异登记在验证矩阵「待复核」列，不在此断言）
    "hnc_848_22": ControllerProfile(family="iso", g_whitelist=_ISO_G_FANUC_FULL, m_whitelist=_ISO_M_COMMON),
    # KND 1000/2000/3000：Fanuc 兼容子集
    "knd_1000_2000_3000": ControllerProfile(
        family="iso",
        g_whitelist=_ISO_G_FANUC_FULL - {"G05.1", "G43.4", "G43.5", "G54.1"},
        m_whitelist=_ISO_M_COMMON,
    ),
    # Mitsubishi M70/M80：Fanuc 兼容全集（含 G05.1 高精度轨迹控制）
    "mitsubishi_m70_m80": ControllerProfile(family="iso", g_whitelist=_ISO_G_FANUC_FULL, m_whitelist=_ISO_M_COMMON),
    # Fagor 8055：程序头为 %01000 形式（包 wrapper + 程序号合一），无 O 程序号行；
    # G75 为该厂商手册的机床零点返回指令（用法 G75 X0. Y0. Z0.，登记待编程站复核）；
    # 子程序用 CALL Pxxxx / RET 行
    "fagor_8055": ControllerProfile(
        family="iso",
        g_whitelist=_ISO_G_FANUC_FULL | {"G75"},
        m_whitelist=_ISO_M_COMMON,
        o_number_required=False,
        program_wrapper=False,
        fagor_header=True,
        extra_line_patterns=[r"^CALL P\d+", r"^RET$"],
    ),
    # xMachine XM100：Fanuc 兼容 + 自定义 M101/M201（机床厂商自定义 M 代码）
    "xmachine_xm100": ControllerProfile(
        family="iso",
        g_whitelist=_ISO_G_FANUC_FULL,
        m_whitelist=_ISO_M_COMMON | {"M101", "M201"},
    ),
    # Heidenhain TNC：对话式编程，不允许字地址 G 代码
    "heidenhain_tnc": ControllerProfile(family="heidenhain"),
    "heidenhain_tnc640_declared": ControllerProfile(family="heidenhain"),
    # Siemens 840D：N 前缀可选、无 % 包裹（declared 方言带 %_N_ 头，也接受）
    "siemens_840d": ControllerProfile(family="siemens", program_wrapper=False, comment_semicolon=True),
    "siemens_840d_declared": ControllerProfile(family="siemens", program_wrapper=False, comment_semicolon=True),
}

# 字地址词法：字母 + 可选符号 + 数字/小数点
_WORD_RE = re.compile(r"[A-Za-z][0-9.+\-]*")
_G_RE = re.compile(r"^G(\d+(?:\.\d+)?)$", re.I)
_M_RE = re.compile(r"^M(\d+)$", re.I)
_HEIDENHAIN_BLOCK_RE = re.compile(r"^(\d+\s+)?")
_MAX_DECIMALS = 4


class NcDialectChecker:
    """独立 NC 方言合规校验器（每次 check 无状态）。"""

    def __init__(self, controller_id: str) -> None:
        if controller_id not in _PROFILES:
            raise KeyError(f"未知控制器: {controller_id}。已知: {sorted(_PROFILES)}")
        self.profile = _PROFILES[controller_id]

    # ── 公开入口 ──────────────────────────────────────────────

    def check(
        self,
        nc_text: str,
        tier: str = "strict",
    ) -> list[ComplianceIssue]:
        """校验 NC 程序文本，返回问题列表（空列表 = 合规）。

        Args:
            nc_text: NC 程序全文
            tier: "strict"（标准程序全规则）或 "structural"（扩展序列，
                仅结构 + 字地址语法，不做代码白名单判定）
        """
        if tier not in ("strict", "structural"):
            raise ValueError(f"tier 必须是 strict/structural: {tier}")
        lines = nc_text.splitlines()
        if not any(line.strip() for line in lines):
            return [ComplianceIssue(0, "", "empty", "空程序")]

        checker = getattr(self, f"_check_{self.profile.family}")
        return checker(lines, tier)

    # ── ISO 字地址族 ──────────────────────────────────────────

    def _check_iso(self, lines: list[str], tier: str) -> list[ComplianceIssue]:
        issues: list[ComplianceIssue] = []
        p = self.profile
        stripped = [re.sub(r"\([^)]*\)", " ", ln) if p.comment_paren else ln for ln in lines]
        if p.comment_semicolon:
            stripped = [ln.split(";", 1)[0] for ln in stripped]

        code_lines = [ln.strip() for ln in stripped if ln.strip()]
        non_percent = [ln for ln in code_lines if ln != "%"]

        # 1. 结构：% 包裹 + 程序号 + M30/M02 结尾
        if p.fagor_header:
            # Fagor 形式：首行 %01000 即程序头，无结尾 %
            if not code_lines or not re.match(r"^%\d{3,6}$", code_lines[0]):
                issues.append(ComplianceIssue(1, lines[0], "structure", "Fagor 程序应以 %<程序号> 开头（如 %01000）"))
        elif p.program_wrapper:
            if not code_lines or code_lines[0] != "%":
                issues.append(ComplianceIssue(1, lines[0], "structure", "ISO 程序应以 % 开头"))
            if not code_lines or code_lines[-1] != "%":
                issues.append(ComplianceIssue(len(lines), lines[-1], "structure", "ISO 程序应以 % 结尾"))
        if p.o_number_required and not any(re.match(r"^O\d{3,6}\b", ln) for ln in non_percent):
            issues.append(ComplianceIssue(0, "", "structure", "缺少 O 程序号头（O####）"))
        if not any(re.search(r"\bM30\b|\bM02\b", ln) for ln in non_percent):
            issues.append(ComplianceIssue(0, "", "structure", "缺少程序结束指令 M30/M02"))

        modal_feed: float | None = None
        for idx, raw in enumerate(lines, start=1):
            code = stripped[idx - 1].strip()
            if not code or code == "%":
                continue
            # 整行模式白名单（子程序调用等非字地址行，如 Fagor CALL Pxxxx / RET）
            if any(re.match(pat, code) for pat in self.profile.extra_line_patterns):
                continue

            # 2. 大写约定
            if code != code.upper():
                issues.append(ComplianceIssue(idx, raw, "case", "ISO 代码应为大写"))

            # 3. 字地址词法：字母必须合法、必须带数值、数值必须可解析
            for tok in _WORD_RE.findall(code):
                letter = tok[0].upper()
                if letter not in p.allowed_letters:
                    issues.append(ComplianceIssue(idx, raw, "illegal_word", f"非法字地址字母: {tok}"))
                num_part = tok[1:]
                if num_part == "":
                    issues.append(ComplianceIssue(idx, raw, "illegal_word", f"字地址缺少数值: {tok}"))
                    continue
                if num_part not in ("+", "-"):
                    try:
                        value = float(num_part)
                    except ValueError:
                        issues.append(ComplianceIssue(idx, raw, "illegal_word", f"字地址数值非法: {tok}"))
                        continue
                    decimals = len(num_part.split(".")[1]) if "." in num_part else 0
                    if decimals > _MAX_DECIMALS:
                        issues.append(ComplianceIssue(idx, raw, "precision", f"{tok} 小数位超过 {_MAX_DECIMALS}"))
                    if letter == "F":
                        if value <= 0:
                            issues.append(ComplianceIssue(idx, raw, "feed", f"进给值必须为正: {tok}"))
                        modal_feed = value

            # 4. 代码白名单（仅 strict 级）
            if tier == "strict":
                for tok in _WORD_RE.findall(code):
                    gm = _G_RE.match(tok) or _M_RE.match(tok)
                    if not gm:
                        continue
                    prefix = "G" if _G_RE.match(tok) else "M"
                    canonical = _norm_gm(prefix, gm.group(1))
                    whitelist = self.profile.g_whitelist if prefix == "G" else self.profile.m_whitelist
                    normalized = {_norm_gm(c[0], c[1:]) for c in whitelist}
                    if canonical not in normalized:
                        issues.append(ComplianceIssue(idx, raw, "unknown_gcode", f"{canonical} 不在该控制器代码白名单"))

            # 5. 模态进给：切削指令要求模态 F 已建立
            if re.search(r"\bG0?[123]\b", code) and not re.search(r"\bG9[01]\b", code):
                if "F" not in code.upper() and modal_feed is None:
                    issues.append(ComplianceIssue(idx, raw, "modal_feed", "G01/G02/G03 前无模态进给 F"))

        return issues

    # ── Heidenhain 对话式 ─────────────────────────────────────

    _HEID_LINE_RE = re.compile(
        r"^\s*(?:\d+\s+)?(L |CC |C |CYCL DEF|CYCL CALL|TOOL CALL|BLK FORM|FN |LBL |M\d+|Q\d+\s*=|S\d+\s+M0?3|$)"
    )

    def _check_heidenhain(self, lines: list[str], tier: str) -> list[ComplianceIssue]:
        issues: list[ComplianceIssue] = []
        non_empty = [ln for ln in lines if ln.strip()]

        # 1. 结构：BEGIN/END PGM
        if not non_empty or not re.match(r"^(\d+\s+)?BEGIN PGM \d+ MM$", non_empty[0].strip()):
            issues.append(ComplianceIssue(1, lines[0] if lines else "", "structure", "应以 'BEGIN PGM <n> MM' 开头"))
        if not non_empty or not re.match(r"^(\d+\s+)?END PGM \d+ MM$", non_empty[-1].strip()):
            issues.append(
                ComplianceIssue(len(lines), lines[-1] if lines else "", "structure", "应以 'END PGM <n> MM' 结尾")
            )

        for idx, raw in enumerate(lines, start=1):
            line = _HEIDENHAIN_BLOCK_RE.sub("", raw).strip()
            if not line:
                continue
            # 注释行 ; ...
            if line.startswith(";"):
                continue

            # 2. 对话式程序不得混用字地址 G 代码（真机会拒绝）
            for tok in _WORD_RE.findall(line):
                if _G_RE.match(tok):
                    issues.append(
                        ComplianceIssue(
                            idx,
                            raw,
                            "gcode_in_dialog",
                            f"Heidenhain 对话式程序出现字地址 {tok}（G 代码属 ISO 模式，与 BEGIN PGM 程序头不兼容）",
                        )
                    )

            # 3. 行语法白名单（结构级，两级都查）
            if not self._HEID_LINE_RE.match(line) and not line.startswith(("BEGIN PGM", "END PGM", "TCH PROBE", "FN")):
                issues.append(ComplianceIssue(idx, raw, "heidenhain_syntax", f"不符合对话式行语法: {line[:40]}"))

            # 4. L 行坐标应带符号、小数 ≤ 3（strict 级）
            if tier == "strict" and re.match(r"^L ", line):
                for coord in re.findall(r"[XYZ]([+\-]?\d+\.?\d*)", line):
                    if "." in coord and len(coord.split(".")[1]) > 3:
                        issues.append(ComplianceIssue(idx, raw, "precision", f"坐标 {coord} 小数位超过 3"))
                if "F" not in line:
                    issues.append(ComplianceIssue(idx, raw, "heidenhain_syntax", "L 行缺少 F/FMAX 进给"))
        return issues

    # ── Siemens 840D ──────────────────────────────────────────

    _SIEMENS_G_SET = {
        "G0",
        "G1",
        "G2",
        "G3",
        "G4",
        "G17",
        "G18",
        "G19",
        "G40",
        "G41",
        "G42",
        "G43.4",
        "G49",
        "G53",
        "G54",
        "G55",
        "G56",
        "G57",
        "G58",
        "G59",
        "G70",
        "G71",
        "G90",
        "G91",
        "G94",
        "G95",
    }
    _SIEMENS_M_SET = {"M00", "M01", "M02", "M03", "M04", "M05", "M06", "M08", "M09", "M17", "M30"}
    _SIEMENS_LINE_RE = re.compile(
        r"^(?:N\d+)?\s*("
        r"G\d+(\.\d+)?|X[+\-0-9.]|Y[+\-0-9.]|Z[+\-0-9.]|A[+\-0-9.]|C[+\-0-9.]"
        r"|F\d|S\d|T\d|T=|M\d+|D\d+|CYCLE\d+|;\$?|\$TC_|G40|G41|G42|DISC\d|CR=|TRAFOF"
        r"|N\d+)"
    )

    def _check_siemens(self, lines: list[str], tier: str) -> list[ComplianceIssue]:
        issues: list[ComplianceIssue] = []
        code_lines = [ln.split(";", 1)[0].strip() for ln in lines]
        effective = [ln for ln in code_lines if ln]

        # 1. 结构：M30/M17 结尾；不接受 % 包裹（declared 方言的 %_N_ 头除外）
        if effective and effective[-1] == "%":
            issues.append(ComplianceIssue(len(lines), lines[-1], "structure", "Sinumerik 程序不以 % 结尾"))
        if not any(re.search(r"\bM30\b|\bM17\b", ln) for ln in effective):
            issues.append(ComplianceIssue(0, "", "structure", "缺少程序结束指令 M30/M17"))

        seen_feed = False
        for idx, raw in enumerate(lines, start=1):
            code = raw.split(";", 1)[0].strip()
            if not code:
                continue

            if code.startswith("$TC_"):
                if not re.match(r"^\$TC_[A-Z0-9_]+\[[0-9,]+\]=[0-9.\-]+$", code):
                    issues.append(ComplianceIssue(idx, raw, "siemens_syntax", "系统变量赋值格式非法"))
                continue
            if code.startswith("%_N_"):
                continue

            # CYCLExx(...) 调用行
            if re.match(r"^(?:N\d+\s+)?CYCLE\d+\(", code):
                continue
            if re.match(r"^(?:N\d+\s+)?CYCLE\d+$", code):
                continue
            # T="TOOLNAME" 字符串刀具名
            if re.match(r'^(?:N\d+\s+)?T="?[^"]+"?(\s+M06)?$', code):
                continue

            for tok in _WORD_RE.findall(code):
                letter = tok[0].upper()
                if letter not in self.profile.allowed_letters:
                    issues.append(ComplianceIssue(idx, raw, "illegal_word", f"非法字地址字母: {tok}"))
                num_part = tok[1:]
                if num_part and num_part not in ("+", "-"):
                    try:
                        float(num_part)
                    except ValueError:
                        issues.append(ComplianceIssue(idx, raw, "illegal_word", f"字地址数值非法: {tok}"))
                if letter == "F":
                    seen_feed = True

            if tier == "strict":
                for tok in _WORD_RE.findall(code):
                    gm = _G_RE.match(tok) or _M_RE.match(tok)
                    if not gm:
                        continue
                    prefix = "G" if _G_RE.match(tok) else "M"
                    canonical = prefix + gm.group(1)
                    whitelist = self._SIEMENS_G_SET if prefix == "G" else self._SIEMENS_M_SET
                    if canonical not in whitelist and str(int(float(gm.group(1)))) not in {c[1:] for c in whitelist}:
                        issues.append(ComplianceIssue(idx, raw, "unknown_gcode", f"{canonical} 不在 Sinumerik 白名单"))

            if not self._SIEMENS_LINE_RE.match(code):
                issues.append(ComplianceIssue(idx, raw, "siemens_syntax", f"不符合 Sinumerik 行语法: {code[:40]}"))

        if not seen_feed and tier == "strict":
            issues.append(ComplianceIssue(0, "", "modal_feed", "程序中无任何进给 F 定义"))
        return issues


def list_supported_controllers() -> list[str]:
    """返回支持的控制器 id（与 golden 覆盖对齐）。"""
    return sorted(_PROFILES)
