"""
制造安全约束规则库

定义机床、刀具、工艺、物理安全四级安全约束规则，支持YAML配置加载、规则验证、
条件评估和动作执行。四级优先级：P0（人员安全）> P1（设备安全）>
P2（产品质量）> P3（效率优化）。

规则分类:
- M系列: 机床安全约束（速度、温度、振动、进给限制）
- T系列: 刀具安全约束（磨损、断刀、寿命）
- P系列: 工艺约束（切削参数、公差、过切检测）
- S系列: 物理安全约束（急停、防护门、光幕、操作员在场、双手按钮、安全垫）
        [F-P0-3 新增]

物理安全信号依据:
- IEC 62443-3-3 SR 7.2（可用性 / 安全功能完整性）
- ISO 10218（工业机器人安全要求）
- ISO 13849-1（机械安全控制系统相关部件）

防复发约束:
- S系列规则 priority 必须为 P0，不可由 AI 优化降级
- e_stop / hold 动作必须由人工复位，不可自动恢复
- known_fields 需与 config/safety_rules.yaml 的 known_fields 声明保持同步
- 新增 action type 必须同时在 VALID_ACTION_TYPES 和 YAML action_types 中声明
"""

from __future__ import annotations

import ast
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# 模块级缓存：避免对相同字符串重复扫描/编译，提升性能
_SAFE_EXPR_CACHE: dict[str, "SafeMathEvaluator"] = {}
_SAFE_EXPR_CACHE_MAX = 512

# 枚举定义


class Priority(str, Enum):
    """安全规则优先级 - 数值越小优先级越高"""

    P0 = "P0"  # 最高：人员安全，任何情况下不可被覆盖
    P1 = "P1"  # 设备安全：机床/刀具保护
    P2 = "P2"  # 产品质量：公差/表面质量
    P3 = "P3"  # 效率优化：可被LNN建议覆盖

    @property
    def level(self) -> int:
        _map = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        return _map[self.value]

    @classmethod
    def from_string(cls, s: str) -> "Priority":
        try:
            return cls(s.upper())
        except ValueError:
            raise ValueError(f"无效的优先级: '{s}'，仅支持 P0/P1/P2/P3")


class ActionType(str, Enum):
    """动作类型"""

    OVERRIDE = "override"  # 强制覆盖目标参数值
    ALERT = "alert"  # 告警
    ALERT_AND_OVERRIDE = "alert_and_override"  # 告警+覆盖
    STOP = "stop"  # 立即停机
    PAUSE_AND_ALERT = "pause_and_alert"  # 暂停+报警
    FORCE_CHANGE = "force_change"  # 强制更换（如换刀）
    E_STOP = "e_stop"  # 急停（物理安全回路联锁）[F-P0-3]
    HOLD = "hold"  # 保持当前状态等待人工干预 [F-P0-3]


class RuleCategory(str, Enum):
    """规则类别"""

    MACHINE = "M"  # 机床
    TOOL = "T"  # 刀具
    PROCESS = "P"  # 工艺
    SAFETY = "S"  # 物理安全 [F-P0-3]


VALID_OPERATORS = {"<", ">", "<=", ">=", "==", "!="}

ACTION_TYPE_MAP = {
    "强制降速": ActionType.OVERRIDE,
    "告警+降速": ActionType.ALERT_AND_OVERRIDE,
    "停机检查": ActionType.STOP,
    "限制到最大值": ActionType.OVERRIDE,
    "强制换刀": ActionType.FORCE_CHANGE,
    "立即停机": ActionType.STOP,
    "降速+减小切深": ActionType.ALERT_AND_OVERRIDE,
    "暂停+报警": ActionType.PAUSE_AND_ALERT,
}

PRIORITY_ORDER: list[Priority] = [Priority.P0, Priority.P1, Priority.P2, Priority.P3]

# 数据模型


