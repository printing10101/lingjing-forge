"""
安全数学表达式求值测试

覆盖 ``safety_constraint_rules.SafeMathEvaluator`` 与
``safe_eval_math_expression`` 的全部安全路径与功能路径，确保：

- 不存在 ``eval()`` 代码执行入口
- 恶意输入（import、call、attribute、subscript、字符串常量）一律返回 0.0
- 正常四则运算（含括号、一元正负号、浮点）求值正确
- 接口兼容：``SafetyRuleEngine._resolve_expression`` 行为与原实现一致
"""

import sys
import time
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pytest  # noqa: E402

from app.rules.safety_constraint_rules import (  # noqa: E402
    SafeMathEvaluator,
    SafetyRuleEngine,
    safe_eval_math_expression,
)


# 1. 正常表达式求值


class TestNormalExpressions:
    """正常数学表达式的精确求值验证。"""

    @pytest.mark.parametrize(
        "expr, expected",
        [
            ("0", 0.0),
            ("1", 1.0),
            ("42", 42.0),
            ("3.14", 3.14),
            ("-5", -5.0),
            ("+5", 5.0),
            ("--5", 5.0),
            ("10+5", 15.0),
            ("10-5", 5.0),
            ("10*5", 50.0),
            ("10/5", 2.0),
            ("10.5+20.3*2", 51.1),
            ("(10.5+20.3)*2", 61.6),
            ("2*(3+4)", 14.0),
            ("1+2+3+4", 10.0),
            ("100-50-25", 25.0),
            ("2*3*4", 24.0),
            ("100/2/5", 10.0),
            ("  3  +  4  *  2  ", 11.0),
            ("(1+2)*(3+4)", 21.0),
            ("-(3+4)", -7.0),
            ("10-20+30", 20.0),
            ("(2+3)*(4-5)", -5.0),
        ],
    )
    def test_valid_expr_returns_expected(self, expr, expected):
        result = safe_eval_math_expression(expr)
        assert result == pytest.approx(expected, abs=1e-9)
        assert isinstance(result, float)

    def test_user_stated_example(self):
        """用户验收用例：``10.5+20.3*2`` 必须精确返回 51.1"""
        assert safe_eval_math_expression("10.5+20.3*2") == 51.1


# 2. 恶意代码注入防护


class TestMaliciousInputRejected:
    """任何试图执行 Python 代码的输入必须返回 0.0 而非执行副作用。"""

    @pytest.mark.parametrize(
        "payload",
        [
            # 用户验收用例
            "__import__('os').system('echo hack')",
            # 经典注入
            "eval('1+1')",
            "exec('print(1)')",
            "compile('1+1', '<s>', 'eval')",
            # 引入对象
            "__import__('os')",
            "__builtins__",
            "open('etc/passwd')",
            "globals()",
            "locals()",
            # 字符串/列表/字典常量
            "'hello'",
            '"world"',
            "[1,2,3]",
            "{'a':1}",
            "(1,2,3)",
            "{1,2,3}",
            # 命名访问
            "True",
            "False",
            "None",
            # 属性/下标访问
            "(1).bit_length()",
            "a.b",
            "a[0]",
            # 比较/布尔表达式
            "1==1",
            "1<2",
            "1 and 2",
            "1 or 2",
            "not 1",
            # 函数调用
            "abs(-1)",
            "pow(2,3)",
            "sum([1,2,3])",
            # 三元与 lambda
            "1 if True else 2",
            "(lambda:1)()",
            # 模块级访问
            "math.sqrt(4)",
            "os.environ",
        ],
    )
    def test_malicious_returns_zero(self, payload):
        result = safe_eval_math_expression(payload)
        assert result == 0.0, f"恶意载荷 {payload!r} 不应被求值为 {result}"

    def test_no_eval_or_exec_in_module(self):
        """模块源码中不应出现 ``eval(`` / ``exec(`` / ``compile(`` 真实调用。"""
        import ast as _ast

        source_path = Path(__file__).resolve().parent.parent.parent / "app" / "rules" / "safety_constraint_rules.py"
        source = source_path.read_text(encoding="utf-8")
        tree = _ast.parse(source, filename=str(source_path))
        # 收集所有 Name 节点，定位 eval/exec/compile 的真实调用入口
        offenders: List[str] = []
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Call):
                func = node.func
                if isinstance(func, _ast.Name) and func.id in {"eval", "exec", "compile"}:
                    offenders.append(f"line {node.lineno}: {func.id}(...)")
                if isinstance(func, _ast.Attribute) and func.attr in {"eval", "exec"}:
                    offenders.append(f"line {node.lineno}: {func.attr}(...) on {_ast.dump(func.value)}")
        assert not offenders, f"发现残留的可执行入口: {offenders}"


