"""
自动化测试生成工具

基于AST分析自动生成单元测试和集成测试
支持一键生成和运行测试
"""

from .analyzer import CodeAnalyzer
from .generator import TestGenerator
from .runner import TestRunner

__version__ = "2.5.0"
__all__ = ["CodeAnalyzer", "TestGenerator", "TestRunner"]