@dataclass
class RuleCondition:
    """规则触发条件"""

    condition_type: str  # threshold, composite
    field: str
    operator: str
    value: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.condition_type,
            "field": self.field,
            "operator": self.operator,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RuleCondition":
        return cls(
            condition_type=d.get("type", "threshold"),
            field=d["field"],
            operator=d.get("operator", ">"),
            value=d.get("value"),
        )


@dataclass
class RuleAction:
    """规则执行动作"""

    action_type: ActionType
    target: str
    value: Any
    duration: str = "until_condition_cleared"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.action_type.value,
            "target": self.target,
            "value": self.value,
            "duration": self.duration,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any], action_desc: str = "") -> "RuleAction":
        raw_type = d.get("type", "")
        atype = cls._resolve_action_type(raw_type, action_desc)
        return cls(
            action_type=atype,
            target=d.get("target", ""),
            value=d.get("value"),
            duration=d.get("duration", "until_condition_cleared"),
        )

    @staticmethod
    def _resolve_action_type(raw: str, desc: str) -> ActionType:
        try:
            return ActionType(raw)
        except ValueError as action_err:
            # 解析失败时回退到 desc 映射或默认 ALERT
            logger.debug(
                "Failed to resolve ActionType from raw %r, fallback to desc/default: %s",
                raw,
                action_err,
                exc_info=True,
            )
        if desc in ACTION_TYPE_MAP:
            return ACTION_TYPE_MAP[desc]
        return ActionType.ALERT


@dataclass
class SafetyRule:
    """单条制造安全约束规则"""

    rule_id: str  # M-001, T-001, P-001 ...
    name: str  # 规则名称
    priority: Priority  # P0-P3
    category: RuleCategory  # M/T/P
    condition: RuleCondition  # 触发条件
    action: RuleAction  # 执行动作
    audit: bool = True  # 是否记录审计日志

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "priority": self.priority.value,
            "category": self.category.value,
            "condition": self.condition.to_dict(),
            "action": self.action.to_dict(),
            "audit": self.audit,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SafetyRule":
        raw_action_desc = d.get("action_description", "")
        return cls(
            rule_id=d["rule_id"],
            name=d["name"],
            priority=Priority.from_string(d.get("priority", "P3")),
            category=RuleCategory(d.get("category", d["rule_id"][0])),
            condition=RuleCondition.from_dict(d["condition"]),
            action=RuleAction.from_dict(d["action"], raw_action_desc),
            audit=d.get("audit", True),
        )


@dataclass
class AuditEntry:
    """审计日志条目"""

    timestamp: float  # Unix时间戳
    rule_id: str  # 触发规则ID
    condition_values: dict[str, Any]  # 触发时的条件值
    action: dict[str, Any]  # 执行的动作
    result: str  # 执行结果 description
    priority: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "rule_id": self.rule_id,
            "priority": self.priority,
            "condition_values": self.condition_values,
            "action": self.action,
            "result": self.result,
        }


# 规则验证


@dataclass
class ValidationError:
    """验证错误"""

    rule_id: str
    field: str
    message: str


def validate_rules(rules: list[SafetyRule]) -> list[ValidationError]:
    """
    完整规则验证，包含:
    - 语法检查（rule_id格式、必填字段）
    - 条件字段存在性检查
    - 动作类型合法性检查 [F-P0-3]
    - 优先级循环依赖检查
    - 动作目标有效性检查
    """
    errors: list[ValidationError] = []
    if not rules:
        errors.append(ValidationError("", "rules", "规则列表为空"))
        return errors

    errors.extend(_check_syntax(rules))
    errors.extend(_check_field_existence(rules))
    errors.extend(_check_action_type(rules))
    errors.extend(_check_priority_dependency(rules))
    errors.extend(_check_action_target_validity(rules))
    return errors


