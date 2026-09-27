"""Siemens 840D hooks — 用「模板 + hooks」声明化表达复杂固定循环。

Siemens 840D 与 Fanuc 兼容方言的关键差异：
1. 钻孔用 CYCLE81（简单钻）/ CYCLE83（深孔钻）参数化固定循环，
   而非 G81/G83 模态指令；
2. 攻丝用 CYCLE84（刚性攻丝）/ CYCLE840；
3. 镗孔用 CYCLE85/CYCLE86；
4. 换刀用 T 指令 + D 补偿号（而非 Fanuc 的 H 补偿号）；
5. 程序头/尾格式不同（%_N_..._MPF 或简化的 % 起始）。

hooks 方法以方言实例为 self，可访问：
- ``self._fmt(value)`` — 按 decimal_places 格式化数值
- ``self.get_spindle_rpm()`` — 主轴转速
- ``self.get_feed_rate(rpm)`` — 由 RPM 换算进给
- ``self.get_cycle_config(name, default_code)`` — 循环配置
- ``self.rapid_feed`` / ``self.safe_z_height`` — 构造参数
"""

from __future__ import annotations


class Siemens840DHooks:
    """Siemens 840D 固定循环 hooks（CYCLE 系列）。"""

    def format_cycle_drill(
        self,
        x: float,
        y: float,
        z: float,
        depth: float,
        dwell: float = 0.0,
        pecking: bool = True,
    ) -> str:
        """钻孔循环：深孔用 CYCLE83，浅孔用 CYCLE81。

        对应 Fanuc 的 G83（啄钻）/ G81（普通钻）语义。
        """
        cfg = self.get_cycle_config("drilling", "CYCLE81")
        retract = cfg.get("retract", 2.0)  # 回退量 (mm)
        z_start = self._fmt(z)
        z_end = self._fmt(-abs(depth))
        if pecking:
            peck_depth = cfg.get("peck_depth", 5.0)
            return f"CYCLE83({z_start}, {self._fmt(retract)}, {self._fmt(peck_depth)}, {z_end}, 0.0, 0.0, 1, 0, 1, 1)"
        return f"CYCLE81({z_start}, {self._fmt(retract)}, {z_end})"

    def format_cycle_tapping(
        self,
        x: float,
        y: float,
        z: float,
        depth: float,
        pitch: float = 1.0,
        spindle_rpm: float | None = None,
    ) -> str:
        """刚性攻丝循环 CYCLE84。

        对应 Fanuc 的 G84 刚性攻丝。Siemens 需要主轴转速与螺距
        （由 get_feed_rate 推导进给）。
        """
        cfg = self.get_cycle_config("tapping", "CYCLE84")
        retract = cfg.get("retract", 2.0)
        rpm = spindle_rpm if spindle_rpm is not None else self.get_spindle_rpm()
        feed = self.get_feed_rate(rpm)
        z_start = self._fmt(z)
        z_end = self._fmt(-abs(depth))
        return f"CYCLE84({z_start}, {self._fmt(retract)}, {z_end}, {self._fmt(feed)}, {int(rpm)}, 3)"

    def format_cycle_boring(
        self,
        x: float,
        y: float,
        z: float,
        depth: float,
        cycle_type: str = "G85",
        dwell: float = 0.0,
    ) -> str:
        """镗孔循环 CYCLE85（进给进/进给出）。

        对应 Fanuc 的 G85 镗孔。
        """
        cfg = self.get_cycle_config("boring", "CYCLE85")
        retract = cfg.get("retract", 2.0)
        z_start = self._fmt(z)
        z_end = self._fmt(-abs(depth))
        return f"CYCLE85({z_start}, {self._fmt(retract)}, {z_end}, 0.0, 0.0)"

    def format_tool_change(
        self,
        tool_id: int,
        length_comp: float = 0.0,
        radius_comp: float = 0.0,
    ) -> str:
        """Siemens 换刀：T 指令 + D 补偿号（无 H 补偿）。

        参数名与基类 format_tool_change 调用约定对齐。
        """
        z_comp = self._fmt(length_comp) if length_comp else "D1"
        return f"T{int(tool_id):02d} {z_comp}"


class Siemens840DHeaderHooks:
    """Siemens 840D 程序头/尾 hooks。"""

    def format_header(self, program_number: int = 1) -> str:
        """Siemens 840D 程序头：TRAFO 关闭 + 绝对编程 + 米制（签名与基类约定对齐）。

        程序号四位补零嵌入 MPF 文件名（避免负数/超长破坏文件名）；
        ``;$PATH=`` 行是 Sinumerik 的程序路径元信息注释。
        """
        num = int(program_number)
        return f"%_N_{num:04d}_MPF\n;$PATH=/_N_MPF_DIR\nG90 G71 G94\nG17\nTRAFOF\nG0 X0 Y0 Z100\n"


__all__ = ["Siemens840DHooks", "Siemens840DHeaderHooks"]
