"""统一多层安全校验器（借鉴 NumCraft 的 SafetyValidator 思路）。

在 G 代码导出前对「颤振预测特征 × 工艺参数 × G 代码文本」做多层安全门禁：

- L1 参数限界：主轴转速 / 进给在机床能力范围内（越界给出 clamp 建议值）
- L2 轴行程软限位：safe_z / stock_top_z 等关键 Z 坐标不超程
- L3 切削物理约束：实际切深 ≤ 极限切深、安全裕度 ≥ 0.8、切削力系数有效
- L4 刀具完整性：刀具 / 机床字段存在、关键参数有限
- L5 控制器语法合规：G/M 代码白名单（未知代码告警）
- L6 程序结构完整性：程序号 / M30 结束 / 负进给拦截

策略：error 级 → 阻断导出（强制回上游调整）；warning 级 → 放行并随任务上报，
交由工程师审核环节处理。与「CAM 二次校验（人工）」构成自动 + 人工双保险。
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.gcode_generation.gcode_store import SAFETY_MARGIN_RATIO

logger = logging.getLogger(__name__)

# 默认机床能力配置（与 postprocessor ConfigLimiter 默认值一致）
DEFAULT_MACHINE_CONFIG: dict[str, Any] = {
    "spindle": {"min_rpm": 50, "max_rpm": 24000},
    "feed": {"min_rate": 10.0, "max_rate": 20000.0},
    "axis_limits": {
        "enabled": True,
        "x_min": -1000.0,
        "x_max": 1000.0,
        "y_min": -1000.0,
        "y_max": 1000.0,
        "z_min": -500.0,
        "z_max": 500.0,
    },
}

# G/M 代码白名单（按控制器族；未知代码仅告警不阻断）
_BASE_G_CODES = {
    0,
    1,
    2,
    3,
    4,
    10,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    28,
    40,
    41,
    42,
    43,
    49,
    52,
    53,
    54,
    55,
    56,
    57,
    58,
    59,
    73,
    76,
    80,
    81,
    82,
    83,
    84,
    85,
    86,
    87,
    88,
    89,
    90,
    91,
    94,
    95,
    98,
    99,
}
_BASE_M_CODES = {
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    13,
    17,
    18,
    19,
    20,
    21,
    22,
    30,
    98,
    99,
}
# 西门子 / 海德汉等循环指令关键词（不作为未知代码告警）
_CYCLE_PATTERNS = (
    re.compile(r"CYCLE\d{2}", re.IGNORECASE),
    re.compile(r"CYCL DEF", re.IGNORECASE),
)

# 错误码
ERR_SPINDLE_OUT_OF_RANGE = "SPINDLE_OUT_OF_RANGE"
ERR_FEED_OUT_OF_RANGE = "FEED_OUT_OF_RANGE"
ERR_INVALID_DEPTH = "INVALID_DEPTH"
ERR_CUTTING_DEPTH_EXCEEDS_LIMIT = "CUTTING_DEPTH_EXCEEDS_LIMIT"
ERR_AXIS_TRAVEL_EXCEEDED = "AXIS_TRAVEL_EXCEEDED"
ERR_EMPTY_PROGRAM = "EMPTY_PROGRAM"
ERR_NO_PROGRAM_END = "NO_PROGRAM_END"
ERR_NEGATIVE_FEED = "NEGATIVE_FEED"
WARN_SAFETY_MARGIN_INSUFFICIENT = "SAFETY_MARGIN_INSUFFICIENT"
WARN_MISSING_LIMIT_DEPTH = "MISSING_LIMIT_DEPTH"
WARN_UNSTABLE_FEATURE = "UNSTABLE_FEATURE"
WARN_MISSING_TOOL = "MISSING_TOOL"
WARN_INVALID_CUTTING_FORCE_COEFF = "INVALID_CUTTING_FORCE_COEFF"
WARN_UNKNOWN_G_M_CODE = "UNKNOWN_G_M_CODE"
WARN_ZERO_FEED = "ZERO_FEED"
WARN_NO_PROGRAM_NUMBER = "NO_PROGRAM_NUMBER"
WARN_NON_FINITE_PARAM = "NON_FINITE_PARAM"


@dataclass
class SafetyIssue:
    """单条安全校验问题。"""

    code: str
    severity: str  # "error" | "warning"
    message: str
    context: dict[str, Any] = field(default_factory=dict)
    recommended: float | None = None  # clamp 建议值

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "context": self.context,
            "recommended": self.recommended,
        }


@dataclass
class SafetyReport:
    """安全校验汇总报告。"""

    issues: list[SafetyIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[SafetyIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[SafetyIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def error_codes(self) -> list[str]:
        return [i.code for i in self.errors]

    @property
    def warning_codes(self) -> list[str]:
        return [i.code for i in self.warnings]

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "error_codes": self.error_codes,
            "warning_codes": self.warning_codes,
            "issues": [i.to_dict() for i in self.issues],
        }

    def summary(self) -> str:
        if self.is_valid:
            return f"安全校验通过（warnings={len(self.warnings)}）"
        return f"安全校验未通过: {self.error_codes}"


class SafetyValidationError(Exception):
    """存在 error 级安全问题时抛出。"""

    def __init__(self, report: SafetyReport) -> None:
        self.report = report
        super().__init__(report.summary())


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


# 校验器
class SafetyValidator:
    """统一多层安全校验器。

    Args:
        machine_config: 机床能力配置（缺省用 DEFAULT_MACHINE_CONFIG）。
        controller_type: 默认控制器（fanuc_0i / siemens_840d / heidenhain_tnc ...）。
    """

    def __init__(
        self,
        machine_config: dict[str, Any] | None = None,
        controller_type: str = "fanuc_0i",
    ) -> None:
        cfg = machine_config or DEFAULT_MACHINE_CONFIG
        spindle = cfg.get("spindle", {})
        feed = cfg.get("feed", {})
        axis = cfg.get("axis_limits", {})
        self._spindle_min = float(spindle.get("min_rpm", 50))
        self._spindle_max = float(spindle.get("max_rpm", 24000))
        self._feed_min = float(feed.get("min_rate", 10.0))
        self._feed_max = float(feed.get("max_rate", 20000.0))
        self._axis_enabled = bool(axis.get("enabled", True))
        self._z_min = float(axis.get("z_min", -500.0))
        self._z_max = float(axis.get("z_max", 500.0))
        self._x_min = float(axis.get("x_min", -1000.0))
        self._x_max = float(axis.get("x_max", 1000.0))
        self._y_min = float(axis.get("y_min", -1000.0))
        self._y_max = float(axis.get("y_max", 1000.0))
        self._controller_type = controller_type

    # L1 + L3 + L4：单特征参数校验
    def validate_feature(self, feat: Any) -> list[SafetyIssue]:
        """对单个颤振预测特征执行参数级安全校验。"""
        issues: list[SafetyIssue] = []
        fid = getattr(feat, "feature_id", "?")

        # L1 主轴转速
        rpm = getattr(feat, "spindle_rpm", None)
        if rpm is None or not _finite(rpm):
            issues.append(
                SafetyIssue(WARN_NON_FINITE_PARAM, "warning", f"特征 {fid} 主轴转速缺失或非有限值", {"feature_id": fid})
            )
        elif rpm < self._spindle_min or rpm > self._spindle_max:
            clamped = min(max(rpm, self._spindle_min), self._spindle_max)
            issues.append(
                SafetyIssue(
                    ERR_SPINDLE_OUT_OF_RANGE,
                    "error",
                    f"特征 {fid} 主轴转速 {rpm:.1f} RPM 超出机床能力 "
                    f"[{self._spindle_min:.0f}, {self._spindle_max:.0f}]，建议 clamp 至 {clamped:.0f} RPM",
                    {"feature_id": fid, "spindle_rpm": rpm, "min": self._spindle_min, "max": self._spindle_max},
                    recommended=clamped,
                )
            )

        # L3 切深有效性
        axial = getattr(feat, "axial_depth_mm", None)
        limit = getattr(feat, "limit_depth_mm", None)
        if axial is None or not _finite(axial) or axial <= 0:
            issues.append(
                SafetyIssue(
                    ERR_INVALID_DEPTH,
                    "error",
                    f"特征 {fid} 实际切深 {axial!r} 非法（必须为有限正数）",
                    {"feature_id": fid, "axial_depth_mm": axial},
                )
            )
        else:
            # 切深超过极限切深 error（进入不稳定区）
            if limit is not None and _finite(limit) and limit > 0 and axial > limit:
                issues.append(
                    SafetyIssue(
                        ERR_CUTTING_DEPTH_EXCEEDS_LIMIT,
                        "error",
                        f"特征 {fid} 实际切深 {axial:.3f}mm 超过极限切深 {limit:.3f}mm，处于颤振不稳定区，禁止导出",
                        {"feature_id": fid, "axial_depth_mm": axial, "limit_depth_mm": limit},
                    )
                )
            # 安全裕度不足（> 0.8 × limit） warning（与阶段 6 既有逻辑一致）
            elif limit is not None and _finite(limit) and limit > 0 and axial > SAFETY_MARGIN_RATIO * limit:
                issues.append(
                    SafetyIssue(
                        WARN_SAFETY_MARGIN_INSUFFICIENT,
                        "warning",
                        f"特征 {fid} 安全裕度不足：切深 {axial:.3f}mm > 极限切深 × {SAFETY_MARGIN_RATIO} = {limit * SAFETY_MARGIN_RATIO:.3f}mm",
                        {"feature_id": fid, "axial_depth_mm": axial, "limit_depth_mm": limit},
                    )
                )
            elif limit is None or not _finite(limit) or limit <= 0:
                issues.append(
                    SafetyIssue(
                        WARN_MISSING_LIMIT_DEPTH,
                        "warning",
                        f"特征 {fid} 缺少有效的极限切深（limit_depth_mm={limit!r}），无法评估安全裕度",
                        {"feature_id": fid, "limit_depth_mm": limit},
                    )
                )

        # L3 颤振稳定性（stable=False 理论上已被上游拦截，此处兜底告警）
        if getattr(feat, "stable", True) is False:
            issues.append(
                SafetyIssue(
                    WARN_UNSTABLE_FEATURE,
                    "warning",
                    f"特征 {fid} 颤振预测为不稳定（兜底告警，正常流程应已在阶段 5/6 拦截）",
                    {"feature_id": fid},
                )
            )

        # L4 刀具完整性
        if not getattr(feat, "tool_id", ""):
            issues.append(
                SafetyIssue(WARN_MISSING_TOOL, "warning", f"特征 {fid} 未指定刀具（tool_id 为空）", {"feature_id": fid})
            )

        # L4 切削力系数
        ks = getattr(feat, "cutting_force_coeff", 0.0)
        if ks is not None and _finite(ks) and ks < 0:
            issues.append(
                SafetyIssue(
                    WARN_INVALID_CUTTING_FORCE_COEFF,
                    "warning",
                    f"特征 {fid} 切削力系数为负（K_s={ks}），物理异常",
                    {"feature_id": fid, "cutting_force_coeff": ks},
                )
            )

        return issues

    def validate_features(self, feats: list[Any]) -> SafetyReport:
        """批量校验特征列表。"""
        report = SafetyReport()
        for feat in feats:
            report.issues.extend(self.validate_feature(feat))
        return report

    # L2：关键 Z 坐标软限位
    def _validate_axis(self, axis: str, position: float | None, label: str) -> list[SafetyIssue]:
        if not self._axis_enabled or position is None or not _finite(position):
            return []
        limits = {
            "X": (self._x_min, self._x_max),
            "Y": (self._y_min, self._y_max),
            "Z": (self._z_min, self._z_max),
        }
        key = axis.upper()
        if key not in limits:
            # 未知轴无软限位定义，跳过
            return []
        lo, hi = limits[key]
        if position < lo or position > hi:
            clamped = min(max(position, lo), hi)
            return [
                SafetyIssue(
                    ERR_AXIS_TRAVEL_EXCEEDED,
                    "error",
                    f"{label} {axis} 坐标 {position:.3f}mm 超出软限位 [{lo}, {hi}]，建议 clamp 至 {clamped:.3f}mm",
                    {"label": label, "axis": axis, "position": position, "min": lo, "max": hi},
                    recommended=clamped,
                )
            ]
        return []

    # L5 + L6：G 代码文本校验
    def validate_gcode_text(self, gcode_text: str, controller_type: str | None = None) -> SafetyReport:
        """对 G 代码文本做语法合规（L5）+ 结构完整性（L6）校验。"""
        report = SafetyReport()
        text = gcode_text or ""
        lines = [ln.strip() for ln in text.split("\n") if ln.strip() and not ln.strip().startswith(";")]
        controller = (controller_type or self._controller_type or "").lower()

        # L6 空程序
        if not lines:
            report.issues.append(SafetyIssue(ERR_EMPTY_PROGRAM, "error", "G 代码程序为空"))
            return report

        # L6 程序结束
        if not any(re.search(r"\bM(30|02)\b", ln, re.IGNORECASE) for ln in lines):
            report.issues.append(SafetyIssue(ERR_NO_PROGRAM_END, "error", "G 代码缺少程序结束指令（M30/M02）"))

        # L6 程序号（fanuc 系：O 号；heidenhain 不要求）
        if controller.startswith("fanuc") or "siemens" in controller:
            if not any(re.match(r"^\s*O\d{4,5}\b", ln) for ln in lines):
                report.issues.append(
                    SafetyIssue(
                        WARN_NO_PROGRAM_NUMBER, "warning", "未检测到程序号（O 号），Fanuc/Siemens 程序建议带程序号"
                    )
                )

        # L6 负进给 / 零进给 / 进给范围（FEED_OUT_OF_RANGE）
        # 模态感知：G95（每转进给）下 F 值为 mm/r（合法值可远小于 min_rate），
        # 范围校验仅在 G94（每分钟进给，默认）模态生效，避免误报。
        feed_mode_per_rev = False
        for i, ln in enumerate(lines, start=1):
            if re.search(r"\bG95\b", ln, re.IGNORECASE):
                feed_mode_per_rev = True
            elif re.search(r"\bG94\b", ln, re.IGNORECASE):
                feed_mode_per_rev = False
            m = re.search(r"F\s*(-?\d+(?:\.\d+)?)", ln, re.IGNORECASE)
            if m:
                feed = float(m.group(1))
                if feed < 0:
                    report.issues.append(
                        SafetyIssue(ERR_NEGATIVE_FEED, "error", f"第 {i} 行存在负进给 F{feed:g}：物理非法", {"line": i})
                    )
                elif feed == 0:
                    report.issues.append(SafetyIssue(WARN_ZERO_FEED, "warning", f"第 {i} 行进给为 0", {"line": i}))
                elif not feed_mode_per_rev and (feed < self._feed_min or feed > self._feed_max):
                    # L1 文本级进给限界（2026-09 接线，此前 ERR_FEED_OUT_OF_RANGE
                    # 从未产出）：越界为 error 并附 clamp 建议值，供修复闭环使用
                    clamped = min(max(feed, self._feed_min), self._feed_max)
                    report.issues.append(
                        SafetyIssue(
                            ERR_FEED_OUT_OF_RANGE,
                            "error",
                            f"第 {i} 行进给 F{feed:g} 超出机床能力范围 "
                            f"[{self._feed_min:g}, {self._feed_max:g}]，建议 clamp 至 F{clamped:g}",
                            {"line": i, "feed": feed, "min": self._feed_min, "max": self._feed_max},
                            recommended=clamped,
                        )
                    )

        # L5 G/M 代码白名单
        for i, ln in enumerate(lines, start=1):
            for token in re.findall(r"\b[GM]\d{1,3}\b", ln, re.IGNORECASE):
                code = token.upper()
                num = int(code[1:])
                if code.startswith("G") and num not in _BASE_G_CODES:
                    report.issues.append(
                        SafetyIssue(
                            WARN_UNKNOWN_G_M_CODE,
                            "warning",
                            f"第 {i} 行未知 G 代码 {code}（不在白名单，请人工确认控制器兼容性）",
                            {"line": i, "code": code},
                        )
                    )
                elif code.startswith("M") and num not in _BASE_M_CODES:
                    report.issues.append(
                        SafetyIssue(
                            WARN_UNKNOWN_G_M_CODE,
                            "warning",
                            f"第 {i} 行未知 M 代码 {code}（不在白名单，请人工确认控制器兼容性）",
                            {"line": i, "code": code},
                        )
                    )

        return report

    # 汇总入口
    def validate_all(
        self,
        chatter_results: list[Any],
        gcode_text: str = "",
        safe_z: float | None = None,
        stock_top_z: float | None = None,
        controller_type: str | None = None,
    ) -> SafetyReport:
        """全量校验：特征参数 + Z 坐标软限位 + G 代码文本。"""
        report = self.validate_features(chatter_results)
        report.issues.extend(self._validate_axis("Z", safe_z, "safe_z"))
        report.issues.extend(self._validate_axis("Z", stock_top_z, "stock_top_z"))
        if gcode_text:
            gcode_report = self.validate_gcode_text(gcode_text, controller_type)
            report.issues.extend(gcode_report.issues)
        return report

    # clamp 建议
    def get_clamped_parameters(self, feat: Any) -> dict[str, float]:
        """返回该特征越界参数的 clamp 建议值（未越界返回原值）。"""
        rpm = getattr(feat, "spindle_rpm", 0.0) or 0.0
        clamped_rpm = min(max(rpm, self._spindle_min), self._spindle_max)
        return {"spindle_rpm": float(clamped_rpm)}


def raise_if_invalid(report: SafetyReport) -> None:
    """report 存在 error 级问题时抛出 SafetyValidationError。"""
    if report is not None and not report.is_valid:
        raise SafetyValidationError(report)


__all__ = [
    "SafetyIssue",
    "SafetyReport",
    "SafetyValidator",
    "SafetyValidationError",
    "raise_if_invalid",
    "DEFAULT_MACHINE_CONFIG",
]
