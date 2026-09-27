"""研究模块的 Problem Registry。

研究模块从这里读取产品中遇到的问题，按优先级排 backlog。
"""
from .registry import ProblemRegistry, Problem, ProblemStatus, ProblemPriority

__all__ = ["ProblemRegistry", "Problem", "ProblemStatus", "ProblemPriority"]
