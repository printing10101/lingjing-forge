"""
代码分析器

使用AST解析Python源代码，提取函数、类、方法签名和依赖关系
"""

import ast
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field


@dataclass
class FunctionInfo:
    """函数信息"""
    name: str
    module: str
    lineno: int
    args: list[str]
    defaults: dict[str, Any]
    returns: str | None
    is_async: bool = False
    docstring: str | None = None
    decorators: list[str] = field(default_factory=list)


@dataclass
class ClassInfo:
    """类信息"""
    name: str
    module: str
    lineno: int
    bases: list[str]
    methods: list[FunctionInfo] = field(default_factory=list)
    docstring: str | None = None


@dataclass
class ModuleInfo:
    """模块信息"""
    path: Path
    name: str
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


class CodeAnalyzer:
    """代码分析器"""
    
    def __init__(self, source_dir: str | Path):
        self.source_dir = Path(source_dir)
        if not self.source_dir.exists():
            raise ValueError(f"Source directory not found: {source_dir}")
    
    def analyze_file(self, file_path: str | Path) -> ModuleInfo:
        """分析单个Python文件"""
        file_path = Path(file_path)
        if not file_path.suffix == '.py':
            raise ValueError(f"Not a Python file: {file_path}")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        
        tree = ast.parse(source, filename=str(file_path))
        
        # 计算模块名（相对于source_dir）
        try:
            rel_path = file_path.relative_to(self.source_dir)
            module_name = str(rel_path.with_suffix('')).replace('/', '.').replace('\\', '.')
        except ValueError:
            module_name = file_path.stem
        
        module_info = ModuleInfo(
            path=file_path,
            name=module_name
        )
        
        # 提取导入
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_info.imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    module_info.imports.append(node.module)
        
        # 提取顶层函数和类
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                func_info = self._extract_function(node, module_name)
                module_info.functions.append(func_info)
            elif isinstance(node, ast.ClassDef):
                class_info = self._extract_class(node, module_name)
                module_info.classes.append(class_info)
        
        return module_info
    
    def analyze_directory(self, dir_path: str | Path = None, recursive: bool = True) -> list[ModuleInfo]:
        """分析目录中的所有Python文件"""
        if dir_path is None:
            dir_path = self.source_dir
        else:
            dir_path = Path(dir_path)
        
        modules = []
        pattern = '**/*.py' if recursive else '*.py'
        
        for py_file in dir_path.glob(pattern):
            if '__pycache__' in str(py_file):
                continue
            try:
                module_info = self.analyze_file(py_file)
                modules.append(module_info)
            except Exception as e:
                print(f"Warning: Failed to analyze {py_file}: {e}")
        
        return modules
    
    def _extract_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, module: str) -> FunctionInfo:
        """提取函数信息"""
        args = []
        defaults = {}
        
        # 提取参数
        for arg in node.args.args:
            if arg.arg != 'self':  # 跳过self
                args.append(arg.arg)
        
        # 提取默认值
        num_defaults = len(node.args.defaults)
        if num_defaults > 0:
            default_args = args[-num_defaults:]
            for arg_name, default_node in zip(default_args, node.args.defaults):
                try:
                    default_value = ast.literal_eval(default_node)
                    defaults[arg_name] = default_value
                except (ValueError, SyntaxError, TypeError):
                    defaults[arg_name] = '<complex>'
        
        # 提取返回类型
        returns = None
        if node.returns:
            try:
                returns = ast.unparse(node.returns)
            except (ValueError, SyntaxError, TypeError):
                returns = 'Any'
        
        # 提取装饰器
        decorators = []
        for dec in node.decorator_list:
            try:
                decorators.append(ast.unparse(dec))
            except (ValueError, SyntaxError, TypeError):
                decorators.append('<complex>')
        
        # 提取docstring
        docstring = ast.get_docstring(node)
        
        return FunctionInfo(
            name=node.name,
            module=module,
            lineno=node.lineno,
            args=args,
            defaults=defaults,
            returns=returns,
            is_async=isinstance(node, ast.AsyncFunctionDef),
            docstring=docstring,
            decorators=decorators
        )
    
    def _extract_class(self, node: ast.ClassDef, module: str) -> ClassInfo:
        """提取类信息"""
        bases = []
        for base in node.bases:
            try:
                bases.append(ast.unparse(base))
            except (ValueError, SyntaxError, TypeError):
                bases.append('object')
        
        docstring = ast.get_docstring(node)
        
        class_info = ClassInfo(
            name=node.name,
            module=module,
            lineno=node.lineno,
            bases=bases,
            docstring=docstring
        )
        
        # 提取方法
        for item in ast.iter_child_nodes(node):
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                method_info = self._extract_function(item, module)
                class_info.methods.append(method_info)
        
        return class_info
    
    def get_testable_items(self, module_info: ModuleInfo) -> dict:
        """获取可测试的项目（排除私有和特殊方法）"""
        testable = {
            'functions': [],
            'classes': []
        }
        
        # 过滤函数
        for func in module_info.functions:
            if not func.name.startswith('_'):
                testable['functions'].append(func)
        
        # 过滤类和方法
        for cls in module_info.classes:
            if not cls.name.startswith('_'):
                public_methods = [m for m in cls.methods if not m.name.startswith('_') or m.name.startswith('__')]
                if public_methods:
                    cls_copy = ClassInfo(
                        name=cls.name,
                        module=cls.module,
                        lineno=cls.lineno,
                        bases=cls.bases,
                        methods=public_methods,
                        docstring=cls.docstring
                    )
                    testable['classes'].append(cls_copy)
        
        return testable