def _check_syntax(rules: list[SafetyRule]) -> list[ValidationError]:
    """语法检查: rule_id格式、必填字段"""
    errors = []
    seen_ids: set = set()
    for rule in rules:
        # rule_id格式: 大写字母-三位数字
        if not rule.rule_id:
            errors.append(ValidationError("", "rule_id", "rule_id不能为空"))
        elif not _is_valid_rule_id(rule.rule_id):
            errors.append(
                ValidationError(rule.rule_id, "rule_id", f"rule_id格式无效: '{rule.rule_id}'，应为 大写字母-三位数字")
            )
        else:
            if rule.rule_id in seen_ids:
                errors.append(ValidationError(rule.rule_id, "rule_id", f"rule_id重复: {rule.rule_id}"))
            seen_ids.add(rule.rule_id)

        if not rule.name:
            errors.append(ValidationError(rule.rule_id, "name", "规则名称不能为空"))

        if rule.condition.field is None:
            errors.append(ValidationError(rule.rule_id, "condition.field", "条件字段不能为空"))

        if rule.condition.operator not in VALID_OPERATORS:
            errors.append(
                ValidationError(
                    rule.rule_id,
                    "condition.operator",
                    f"无效运算符: '{rule.condition.operator}'，支持: {VALID_OPERATORS}",
                )
            )

        if not rule.action.target:
            errors.append(ValidationError(rule.rule_id, "action.target", "动作目标不能为空"))

    return errors


def _is_valid_rule_id(rule_id: str) -> bool:
    """检查规则ID格式: 大写字母-三位数字"""
    return bool(re.match(r"^[A-Z]-\d{3}$", rule_id))


def _check_field_existence(rules: list[SafetyRule]) -> list[ValidationError]:
    """条件字段存在性检查 - 确保condition.field引用的是已知的传感器/参数域"""
    errors = []

    known_fields = {
        # 机床状态
        "spindle_speed",
        "spindle_temperature",
        "vibration_rms",
        "feed_rate",
        "max_feed_rate",
        "max_spindle_speed",
        # 刀具状态
        "tool_wear",
        "acoustic_emission",
        "tool_life_used",
        "tool_rated_life",
        "cutting_force",
        "material_force_limit",
        "dimension_deviation",
        "tolerance_band",
        "overcut_detected",
        # 物理安全信号 [F-P0-3] - IEC 62443 / ISO 10218 合规
        "emergency_stop_active",
        "guard_door_open",
        "light_curtain_broken",
        "operator_present",
        "two_hand_button_engaged",
        "safety_mat_occupied",
    }

    for rule in rules:
        fld = rule.condition.field
        if fld and fld not in known_fields:
            errors.append(
                ValidationError(
                    rule.rule_id, "condition.field", f"未知条件字段: '{fld}'，已知字段: {sorted(known_fields)}"
                )
            )

        target = rule.action.target
        if target and target not in known_fields:
            errors.append(ValidationError(rule.rule_id, "action.target", f"未知动作目标字段: '{target}'"))

    return errors


# 合法动作类型枚举集合 [F-P0-3]
# 需与 config/safety_rules.yaml 的 action_types 声明保持同步
VALID_ACTION_TYPES = {
    "override",
    "alert_and_override",
    "stop",
    "force_change",
    "pause_and_alert",
    "e_stop",
    "hold",
}


def _check_action_type(rules: list[SafetyRule]) -> list[ValidationError]:
    """校验 action.type 是否为合法枚举值 [F-P0-3]。"""
    errors = []
    for rule in rules:
        # RuleAction.action_type 为 ActionType(str, Enum)；
        # 取 .value 得到字符串再做集合校验，避免依赖枚举成员身份
        atype = rule.action.action_type
        atype_str = atype.value if isinstance(atype, ActionType) else str(atype)
        if atype_str and atype_str not in VALID_ACTION_TYPES:
            errors.append(
                ValidationError(
                    rule.rule_id, "action.type", f"未知动作类型: '{atype_str}'，合法类型: {sorted(VALID_ACTION_TYPES)}"
                )
            )
    return errors


