"""字典工具函数。

跨模块通用的字典操作，避免 ``deep_merge`` 等函数在
postprocessor / lnn config / templates 等处各自实现导致的行为漂移。
"""

from __future__ import annotations

import copy
from typing import Any


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """深度合并两个字典，override 中的值覆盖 base 中的同名键。

    嵌套字典递归合并，非字典值直接覆盖。不修改传入的字典；
    返回值与两个输入完全隔离（嵌套结构 deepcopy），调用方可安全
    原地修改返回值而不影响入参。

    Args:
        base: 基础配置字典
        override: 覆盖配置字典

    Returns:
        合并后的新字典
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
