"""测试用方言 hooks 模块。

模拟「模板表达不了的复杂逻辑」——例如 Siemens 风格固定循环、
Heidenhain 风格探针等。hooks 类的方法以方言实例为 self 调用，
可访问 _fmt / get_spindle_rpm / get_cycle_config 等处理器状态。
"""

from __future__ import annotations



class CustomCycleHooks:
    """自定义固定循环 hooks：演示 hooks 覆盖基类/模板方法。

    实现了一个特殊的钻孔循环格式（模板难以表达的任意逻辑），
    以及一个仅 hooks 提供的自定义方法（format_special_cycle）。
    """

    def format_cycle_drill(
        self,
        x: float,
        y: float,
        z: float,
        depth: float,
        dwell: float = 0.0,
        pecking: bool = True,
    ) -> str:
        """自定义钻孔循环：CUSTOM CYCLE 前缀（模拟特殊控制器逻辑）。"""
        cfg = self.get_cycle_config("drilling", "G83")
        peck_depth = cfg.get("peck_depth", 5.0)
        feed = self._fmt(self.get_feed_rate(self.rapid_feed * 0.3))
        z_out = self._fmt(-abs(depth))
        return (
            f"CUSTOM CYCLE X{self._fmt(x)} Y{self._fmt(y)} Z{z_out} "
            f"Q{self._fmt(peck_depth)} F{feed}"
        )

    def format_special_cycle(self, value: float = 0.0) -> str:
        """仅 hooks 提供的扩展方法（不在基类 MRO 中）。"""
        return f"SPECIAL {self._fmt(value)}"


class ProbeHooks:
    """探针 hooks：模拟 Heidenhain 风格测头循环。"""

    def format_probe(self, probe_number: int = 1, x_pos: float = 0.0) -> str:
        return f"PROBE {probe_number} X{self._fmt(x_pos)}"


__all__ = ["CustomCycleHooks", "ProbeHooks"]