def _check_priority_dependency(rules: list[SafetyRule]) -> list[ValidationError]:
    """优先级循环依赖检查 - P0规则不能被其他低优先级规则覆盖"""
    errors = []

    p0_rules = [r for r in rules if r.priority == Priority.P0]
    for rule in rules:
        if rule.priority != Priority.P0:
            for p0 in p0_rules:
                if rule.action.target == p0.condition.field:
                    errors.append(
                        ValidationError(
                            rule.rule_id,
                            "priority",
                            f"规则 {rule.rule_id}({rule.priority.value}) "
                            f"尝试修改P0规则 {p0.rule_id} 监控的字段 '{p0.condition.field}'，"
                            f"存在优先级依赖冲突",
                        )
                    )

    return errors


def _check_action_target_validity(rules: list[SafetyRule]) -> list[ValidationError]:
    """动作目标有效性检查 - 确保动作类型与目标匹配"""
    errors = []

    for rule in rules:
        atype = rule.action.action_type
        target = rule.action.target

        if atype in (ActionType.OVERRIDE, ActionType.ALERT_AND_OVERRIDE):
            if not target:
                errors.append(
                    ValidationError(rule.rule_id, "action.target", f"动作类型 '{atype.value}' 必须指定覆盖目标")
                )

        if atype == ActionType.STOP:
            if target and target not in ("spindle_speed", "feed_rate"):
                errors.append(
                    ValidationError(
                        rule.rule_id, "action.target", f"STOP类型动作目标通常应为机床参数，当前: '{target}'"
                    )
                )

    return errors


# 规则引擎


