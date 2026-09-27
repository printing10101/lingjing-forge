"""
测试用例生成器

基于代码分析结果自动生成 pytest 测试文件
"""

from pathlib import Path
from datetime import datetime
from .analyzer import CodeAnalyzer, ModuleInfo, FunctionInfo, ClassInfo


class TestGenerator:
    """测试用例生成器"""

    def __init__(self, output_dir: str | Path = "tests/generated"):
        self.output_dir = Path(output_dir)

    def generate_for_module(self, module_info: ModuleInfo) -> Path | None:
        """为单个模块生成测试文件"""
        testable = self._get_testable_items(module_info)
        if not testable['functions'] and not testable['classes']:
            return None

        lines = [
            '"""',
            f'自动生成的测试文件 - {module_info.name}',
            f'生成时间: {datetime.now().isoformat()}',
            '"""',
            '',
            'import pytest',
        ]

        # 尝试生成导入语句
        module_import = module_info.name
        lines.append('')
        lines.append('# 注意: 请根据实际项目结构调整导入路径')
        lines.append(f'# from {module_import} import ...')
        lines.append('')

        # 为函数生成测试
        for func in testable['functions']:
            lines.extend(self._generate_function_test(func, module_import))

        # 为类生成测试
        for cls in testable['classes']:
            lines.extend(self._generate_class_test(cls, module_import))

        # 写入文件
        self.output_dir.mkdir(parents=True, exist_ok=True)
        safe_name = module_info.name.replace('.', '_')
        test_file = self.output_dir / f"test_{safe_name}.py"
        test_file.write_text('\n'.join(lines), encoding='utf-8')
        return test_file

    def generate_for_directory(self, source_dir: str | Path) -> list[Path]:
        """为目录中所有模块生成测试文件"""
        analyzer = CodeAnalyzer(source_dir)
        modules = analyzer.analyze_directory()
        generated = []
        for mod in modules:
            result = self.generate_for_module(mod)
            if result:
                generated.append(result)
        return generated

    def _get_testable_items(self, module_info: ModuleInfo) -> dict:
        """获取可测试的项目"""
        testable = {'functions': [], 'classes': []}
        for func in module_info.functions:
            if not func.name.startswith('_'):
                testable['functions'].append(func)
        for cls in module_info.classes:
            if not cls.name.startswith('_'):
                public_methods = [
                    m for m in cls.methods
                    if not m.name.startswith('_') or m.name.startswith('__')
                ]
                if public_methods:
                    cls_copy = ClassInfo(
                        name=cls.name, module=cls.module,
                        lineno=cls.lineno, bases=cls.bases,
                        methods=public_methods, docstring=cls.docstring
                    )
                    testable['classes'].append(cls_copy)
        return testable

    def _generate_function_test(self, func: FunctionInfo, module_import: str) -> list[str]:
        """为函数生成测试代码"""
        lines = [
            '',
            f'class Test_{func.name}:',
            f'    """测试 {func.name} 函数"""',
            '',
        ]

        # 生成正常路径测试
        lines.extend([
            f'    def test_{func.name}_normal(self):',
            '        """正常路径测试"""',
        ])

        # 生成测试数据
        test_args = self._generate_test_args(func)
        if test_args:
            args_str = ', '.join(f'{k}={v}' for k, v in test_args.items())
            lines.append(f'        result = {func.name}({args_str})')
            lines.append('        assert result is not None')
            lines.append('        # TODO: 添加更具体的断言')
        else:
            lines.append(f'        result = {func.name}()')
            lines.append('        assert result is not None')
            lines.append('        # TODO: 添加更具体的断言')

        lines.append('')

        # 生成边界测试
        lines.extend([
            f'    def test_{func.name}_edge_cases(self):',
            '        """边界条件测试"""',
        ])

        # 为每个参数生成边界测试
        for arg in func.args:
            if arg != 'self':
                lines.extend([
                    f'        # 测试 {arg} 为 None',
                    '        # TODO: 根据实际类型调整',
                    f'        # result = {func.name}({arg}=None)',
                    '        # assert result is not None',
                    '',
                    f'        # 测试 {arg} 为空值',
                    '        # TODO: 根据实际类型调整',
                    f'        # result = {func.name}({arg}="")',
                    '        # assert result is not None',
                    '',
                ])
                break  # 只为第一个参数生成示例

        # 如果是异步函数，添加异步测试
        if func.is_async:
            lines.extend([
                '    @pytest.mark.asyncio',
                f'    async def test_{func.name}_async(self):',
                '        """异步调用测试"""',
            ])
            if test_args:
                args_str = ', '.join(f'{k}={v}' for k, v in test_args.items())
                lines.append(f'        result = await {func.name}({args_str})')
            else:
                lines.append(f'        result = await {func.name}()')
            lines.append('        assert result is not None')
            lines.append('')

        return lines

    def _generate_test_args(self, func: FunctionInfo) -> dict[str, str]:
        """为函数参数生成测试数据"""
        test_args = {}

        for arg in func.args:
            if arg == 'self':
                continue

            # 检查是否有默认值
            if arg in func.defaults:
                default = func.defaults[arg]
                if isinstance(default, str):
                    test_args[arg] = f'"{default}"'
                elif isinstance(default, (int, float)):
                    test_args[arg] = str(default)
                elif isinstance(default, bool):
                    test_args[arg] = str(default)
                elif default is None:
                    test_args[arg] = 'None'
                else:
                    test_args[arg] = repr(default)
            else:
                # 根据参数名猜测类型
                arg_lower = arg.lower()
                if 'name' in arg_lower or 'title' in arg_lower or 'path' in arg_lower:
                    test_args[arg] = '"test_value"'
                elif 'count' in arg_lower or 'num' in arg_lower or 'size' in arg_lower or 'length' in arg_lower:
                    test_args[arg] = '10'
                elif 'flag' in arg_lower or 'is_' in arg_lower or 'has_' in arg_lower:
                    test_args[arg] = 'True'
                elif 'data' in arg_lower or 'content' in arg_lower or 'text' in arg_lower:
                    test_args[arg] = '"test data"'
                elif 'index' in arg_lower or 'id' in arg_lower:
                    test_args[arg] = '0'
                else:
                    # 默认使用字符串
                    test_args[arg] = '"test"'

        return test_args

    def _generate_class_test(self, cls: ClassInfo, module_import: str) -> list[str]:
        """为类生成测试代码"""
        lines = [
            '',
            f'class Test_{cls.name}:',
            f'    """测试 {cls.name} 类"""',
            '',
        ]

        # 查找 __init__ 方法
        init_method = None
        for method in cls.methods:
            if method.name == '__init__':
                init_method = method
                break

        # 生成 fixture
        lines.extend([
            '    @pytest.fixture',
            '    def instance(self):',
            '        """创建测试实例"""',
        ])

        if init_method and init_method.args:
            # 生成构造函数参数
            init_args = self._generate_test_args(init_method)
            if init_args:
                args_str = ', '.join(f'{k}={v}' for k, v in init_args.items())
                lines.append(f'        instance = {cls.name}({args_str})')
            else:
                lines.append(f'        instance = {cls.name}()')
        else:
            lines.append(f'        instance = {cls.name}()')

        lines.append('        return instance')
        lines.append('')

        # 为每个公开方法生成测试
        for method in cls.methods:
            if method.name.startswith('__') and method.name != '__init__':
                continue
            if method.name == '__init__':
                continue

            lines.extend([
                f'    def test_{method.name}(self, instance):',
                f'        """测试 {method.name} 方法"""',
            ])

            # 生成方法调用参数
            method_args = self._generate_test_args(method)
            if method_args:
                args_str = ', '.join(f'{k}={v}' for k, v in method_args.items())
                lines.append(f'        result = instance.{method.name}({args_str})')
            else:
                lines.append(f'        result = instance.{method.name}()')

            lines.append('        assert result is not None')
            lines.append('        # TODO: 添加更具体的断言')
            lines.append('')

        return lines