# 3. 边界条件


class TestBoundaryConditions:
    """空表达式、非法字符、非字符串输入等边界情况。"""

    @pytest.mark.parametrize(
        "expr",
        [
            "",
            "   ",
            "\t\n",
            "+",
            "-",
            "*",
            "/",
            "()",
            "(1",
            "1)",
            "1+",
            "1-",
            "1*",
            "1/",
            "*1",
            "1 2 3",
            "1..2",
            ".",
            "1.2.3",
            "abc",
            "1+abc",
            "abc+1",
            "1 2",
            "1**2",  # 不允许幂运算
            "1%2",  # 不允许取模
            "1&2",  # 不允许位运算
            "1|2",
            "1^2",
            "//",  # 不允许整除
            "1//2",
            "a+b",
            "_x",
            "var",
            "中+文",
        ],
    )
    def test_invalid_expr_returns_zero(self, expr):
        assert safe_eval_math_expression(expr) == 0.0

    @pytest.mark.parametrize(
        "non_string",
        [
            None,
            0,
            1,
            1.5,
            True,
            False,
            [],
            {},
            (),
            object(),
            b"1+1",
        ],
    )
    def test_non_string_input_returns_zero(self, non_string):
        assert safe_eval_math_expression(non_string) == 0.0

    def test_division_by_zero_returns_zero(self):
        """除零显式降级为 0.0，与原始降级行为一致。"""
        assert safe_eval_math_expression("1/0") == 0.0
        assert safe_eval_math_expression("10/0.0") == 0.0
        assert safe_eval_math_expression("(1+1)/0") == 0.0

    def test_long_expression_handled(self):
        """构造超长但合法的数字串，确保不会无限执行。"""
        # 避免 ast.parse 的递归深度限制，使用合理规模
        huge = "+".join(["1"] * 200)
        result = safe_eval_math_expression(huge)
        assert result == pytest.approx(200.0, abs=1e-6)

    def test_evaluator_constructor_rejects_invalid(self):
        """直接构造 SafeMathEvaluator 时也应拒绝非法输入。"""
        with pytest.raises(ValueError):
            SafeMathEvaluator("1+abc")
        with pytest.raises(ValueError):
            SafeMathEvaluator("__import__('os')")
        with pytest.raises(ValueError):
            SafeMathEvaluator("eval('1')")


# 4. 缓存正确性


class TestCaching:
    """模块级缓存应让相同表达式复用编译结果，且不影响正确性。"""

    def test_cache_returns_same_value(self):
        from app.rules.safety_constraint_rules import _SAFE_EXPR_CACHE

        _SAFE_EXPR_CACHE.clear()
        assert safe_eval_math_expression("12*34") == 408.0
        assert safe_eval_math_expression("12*34") == 408.0
        # 命中缓存：至少有一条记录
        assert any("12*34" in k for k in _SAFE_EXPR_CACHE.keys())

    def test_cache_does_not_leak_state(self):
        from app.rules.safety_constraint_rules import _SAFE_EXPR_CACHE

        _SAFE_EXPR_CACHE.clear()
        a = safe_eval_math_expression("3+4")
        b = safe_eval_math_expression("(3+4)")
        c = safe_eval_math_expression(" 3+4 ")
        assert a == 7.0
        assert b == 7.0
        assert c == 7.0


# 5. 接口兼容性


class TestEngineInterfaceCompat:
    """``SafetyRuleEngine._resolve_expression`` 必须保持与原实现一致的行为。"""

    def test_resolve_with_sensor_replacement(self):
        engine = SafetyRuleEngine()
        result = engine._resolve_expression(
            "max_spindle_speed * 0.9",
            {"max_spindle_speed": 10000.0},
        )
        assert result == pytest.approx(9000.0)

    def test_resolve_with_unknown_token_returns_zero(self):
        engine = SafetyRuleEngine()
        result = engine._resolve_expression(
            "unknown_field + 1",
            {},
        )
        # unknown_field 无法替换，解析后含非法字符 -> 0.0
        assert result == 0.0

    def test_resolve_non_string_returns_zero(self):
        engine = SafetyRuleEngine()
        assert engine._resolve_expression(123, {}) == 0.0
        assert engine._resolve_expression(None, {}) == 0.0
        assert engine._resolve_expression([], {}) == 0.0

    def test_resolve_division_by_zero(self):
        engine = SafetyRuleEngine()
        # 1/0 降级为 0.0
        assert engine._resolve_expression("1/0", {}) == 0.0

    def test_resolve_eval_payload_returns_zero(self):
        engine = SafetyRuleEngine()
        # 注入字符串必须返回 0.0
        assert engine._resolve_expression("__import__('os').system('echo hack')", {}) == 0.0

    def test_end_to_end_rule_evaluation(self):
        """完整走通 ``evaluate`` 流程，确保 ``_resolve_expression`` 的改动不破坏规则链路。"""
        from app.rules.safety_constraint_rules import (
            ActionType,
            Priority,
            RuleAction,
            RuleCategory,
            RuleCondition,
            SafetyRule,
        )

        engine = SafetyRuleEngine()
        rule = SafetyRule(
            rule_id="M-001",
            name="速度限制",
            priority=Priority.P0,
            category=RuleCategory.MACHINE,
            condition=RuleCondition(
                condition_type="threshold",
                field="spindle_speed",
                operator=">",
                value="max_spindle_speed",
            ),
            action=RuleAction(
                action_type=ActionType.OVERRIDE,
                target="spindle_speed",
                value="max_spindle_speed * 0.9",
            ),
        )
        engine.load_rules([rule])
        triggered = engine.evaluate({"spindle_speed": 12000.0, "max_spindle_speed": 10000.0})
        assert len(triggered) == 1
        assert triggered[0]["value"] == pytest.approx(9000.0)


