"""Heidenhain TNC CNC控制器后处理器。

实现Heidenhain TNC控制器特有的代码方言，包括：
- TOOL CALL刀具调用语法
- L（顺时针）/ CC（逆时针）圆弧插补
- CYCL DEF 200/203钻孔循环定义
- CYCL DEF 206攻丝循环
- CYCL DEF 202/209镗孔循环
- CYCL DEF 264螺纹加工循环
- LBL CALL/LBL 0标签子程序支持
- 符合Heidenhain独特的程序结构和指令格式

本模块为门面：实现已拆分至 _heidenhain_core_mixin / _heidenhain_cycles_mixin。
"""

from __future__ import annotations

import logging
from typing import Any

from app.postprocessor.base import BasePostProcessor
from app.postprocessor._heidenhain_core_mixin import _HeidenhainCoreMixin
from app.postprocessor._heidenhain_cycles_mixin import _HeidenhainCyclesMixin

logger = logging.getLogger(__name__)


class HeidenhainPostProcessor(_HeidenhainCoreMixin, _HeidenhainCyclesMixin, BasePostProcessor):
    """Heidenhain TNC CNC控制器后处理器。

    生成符合Heidenhain TNC语法规范的程序代码。
    适配Heidenhain专用固定循环定义及子程序LBL CALL格式。
    """

    def __init__(
        self,
        decimal_places: int = 3,
        safe_z_height: float = 80.0,
        rapid_feed: float = 10000,
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(decimal_places, safe_z_height, rapid_feed, config)
        self._block_counter = 0
        self._last_program_number = 1  # 与 format_header 默认值一致

    def _next_block(self) -> int:
        self._block_counter += 1
        return self._block_counter
