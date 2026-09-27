"""后处理器格式化 mixin（从 base 拆出）。"""

from __future__ import annotations

import datetime
import logging

from collections.abc import Callable

logger = logging.getLogger(__name__)


class _FormatMixin:
    # 宿主契约：由主类提供（mypy 需要显式声明）
    decimal_places: int
    get_feed_rate: Callable[..., float]

    def _fmt(self, value: float) -> str:
        """将数值格式化为指定小数位数的字符串。"""
        return f"{value:.{self.decimal_places}f}"

    def _comment(self, text: str) -> str:
        """生成 G-code 注释行。

        Args:
            text: 注释文本

        Returns:
            格式化后的注释字符串
        """
        return f"; {text}"

    def _paren_comment(self, text: str) -> str:
        """生成括号注释行（Fanuc 字地址族），并净化文本内嵌的括号。

        Fanuc 族控制器的注释以第一个 ``)`` 结束且不支持嵌套：注释文本中
        出现内层括号（如控制器名 "(Guangzhou CNC)"）会导致注释被提前
        终止，剩余文本被当作代码解释从而触发报警。因此注释文本中的
        括号统一替换为空格。
        """
        sanitized = text.replace("(", " ").replace(")", " ")
        return f"({sanitized})"

    @staticmethod
    def _calc_arc_radius(
        end: tuple[float, float, float],
        center: tuple[float, float, float],
    ) -> float:
        """计算圆弧半径 sqrt((ex-cx)² + (ey-cy)²)。"""
        return ((end[0] - center[0]) ** 2 + (end[1] - center[1]) ** 2) ** 0.5

    @staticmethod
    def _format_coolant(state: str) -> str:
        """统一冷却液格式化。

        Args:
            state: 冷却液状态，"on"开启，"off"关闭，"fog"雾冷

        Returns:
            "on" -> "M08", "fog" -> "M07", "off" -> "M09", 否则返回空字符串
        """
        state_lower = state.lower()
        if state_lower == "on":
            return "M08"
        if state_lower == "fog":
            return "M07"
        if state_lower == "off":
            return "M09"
        return ""

    def format_coolant(self, state: str) -> str:
        """生成冷却液控制指令。

        默认实现使用 ``_format_coolant`` 静态方法，支持 "on"/"off"/"fog"。
        子类如需添加行号前缀等控制器特定格式，可覆盖此方法。

        Args:
            state: 冷却液状态，"on"开启，"off"关闭，"fog"雾冷

        Returns:
            冷却液控制NC代码字符串（默认 "M08"/"M07"/"M09"）
        """
        return self._format_coolant(state) or "M09"

    def format_optional_stop(self) -> str:
        """生成可选停止指令 (M01)。

        用于单步执行模式，机床在遇到 M01 时会暂停等待操作员确认。
        常用于关键工序后的检查点。

        Returns:
            可选停止NC代码字符串 "M01"
        """
        return "M01"

    def format_program_stop(self) -> str:
        """生成程序停止指令 (M00)。

        无条件停止指令，机床遇到 M00 时会完全停止，
        需要操作员手动按循环启动才能继续。

        Returns:
            程序停止NC代码字符串 "M00"
        """
        return "M00"

    # 通用直线/快速移动与 RTCP 接口
    # 背景：gcode_generator 在三轴与五轴模式下均会调用 format_linear_move，
    # 因此必须在基类提供默认实现；format_rapid_move / format_rtcp_on/off
    # 仅在五轴模式下调用（已用 hasattr 保护），仍提供默认实现以便子类按需覆盖。

    def format_linear_move(
        self,
        x: float,
        y: float,
        z: float,
        feed: float | None = None,
        a: float | None = None,
        c: float | None = None,
    ) -> str:
        """生成直线插补指令（G01）。

        默认实现输出 Fanuc 风格的 G01 X Y Z F 代码，子类可覆盖以适配
        控制器特定语法（如 Heidenhain 的 L X+ Y+ Z+ F）。

        Args:
            x: X轴坐标
            y: Y轴坐标
            z: Z轴坐标
            feed: 进给速度 (mm/min)，None使用默认值
            a: A轴角度（可选，度）
            c: C轴角度（可选，度）

        Returns:
            直线插补NC代码字符串
        """
        feed_val = self.get_feed_rate(feed) if feed is not None else self.get_feed_rate()
        line = f"G01 X{self._fmt(x)} Y{self._fmt(y)} Z{self._fmt(z)} F{self._fmt(feed_val)}"
        if a is not None:
            line += f" A{self._fmt(a)}"
        if c is not None:
            line += f" C{self._fmt(c)}"
        return line

    def format_rapid_move(
        self,
        x: float,
        y: float,
        z: float,
        a: float | None = None,
        c: float | None = None,
    ) -> str:
        """生成快速定位指令（G00）。

        默认实现输出 G00 X Y Z 代码，子类可覆盖。

        Args:
            x: X轴坐标
            y: Y轴坐标
            z: Z轴坐标
            a: A轴角度（可选，度）
            c: C轴角度（可选，度）

        Returns:
            快速定位NC代码字符串
        """
        line = f"G00 X{self._fmt(x)} Y{self._fmt(y)} Z{self._fmt(z)}"
        if a is not None:
            line += f" A{self._fmt(a)}"
        if c is not None:
            line += f" C{self._fmt(c)}"
        return line

    def format_rtcp_on(self, tool_length: float = 0.0) -> str:
        """开启 RTCP（旋转刀具中心点）补偿模式。

        默认实现使用 Fanuc 风格的 G43.4 指令，子类可覆盖以适配
        其他控制器（如 Siemens TRAORI / Heidenhain M128）。

        Args:
            tool_length: 刀具长度 (mm)

        Returns:
            RTCP开启NC代码字符串
        """
        return f"G43.4 H{int(tool_length):02d}"

    def format_rtcp_off(self) -> str:
        """关闭 RTCP 补偿模式。

        默认实现使用 G49 指令，子类可覆盖。

        Returns:
            RTCP关闭NC代码字符串
        """
        return "G49"

    @staticmethod
    def _date_string() -> str:
        """获取当前日期字符串，用于程序头注释。"""
        return datetime.date.today().strftime("%Y-%m-%d")