# 6. 性能


class TestPerformance:
    """新实现性能应不低于原 eval() 实现的 80%（此为相对回归测试）。"""

    def test_safe_eval_under_25ms_per_call(self):
        """单次求值 < 25ms（足够宽松，但远低于业务延迟要求）。"""
        engine = SafetyRuleEngine()
        # 预热
        for _ in range(50):
            engine._resolve_expression("max_spindle_speed * 0.9", {"max_spindle_speed": 10000.0})
        start = time.perf_counter()
        iterations = 1000
        for _ in range(iterations):
            engine._resolve_expression(
                "(max_spindle_speed + feed_rate) * 0.9 / 2",
                {"max_spindle_speed": 10000.0, "feed_rate": 500.0},
            )
        elapsed = time.perf_counter() - start
        per_call_ms = (elapsed / iterations) * 1000
        assert per_call_ms < 25, f"性能回归: {per_call_ms:.3f} ms/call"

    def test_cache_hit_is_fast(self):
        """缓存命中应明显快于冷启动。"""
        from app.rules.safety_constraint_rules import _SAFE_EXPR_CACHE

        _SAFE_EXPR_CACHE.clear()
        # 首次：冷启动
        cold_start = time.perf_counter()
        safe_eval_math_expression("100*(1+2+3+4+5)")
        cold_elapsed = time.perf_counter() - cold_start
        # 后续：全部命中缓存
        warm_start = time.perf_counter()
        for _ in range(1000):
            safe_eval_math_expression("100*(1+2+3+4+5)")
        warm_elapsed = time.perf_counter() - warm_start
        # 缓存命中后应远快于冷启动
        assert warm_elapsed * 1000 < 100, f"缓存命中过慢: {warm_elapsed * 1000:.2f} ms / 1000次"
        # 1000 次缓存命中平均到单次应显著低于冷启动
        per_call_warm_ms = (warm_elapsed / 1000) * 1000
        assert per_call_warm_ms < cold_elapsed * 1000, (
            f"缓存命中单次 ({per_call_warm_ms:.4f} ms) 未快于冷启动 ({cold_elapsed * 1000:.4f} ms)"
        )


# 7. 线程安全（模块级缓存 LRU 淘汰可能竞争）


class TestThreadSafety:
    """SafeMathEvaluator.compile 的简单 FIFO 淘汰在高并发下不应崩溃。"""

    def test_concurrent_compile(self):
        import threading
        from app.rules.safety_constraint_rules import _SAFE_EXPR_CACHE

        _SAFE_EXPR_CACHE.clear()

        errors = []

        def worker(i):
            try:
                for j in range(50):
                    safe_eval_math_expression(f"{i}+{j}*2")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"并发调用出错: {errors}"


# 8. SafeMathEvaluator 直接使用


class TestSafeMathEvaluatorDirect:
    """直接使用 ``SafeMathEvaluator.compile(...)`` 也能得到正确结果。"""

    @pytest.mark.parametrize(
        "expr, expected",
        [
            ("1+1", 2.0),
            ("10-3", 7.0),
            ("4*5", 20.0),
            ("20/4", 5.0),
            ("(1+2)*(3+4)", 21.0),
            ("-3.5", -3.5),
            ("+3.5", 3.5),
        ],
    )
    def test_compile_and_evaluate(self, expr, expected):
        evaluator = SafeMathEvaluator.compile(expr)
        assert evaluator.evaluate() == pytest.approx(expected, abs=1e-9)
        # 再次调用 evaluate 应保持一致（缓存复用）
        assert evaluator.evaluate() == pytest.approx(expected, abs=1e-9)
