"""
沙箱安全测试套件 - 验证 skill_loader.py 沙箱逃逸漏洞修复效果

测试覆盖：
1. 直接导入攻击（__import__）
2. 类层次遍历攻击（type、__subclasses__、__bases__、__mro__）
3. 属性访问绕过（getattr、hasattr、vars、dir）
4. 代码执行攻击（exec、eval、compile）
5. 文件 I/O 攻击（open）
6. 模块导入攻击（import os、import sys 等）
7. 正常功能测试（确保安全代码仍可执行）
"""

import sys
import os

# 确保项目路径在 sys.path 中
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.plugins.skill_loader import SkillLoader, SecurityError  # noqa: E402


class TestResult:
    """测试结果记录器"""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.results = []

    def record(self, name: str, passed: bool, detail: str = ""):
        status = "PASS" if passed else "FAIL"
        self.results.append((name, status, detail))
        if passed:
            self.passed += 1
        else:
            self.failed += 1
        print(f"[{status}] {name}")
        if detail:
            print(f"       {detail}")

    def summary(self):
        print("\n" + "=" * 60)
        print(f"测试结果: {self.passed} 通过, {self.failed} 失败, {self.passed + self.failed} 总计")
        print("=" * 60)
        return self.failed == 0


def run_tests():
    results = TestResult()
    loader = SkillLoader()

    # 测试组 1: 直接导入攻击
    print("\n--- 测试组 1: 直接导入攻击 ---")

    # 测试 1.1: __import__ 直接调用
    code = 'result = __import__("os").system("echo hacked")'
    try:
        loader._compile_code(code, "test_import_direct")
        results.record("__import__ 直接调用", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("__import__ 直接调用", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("__import__ 直接调用", True, f"执行层拦截: {type(e).__name__}")

    # 测试 1.2: import 语句
    code = "import os\nresult = os.system('echo hacked')"
    try:
        loader._compile_code(code, "test_import_stmt")
        results.record("import 语句", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("import 语句", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("import 语句", True, f"执行层拦截: {type(e).__name__}")

    # 测试 1.3: from ... import 语句
    code = "from os import system\nresult = system('echo hacked')"
    try:
        loader._compile_code(code, "test_from_import")
        results.record("from import 语句", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("from import 语句", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("from import 语句", True, f"执行层拦截: {type(e).__name__}")

    # 测试组 2: 类层次遍历攻击（经典沙箱逃逸路径）
    print("\n--- 测试组 2: 类层次遍历攻击 ---")

    # 测试 2.1: type().__bases__[0].__subclasses__() 经典逃逸
    code = "result = type('').__bases__[0].__subclasses__()"
    try:
        loader._compile_code(code, "test_type_bases")
        results.record("type().__bases__ 逃逸", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("type().__bases__ 逃逸", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("type().__bases__ 逃逸", True, f"执行层拦截: {type(e).__name__}")

    # 测试 2.2: ().__class__.__bases__[0].__subclasses__() 逃逸
    code = "result = ().__class__.__bases__[0].__subclasses__()"
    try:
        loader._compile_code(code, "test_class_bases")
        results.record("().__class__.__bases__ 逃逸", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("().__class__.__bases__ 逃逸", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("().__class__.__bases__ 逃逸", True, f"执行层拦截: {type(e).__name__}")

    # 测试 2.3: __mro__ 遍历
    code = "result = ''.__class__.__mro__[1].__subclasses__()"
    try:
        loader._compile_code(code, "test_mro")
        results.record("__mro__ 遍历逃逸", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("__mro__ 遍历逃逸", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("__mro__ 遍历逃逸", True, f"执行层拦截: {type(e).__name__}")

    # 测试 2.4: __globals__ 访问
    code = "result = (lambda:0).__globals__"
    try:
        loader._compile_code(code, "test_globals")
        results.record("__globals__ 访问", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("__globals__ 访问", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("__globals__ 访问", True, f"执行层拦截: {type(e).__name__}")

    # 测试 2.5: __builtins__ 访问
    code = "result = (lambda:0).__globals__['__builtins__']"
    try:
        loader._compile_code(code, "test_builtins")
        results.record("__builtins__ 访问", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("__builtins__ 访问", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("__builtins__ 访问", True, f"执行层拦截: {type(e).__name__}")

    # 测试组 3: 属性访问绕过
    print("\n--- 测试组 3: 属性访问绕过 ---")

    # 测试 3.1: getattr 绕过
    code = 'result = getattr(__builtins__, "__import__")'
    try:
        loader._compile_code(code, "test_getattr")
        results.record("getattr 绕过", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("getattr 绕过", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("getattr 绕过", True, f"执行层拦截: {type(e).__name__}")

    # 测试 3.2: vars 绕过
    code = "result = vars(__builtins__)"
    try:
        loader._compile_code(code, "test_vars")
        results.record("vars 绕过", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("vars 绕过", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("vars 绕过", True, f"执行层拦截: {type(e).__name__}")

    # 测试 3.3: dir 绕过
    code = "result = dir(__builtins__)"
    try:
        loader._compile_code(code, "test_dir")
        results.record("dir 绕过", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("dir 绕过", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("dir 绕过", True, f"执行层拦截: {type(e).__name__}")

    # 测试 3.4: hasattr 探测
    code = "result = hasattr(__builtins__, '__import__')"
    try:
        loader._compile_code(code, "test_hasattr")
        results.record("hasattr 探测", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("hasattr 探测", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("hasattr 探测", True, f"执行层拦截: {type(e).__name__}")

    # 测试组 4: 代码执行攻击
    print("\n--- 测试组 4: 代码执行攻击 ---")

    # 测试 4.1: exec 调用
    code = "exec(\"import os; os.system('echo hacked')\")"
    try:
        loader._compile_code(code, "test_exec")
        results.record("exec 调用", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("exec 调用", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("exec 调用", True, f"执行层拦截: {type(e).__name__}")

    # 测试 4.2: eval 调用
    code = "eval(\"__import__('os').system('echo hacked')\")"
    try:
        loader._compile_code(code, "test_eval")
        results.record("eval 调用", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("eval 调用", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("eval 调用", True, f"执行层拦截: {type(e).__name__}")

    # 测试 4.3: compile 调用
    code = 'compile("import os", "<test>", "exec")'
    try:
        loader._compile_code(code, "test_compile")
        results.record("compile 调用", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("compile 调用", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("compile 调用", True, f"执行层拦截: {type(e).__name__}")

    # 测试组 5: 文件 I/O 攻击
    print("\n--- 测试组 5: 文件 I/O 攻击 ---")

    # 测试 5.1: open 文件读取
    code = 'result = open("/etc/passwd").read()'
    try:
        loader._compile_code(code, "test_open")
        results.record("open 文件读取", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("open 文件读取", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("open 文件读取", True, f"执行层拦截: {type(e).__name__}")

    # 测试 5.2: input 交互
    code = 'result = input("password:")'
    try:
        loader._compile_code(code, "test_input")
        results.record("input 交互", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("input 交互", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("input 交互", True, f"执行层拦截: {type(e).__name__}")

    # 测试组 6: 模块导入攻击
    print("\n--- 测试组 6: 模块导入攻击 ---")

    # 测试 6.1: os 模块
    code = "result = os"
    try:
        loader._compile_code(code, "test_os_ref")
        results.record("os 模块引用", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("os 模块引用", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("os 模块引用", True, f"执行层拦截: {type(e).__name__}")

    # 测试 6.2: subprocess 模块
    code = "result = subprocess"
    try:
        loader._compile_code(code, "test_subprocess_ref")
        results.record("subprocess 模块引用", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("subprocess 模块引用", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("subprocess 模块引用", True, f"执行层拦截: {type(e).__name__}")

    # 测试 6.3: pickle 反序列化攻击
    code = "result = pickle"
    try:
        loader._compile_code(code, "test_pickle_ref")
        results.record("pickle 模块引用", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("pickle 模块引用", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("pickle 模块引用", True, f"执行层拦截: {type(e).__name__}")

    # 测试组 7: 其他绕过技巧
    print("\n--- 测试组 7: 其他绕过技巧 ---")

    # 测试 7.1: __code__ 访问
    code = "result = (lambda:0).__code__"
    try:
        loader._compile_code(code, "test_code")
        results.record("__code__ 访问", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("__code__ 访问", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("__code__ 访问", True, f"执行层拦截: {type(e).__name__}")

    # 测试 7.2: breakpoint 调用
    code = "breakpoint()"
    try:
        loader._compile_code(code, "test_breakpoint")
        results.record("breakpoint 调用", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("breakpoint 调用", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("breakpoint 调用", True, f"执行层拦截: {type(e).__name__}")

    # 测试 7.3: load_module 绕过
    code = "result = type('').__bases__[0].__subclasses__()[0].load_module('os')"
    try:
        loader._compile_code(code, "test_load_module")
        results.record("load_module 绕过", False, "应该被拦截但未被拦截")
    except SecurityError:
        results.record("load_module 绕过", True, "安全审计层拦截成功")
    except Exception as e:
        results.record("load_module 绕过", True, f"执行层拦截: {type(e).__name__}")

    # 测试组 8: 正常功能测试（确保安全代码仍可执行）
    print("\n--- 测试组 8: 正常功能测试 ---")

    # 测试 8.1: 基本数学运算
    code = """
def execute():
    return abs(-5) + max(1, 2, 3) + min(4, 5, 6) + pow(2, 3)
"""
    try:
        executor = loader._compile_code(code, "test_math")
        result = executor()
        expected = 5 + 3 + 4 + 8  # 20
        results.record("基本数学运算", result == expected, f"结果: {result} (期望: {expected})")
    except Exception as e:
        results.record("基本数学运算", False, f"异常: {type(e).__name__}: {e}")

    # 测试 8.2: 列表和字符串操作
    code = """
def execute():
    data = [3, 1, 4, 1, 5, 9]
    return sorted(data), len(data), str(sum(data))
"""
    try:
        executor = loader._compile_code(code, "test_list_str")
        sorted_data, length, total_str = executor()
        results.record(
            "列表和字符串操作",
            sorted_data == [1, 1, 3, 4, 5, 9] and length == 6 and total_str == "23",
            f"结果: {sorted_data}, {length}, {total_str}",
        )
    except Exception as e:
        results.record("列表和字符串操作", False, f"异常: {type(e).__name__}: {e}")

    # 测试 8.3: 字典操作
    code = """
def execute():
    d = {"a": 1, "b": 2, "c": 3}
    return list(d.keys()), sum(d.values()), len(d)
"""
    try:
        executor = loader._compile_code(code, "test_dict")
        keys, total, length = executor()
        results.record(
            "字典操作",
            keys == ["a", "b", "c"] and total == 6 and length == 3,
            f"结果: {keys}, {total}, {length}",
        )
    except Exception as e:
        results.record("字典操作", False, f"异常: {type(e).__name__}: {e}")

    # 测试 8.4: range 和 enumerate
    code = """
def execute():
    result = []
    for i, x in enumerate(range(5)):
        result.append(x * 2)
    return result
"""
    try:
        executor = loader._compile_code(code, "test_range")
        result = executor()
        results.record("range/enumerate", result == [0, 2, 4, 6, 8], f"结果: {result}")
    except Exception as e:
        results.record("range/enumerate", False, f"异常: {type(e).__name__}: {e}")

    # 测试 8.5: 类型转换与字符串拼接
    # 注意：异常类（如 ValueError）不在白名单中（白名单仅含 21 项纯计算函数），
    # 因此异常处理不是沙箱内的支持能力。改为测试类型转换和字符串拼接。
    code = """
def execute():
    n = int("42")
    f = float("3.14")
    s = str(n) + " " + str(f)
    return s
"""
    try:
        executor = loader._compile_code(code, "test_type_conv")
        result = executor()
        results.record("类型转换与字符串拼接", result == "42 3.14", f"结果: {result}")
    except Exception as e:
        results.record("类型转换与字符串拼接", False, f"异常: {type(e).__name__}: {e}")

    # 测试 8.6: any/all 函数
    code = """
def execute():
    return all([True, True, True]), any([False, False, True])
"""
    try:
        executor = loader._compile_code(code, "test_any_all")
        all_result, any_result = executor()
        results.record("any/all 函数", all_result and any_result, f"结果: {all_result}, {any_result}")
    except Exception as e:
        results.record("any/all 函数", False, f"异常: {type(e).__name__}: {e}")

    # 测试 8.7: 全部 21 项白名单函数逐一验证
    # 验证每个允许的函数在沙箱内都能正常工作
    whitelist_tests = [
        # (函数名, 测试表达式, 期望结果) - 表达式会被嵌入 return <expr> 中
        ("True", "True is True", True),
        ("False", "False is False", True),
        ("None", "None is None", True),
        ("bool", "bool(1) and not bool(0)", True),
        ("float", "float('1.5') == 1.5", True),
        ("int", "int('10') == 10", True),
        ("str", "str(123) == '123'", True),
        ("abs", "abs(-7) == 7", True),
        ("divmod", "divmod(10, 3) == (3, 1)", True),
        ("max", "max(1, 2, 3) == 3", True),
        ("min", "min(1, 2, 3) == 1", True),
        ("pow", "pow(2, 10) == 1024", True),
        ("round", "round(3.14159, 2) == 3.14", True),
        ("sum", "sum([1, 2, 3, 4]) == 10", True),
        ("all", "all([1, 2, 3]) and not all([0, 1, 2])", True),
        ("any", "any([0, 0, 1]) and not any([0, 0, 0])", True),
        ("enumerate", "[i for i, v in enumerate(['a', 'b'])] == [0, 1]", True),
        ("len", "len('hello') == 5", True),
        ("list", "list(range(3)) == [0, 1, 2]", True),
        ("range", "list(range(0, 5, 2)) == [0, 2, 4]", True),
        ("sorted", "sorted([3, 1, 2]) == [1, 2, 3]", True),
    ]
    for func_name, expr, expected in whitelist_tests:
        code = f"""
def execute():
    return {expr}
"""
        try:
            executor = loader._compile_code(code, f"test_{func_name}")
            result = executor()
            results.record(
                f"白名单函数 '{func_name}' 可用",
                result == expected,
                f"结果: {result} (期望: {expected})",
            )
        except Exception as e:
            results.record(
                f"白名单函数 '{func_name}' 可用",
                False,
                f"异常: {type(e).__name__}: {e}",
            )

    # 测试 8.8: 危险函数阻断验证
    # 验证 ValueError 等异常类在沙箱中不可用（异常类不在白名单中）
    code = """
def execute():
    return ValueError("test")
"""
    try:
        executor = loader._compile_code(code, "test_value_error")
        try:
            executor()
            results.record("ValueError 不可用", False, "异常类不应在白名单中")
        except NameError:
            results.record("ValueError 不可用", True, "异常类被正确排除")
        except Exception:
            results.record("ValueError 不可用", True, "异常类被正确排除")
    except SecurityError:
        results.record("ValueError 不可用", True, "代码审计层拦截成功")

    # 测试组 9: _SAFE_BUILTINS 完整性审计
    print("\n--- 测试组 9: _SAFE_BUILTINS 完整性审计 ---")

    forbidden_in_safe = [
        "__import__",
        "type",
        "vars",
        "dir",
        "getattr",
        "hasattr",
        "object",
        "super",
        "callable",
        "isinstance",
        "issubclass",
        "exec",
        "eval",
        "compile",
        "open",
        "input",
        "breakpoint",
        "print",
        "memoryview",
        "property",
        "staticmethod",
        "classmethod",
        "ascii",
        "repr",
        "hash",
        "iter",
        "next",
        "map",
        "filter",
        "bytes",
        "bytearray",
    ]

    safe_builtins = loader._SAFE_BUILTINS
    for func_name in forbidden_in_safe:
        if func_name in safe_builtins:
            results.record(
                f"SAFE_BUILTINS 不应包含 '{func_name}'",
                False,
                "该函数是危险的，但仍在白名单中",
            )
        else:
            results.record(
                f"SAFE_BUILTINS 不含 '{func_name}'",
                True,
                "已正确移除",
            )

    # 验证必要的安全函数仍然存在
    required_safe = [
        "abs",
        "len",
        "range",
        "str",
        "int",
        "float",
        "sum",
        "min",
        "max",
        "pow",
        "round",
        "sorted",
        "any",
        "all",
        "True",
        "False",
        "None",
    ]
    for func_name in required_safe:
        if func_name in safe_builtins:
            results.record(
                f"SAFE_BUILTINS 包含必需函数 '{func_name}'",
                True,
                "安全函数可用",
            )
        else:
            results.record(
                f"SAFE_BUILTINS 缺少必需函数 '{func_name}'",
                False,
                "该函数是安全的，但不在白名单中",
            )

    return results.summary()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
