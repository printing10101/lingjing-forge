"""CNC后处理器抽象基类——定义统一的G代码生成接口规范。

所有控制器专用后处理器必须继承此基类并实现全部抽象方法，
确保上层调用方无需关注控制器差异。

本模块为门面：实现已拆分至 _config_mixin / _format_mixin。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from app.postprocessor.config_loader import ConfigLimiter, create_limiter
from app.postprocessor._config_mixin import _ConfigMixin
from app.postprocessor._format_mixin import _FormatMixin

logger = logging.getLogger(__name__)


class BasePostProcessor(_ConfigMixin, _FormatMixin, ABC):
    """CNC后处理器抽象基类。

    为不同CNC控制器提供统一的NC代码生成接口。
    子类需实现所有抽象方法以适配特定控制器的G代码方言。

    Attributes:
        decimal_places: 数值保留的小数位数
        safe_z_height: 安全Z轴高度(mm)
        rapid_feed: 快速进给速度(mm/min)
        config: 完整的合并后配置字典
        limiter: 主轴/进给速度限幅器
        _spindle_min_rpm: 主轴最小转速
        _spindle_max_rpm: 主轴最大转速
        _spindle_default_rpm: 主轴默认转速
        _feed_min_rate: 进给最小速度
        _feed_max_rate: 进给最大速度
        _feed_default_rate: 进给默认速度
    """

    def __init__(
        self,
        decimal_places: int = 3,
        safe_z_height: float = 80.0,
        rapid_feed: float = 10000,
        config: dict[str, Any] | None = None,
    ) -> None:
        if decimal_places < 0:
            raise ValueError(f"decimal_places must be >= 0, got {decimal_places}")
        if safe_z_height <= 0:
            raise ValueError(f"safe_z_height must be > 0, got {safe_z_height}")
        if rapid_feed <= 0:
            raise ValueError(f"rapid_feed must be > 0, got {rapid_feed}")
        self.decimal_places = decimal_places
        self.safe_z_height = safe_z_height
        self.rapid_feed = rapid_feed

        self.config = config or {}
        self.limiter: ConfigLimiter | None = None
        # Always initialize defaults so subclasses can rely on these
        # attributes being present regardless of whether a config dict
        # is supplied.
        self._spindle_min_rpm = 50
        self._spindle_max_rpm = 24000
        self._spindle_default_rpm = 1000
        self._feed_min_rate = 10.0
        self._feed_max_rate = 20000.0
        self._feed_default_rate = 1000.0
        self._work_coordinates: dict[str, dict[str, Any]] = {
            cs: {} for cs in ("G54", "G55", "G56", "G57", "G58", "G59")
        }
        self._default_coordinate_system = "G54"
        if self.config:
            # 在使用配置前进行验证和补全
            self._validate_and_complete_config()
            self.limiter = create_limiter(self.config)
            self._init_from_config()

    @abstractmethod
    def format_header(self, program_number: int = 1) -> str:
        """生成控制器特定的程序头。

        包含程序号、日期、安全启动指令（如取消补偿、回参考点等）。

        Args:
            program_number: 程序号，默认1

        Returns:
            程序头NC代码字符串
        """

    @abstractmethod
    def format_tool_change(
        self,
        tool_id: int,
        length_comp: float = 0.0,
        radius_comp: float = 0.0,
    ) -> str:
        """生成换刀指令。

        Args:
            tool_id: 刀具ID/编号
            length_comp: 刀具长度补偿值
            radius_comp: 刀具半径补偿值

        Returns:
            换刀NC代码字符串
        """

    @abstractmethod
    def format_arc(
        self,
        start: tuple[float, float, float],
        end: tuple[float, float, float],
        center: tuple[float, float, float],
        clockwise: bool = True,
    ) -> str:
        """生成圆弧插补指令。

        Args:
            start: 起点坐标 (x, y, z)
            end: 终点坐标 (x, y, z)
            center: 圆心坐标 (x, y, z)
            clockwise: True为顺时针(G02)，False为逆时针(G03)

        Returns:
            圆弧插补NC代码字符串
        """

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

    @abstractmethod
    def format_tool_compensation(
        self,
        length_offset: int = 0,
        radius_offset: int = 0,
    ) -> str:
        """生成刀具长度和半径补偿指令。

        Args:
            length_offset: 长度补偿寄存器号
            radius_offset: 半径补偿寄存器号

        Returns:
            刀具补偿NC代码字符串
        """

    @abstractmethod
    def format_cycle_drill(
        self,
        x: float,
        y: float,
        z: float,
        depth: float,
        dwell: float = 0.0,
        pecking: bool = True,
    ) -> str:
        """生成钻孔固定循环指令。

        Args:
            x: 孔位X坐标
            y: 孔位Y坐标
            z: 孔位Z坐标（起始高度）
            depth: 钻孔深度
            dwell: 孔底暂停时间(秒)
            pecking: 是否啄钻，True用G83，False用G81

        Returns:
            钻孔循环NC代码字符串
        """

    @abstractmethod
    def format_cycle_tapping(
        self,
        x: float,
        y: float,
        z: float,
        depth: float,
        pitch: float = 1.0,
        spindle_rpm: float | None = None,
    ) -> str:
        """生成攻丝固定循环指令（G84）。

        Args:
            x: 孔位X坐标
            y: 孔位Y坐标
            z: 孔位Z坐标（起始高度）
            depth: 攻丝深度
            pitch: 螺距 (mm)
            spindle_rpm: 主轴转速，None使用默认值

        Returns:
            攻丝循环NC代码字符串
        """

    @abstractmethod
    def format_cycle_boring(
        self,
        x: float,
        y: float,
        z: float,
        depth: float,
        cycle_type: str = "G86",
        dwell: float = 0.5,
    ) -> str:
        """生成镗孔固定循环指令（G86/G89）。

        Args:
            x: 孔位X坐标
            y: 孔位Y坐标
            z: 孔位Z坐标（起始高度）
            depth: 镗孔深度
            cycle_type: 循环类型 "G86"（粗镗）或 "G89"（精镗）
            dwell: 孔底暂停时间(秒)

        Returns:
            镗孔循环NC代码字符串
        """

    @abstractmethod
    def format_cycle_threading(
        self,
        x: float,
        y: float,
        depth: float,
        lead: float = 1.0,
        passes: int | None = None,
        depth_cut_first: float | None = None,
        depth_cut_last: float | None = None,
        finishing_passes: int | None = None,
        tool_angle: float | None = None,
        taper: float | None = None,
    ) -> str:
        """生成螺纹加工固定循环指令（G76）。

        Args:
            x: 孔位X坐标
            y: 孔位Y坐标
            depth: 螺纹深度
            lead: 螺纹导程 (mm)
            passes: 切削次数，None使用配置默认值
            depth_cut_first: 第一次切削深度，None使用配置默认值
            depth_cut_last: 最后一次切削深度，None使用配置默认值
            finishing_passes: 精加工次数，None使用配置默认值
            tool_angle: 刀尖角度，None使用配置默认值
            taper: 锥度角，None使用配置默认值

        Returns:
            螺纹加工循环NC代码字符串
        """

    @abstractmethod
    def format_subprogram_call(
        self,
        program_number: int,
        repeat: int = 1,
    ) -> str:
        """生成子程序调用指令。

        Args:
            program_number: 子程序号
            repeat: 重复调用次数

        Returns:
            子程序调用NC代码字符串
        """

    @abstractmethod
    def format_subprogram_end(
        self,
        return_value: str | None = None,
    ) -> str:
        """生成子程序结束指令。

        Args:
            return_value: 可选的返回参数

        Returns:
            子程序结束NC代码字符串
        """

    @abstractmethod
    def format_footer(self) -> str:
        """生成程序结束部分。

        包含主轴停止、冷却液关闭、回参考点、程序结束等指令。

        Returns:
            程序结束NC代码字符串
        """