class SafeMathEvaluator:
    """
    严格受限的数学表达式求值器（替代不安全的 eval/eval 等价的代码执行入口）。

    安全策略：
    - 仅允许 AST 节点类型: Expression, BinOp, UnaryOp, Constant/Num,
      Add, Sub, Mult, Div, USub/UAdd, Load
    - 拒绝任何 Name/Call/Attribute/Subscript/Compare 等可执行结构
    - 拒绝任何字符串/列表/字典/函数等常量类型
    - 拒绝负号两侧的极端指数（防止数值炸弹）
    - 除零显式返回 0.0，保持与原始降级行为一致

    性能：
    - 对原始表达式字符串做白名单正则预检（与 AST 双重校验）
    - 解析后的 AST 在模块级 LRU 缓存中复用，避免重复解析开销
    """

    _ALLOWED_BINOPS: tuple[type, ...] = (ast.Add, ast.Sub, ast.Mult, ast.Div)
    _ALLOWED_UNARYOPS: tuple[type, ...] = (ast.UAdd, ast.USub)
    _ALLOWED_CONSTANTS: tuple[type, ...] = (ast.Constant,)
    # 仅由数字、小数点、空白、四个基本运算符和括号组成的字符串
    _PRECHECK_PATTERN = re.compile(r"^[\d\s\+\-\*\/\(\)\.]+$")

    def __init__(self, expr: str):
        self._original = expr
        # 预检：字符白名单（防御性，最严格的检查放在 AST 解析后）
        if not self._PRECHECK_PATTERN.match(expr):
            raise ValueError(f"表达式包含非法字符: {expr!r}")
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as exc:
            raise ValueError(f"表达式无法解析: {expr!r}") from exc
        self._tree = tree
        self._validate(tree)

    @classmethod
    def compile(cls, expr: str) -> "SafeMathEvaluator":
        """编译并缓存，重复字符串命中缓存以提升性能。"""
        cached = _SAFE_EXPR_CACHE.get(expr)
        if cached is not None:
            return cached
        evaluator = cls(expr)
        if len(_SAFE_EXPR_CACHE) >= _SAFE_EXPR_CACHE_MAX:
            # 简单的 FIFO 淘汰，避免无界增长
            _SAFE_EXPR_CACHE.pop(next(iter(_SAFE_EXPR_CACHE)))
        _SAFE_EXPR_CACHE[expr] = evaluator
        return evaluator

    def _validate(self, node: ast.AST) -> None:
        """递归白名单校验：拒绝任何不在白名单中的 AST 节点类型。"""
        if isinstance(node, ast.Expression):
            self._validate(node.body)
            return
        if isinstance(node, ast.BinOp) and isinstance(node.op, self._ALLOWED_BINOPS):
            self._validate(node.left)
            self._validate(node.right)
            return
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, self._ALLOWED_UNARYOPS):
            self._validate(node.operand)
            return
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"仅允许数值常量，发现: {type(value).__name__}")
            return
        # 任何其它节点类型（Name/Call/Attribute/Subscript/Compare/...）一律拒绝
        raise ValueError(f"表达式包含禁止的节点类型: {type(node).__name__}")

    def evaluate(self) -> float:
        """对已校验的 AST 进行求值，所有错误均降级为 0.0。"""
        try:
            result = self._eval_node(self._tree.body)
        except (ArithmeticError, ValueError, TypeError, ZeroDivisionError):
            return 0.0
        if not isinstance(result, (int, float)) or isinstance(result, bool):
            return 0.0
        return float(result)

    def _eval_node(self, node: ast.AST) -> float:
        if isinstance(node, ast.BinOp):
            left = self._eval_node(node.left)
            right = self._eval_node(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    # 显式除零：返回 0.0 而非抛出，与原始降级行为保持一致
                    return 0.0
                return left / right
        if isinstance(node, ast.UnaryOp):
            operand = self._eval_node(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +operand
            if isinstance(node.op, ast.USub):
                return -operand
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        return 0.0


def safe_eval_math_expression(expr: Any) -> float:
    """
    对外暴露的安全数学表达式求值入口。

    - 接受任何类型输入；非字符串/None 一律返回 0.0
    - 解析或校验失败时返回 0.0，行为与原 eval 失败降级保持一致
    - 不进行任何形式的代码执行（无 eval/exec/compile 调用栈）
    """
    if not isinstance(expr, str):
        return 0.0
    stripped = expr.strip()
    if not stripped:
        return 0.0
    try:
        return SafeMathEvaluator.compile(stripped).evaluate()
    except (ValueError, TypeError):
        return 0.0


class SafetyRuleEngine:
    """
    制造安全约束规则引擎

    负责加载规则、评估传感器数据、触发条件匹配和动作执行。
    支持审计日志记录和性能计时。
    """

    def __init__(self):
        self.rules: list[SafetyRule] = []
        self._audit_log: list[AuditEntry] = []
        self._machine_context: dict[str, Any] = {}

    def load_rules(self, rules: list[SafetyRule]) -> list[ValidationError]:
        """加载并验证规则列表，返回验证错误"""
        errors = validate_rules(rules)
        self.rules = sorted(rules, key=lambda r: r.priority.level)
        return errors

    @classmethod
    def from_yaml(cls, yaml_path: str) -> "SafetyRuleEngine":
        """从YAML文件加载规则"""
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"规则文件不存在: {yaml_path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        rules = []
        for item in data.get("rules", []):
            rule = SafetyRule.from_dict(item)
            rules.append(rule)

        engine = cls()
        errors = engine.load_rules(rules)
        if errors:
            for e in errors:
                logger.warning("规则验证错误 [%s] %s: %s", e.rule_id, e.field, e.message)

        logger.info("安全规则引擎加载完成: %s 条规则", len(rules))
        return engine

    def evaluate(
        self,
        sensor_data: dict[str, Any],
        *,
        collect_audit: bool = True,
    ) -> list[dict[str, Any]]:
        """
        评估传感器数据，返回触发的动作列表

        Args:
            sensor_data: 传感器读数，如 {"spindle_speed": 12000, ...}
            collect_audit: 是否收集审计日志

        Returns:
            触发的动作列表，按优先级排序
        """
        triggered: list[dict[str, Any]] = []

        for rule in self.rules:
            if self._evaluate_condition(rule.condition, sensor_data):
                action_result = self._build_action_result(rule, sensor_data)

                if collect_audit and rule.audit:
                    self._record_audit(rule, sensor_data, action_result)

                triggered.append(action_result)

        return triggered

    def _evaluate_condition(self, condition: RuleCondition, sensor_data: dict[str, Any]) -> bool:
        """
        评估单个条件是否满足

        支持两种条件类型:
        - threshold: 简单阈值比较
        - composite: 复合条件（AND/OR组合，未来扩展）
        """
        fld = condition.field
        if fld not in sensor_data:
            return False

        actual = sensor_data[fld]
        expected = condition.value

        # 支持引用其他字段值 (如 max_spindle_speed)
        if isinstance(expected, str) and expected in sensor_data:
            expected = sensor_data[expected]

        return self._compare(actual, condition.operator, expected)

    @staticmethod
    def _compare(actual: Any, operator: str, expected: Any) -> bool:
        """数值比较"""
        try:
            a = float(actual)
            e = float(expected)
        except (ValueError, TypeError):
            return actual == expected

        if operator == ">":
            return a > e
        elif operator == "<":
            return a < e
        elif operator == ">=":
            return a >= e
        elif operator == "<=":
            return a <= e
        elif operator == "==":
            return a == e
        elif operator == "!=":
            return a != e
        return False

    def _build_action_result(self, rule: SafetyRule, sensor_data: dict[str, Any]) -> dict[str, Any]:
        """构建动作结果"""
        action_value = rule.action.value
        if isinstance(action_value, str) and "*" in action_value:
            action_value = self._resolve_expression(action_value, sensor_data)

        return {
            "rule_id": rule.rule_id,
            "name": rule.name,
            "priority": rule.priority.value,
            "action_type": rule.action.action_type.value,
            "target": rule.action.target,
            "value": action_value,
            "duration": rule.action.duration,
            "timestamp": time.time(),
        }

    def _resolve_expression(self, expr: str, sensor_data: dict[str, Any]) -> float:
        """
        解析简单算术表达式，如 ``max_spindle_speed * 0.9``。

        流程：
        1. 字符串替换：把 sensor_data 中的字段名替换为对应数值
           - 键按长度倒序替换，避免 ``spindle_speed`` 误替换 ``max_spindle_speed``
        2. 通过 :class:`SafeMathEvaluator` 进行严格白名单 AST 求值
        3. 任意环节失败一律返回 ``0.0``，与原始降级行为一致
        """
        if not isinstance(expr, str):
            return 0.0
        try:
            resolved = expr
            # 按键长倒序：避免短键被先替换导致长键后续无法匹配
            for key in sorted(sensor_data.keys(), key=len, reverse=True):
                if not key:
                    continue
                if key in resolved:
                    try:
                        resolved = resolved.replace(key, str(float(sensor_data[key])))
                    except (ValueError, TypeError):
                        return 0.0
            return safe_eval_math_expression(resolved)
        except (ValueError, TypeError, OSError) as e:
            # 防御性兜底：解析或求值失败时降级为 0.0
            logger.warning("规则表达式求值失败: expr=%s, error=%s", expr, e)
            return 0.0

    def _record_audit(self, rule: SafetyRule, sensor_data: dict[str, Any], action_result: dict[str, Any]) -> None:
        """记录审计日志"""
        entry = AuditEntry(
            timestamp=time.time(),
            rule_id=rule.rule_id,
            priority=rule.priority.value,
            condition_values={rule.condition.field: sensor_data.get(rule.condition.field)},
            action={
                "type": action_result["action_type"],
                "target": action_result["target"],
                "value": action_result["value"],
            },
            result=f"{action_result['action_type']}: {action_result['target']} → {action_result['value']}",
        )
        self._audit_log.append(entry)

    def get_audit_log(self) -> list[dict[str, Any]]:
        """获取审计日志"""
        return [e.to_dict() for e in self._audit_log]

    def clear_audit_log(self) -> None:
        """清除审计日志"""
        self._audit_log.clear()

    def get_rules_by_category(self, category: RuleCategory) -> list[SafetyRule]:
        """按类别获取规则"""
        return [r for r in self.rules if r.category == category]

    def get_rules_by_priority(self, priority: Priority) -> list[SafetyRule]:
        """按优先级获取规则"""
        return [r for r in self.rules if r.priority == priority]

    @property
    def rule_count(self) -> int:
        return len(self.rules)
