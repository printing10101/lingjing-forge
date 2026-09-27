"""DNC 传输核心实现

本模块提供 DNC (Direct Numerical Control) 传输功能，支持通过串口 (RS232) 和 TCP Socket
发送 G-code 到机床。支持 Fanuc、Siemens、Heidenhain 三种控制器类型。

主要功能:
- 串口传输: 使用 pyserial 库（可选依赖）
- TCP 传输: 使用标准库 socket
- 控制器协议适配: 根据不同控制器类型添加相应的头部和尾部标记
"""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from collections.abc import Callable

# pyserial 是可选依赖，只在需要串口时导入
try:
    import serial

    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

logger = logging.getLogger(__name__)


class DNCStatus(Enum):
    """DNC 传输状态枚举"""

    IDLE = "idle"
    CONNECTING = "connecting"
    TRANSFERRING = "transferring"
    COMPLETE = "complete"
    ERROR = "error"


class ControllerType(Enum):
    """CNC 控制器类型"""

    FANUC = "fanuc"
    SIEMENS = "siemens"
    HEIDENHAIN = "heidenhain"


class Protocol(Enum):
    """传输协议类型"""

    SERIAL = "serial"
    TCP = "tcp"


@dataclass
class DNCConfig:
    """DNC 传输配置"""

    controller_type: ControllerType = ControllerType.FANUC
    protocol: Protocol = Protocol.TCP
    host: str = "localhost"
    port: int = 8193  # Fanuc 默认端口
    baud_rate: int = 9600
    timeout: float = 30.0  # 传输超时（秒）
    chunk_size: int = 1024  # 每次发送的字节数
    tcp_send_delay: float = 0.01  # TCP 发送间隔（秒），避免机床缓冲区溢出
    serial_send_delay: float = 0.05  # 串口发送间隔（秒），等待机床处理
    # 串口高级配置
    flow_control: str = "xonxoff"  # 流控模式: "xonxoff"(软件流控), "rtscts"(硬件流控), "none"(无流控)
    data_bits: int = 8  # 数据位: 5, 6, 7, 8
    parity: str = "N"  # 校验位: "N"(无), "E"(偶), "O"(奇), "M"(标记), "S"(空格)
    stop_bits: int = 1  # 停止位: 1, 1.5, 2


@dataclass
class DNCTarget:
    """DNC 传输目标"""

    host: str
    port: int
    protocol: Protocol
    controller_type: ControllerType
    baud_rate: int = 9600


@dataclass
class DNCResult:
    """DNC 传输结果"""

    success: bool
    bytes_sent: int
    duration_seconds: float
    error_message: str | None = None


class DNCTransfer:
    """DNC 传输核心类

    支持通过串口和 TCP 发送 G-code 到机床。

    示例::

        transfer = DNCTransfer()
        target = DNCTarget(
            host="192.168.1.100",
            port=8193,
            protocol=Protocol.TCP,
            controller_type=ControllerType.FANUC
        )
        result = transfer.send_file(Path("program.nc"), target)
    """

    def __init__(self, config: DNCConfig | None = None) -> None:
        """初始化 DNC 传输模块

        Args:
            config: DNC 配置，如果未提供则使用默认配置
        """
        self.config = config or DNCConfig()
        self._status = DNCStatus.IDLE
        self._socket: socket.socket | None = None
        self._serial: "serial.Serial" | None = None

    @property
    def status(self) -> DNCStatus:
        """获取当前传输状态"""
        return self._status

    def send_gcode(
        self,
        gcode: str,
        target: DNCTarget,
        on_progress: Callable[[int, int, float], None] | None = None,
    ) -> DNCResult:
        """发送 G-code 字符串到目标机床

        Args:
            gcode: G-code 内容字符串
            target: 传输目标配置
            on_progress: 进度回调函数，签名为 (bytes_sent, total_bytes, progress_pct)
                - bytes_sent: 已发送字节数
                - total_bytes: 总字节数
                - progress_pct: 进度百分比 (0.0 ~ 100.0)

        Returns:
            DNCResult: 传输结果
        """
        start_time = time.time()
        bytes_sent = 0

        try:
            self._status = DNCStatus.CONNECTING
            logger.info("开始连接到 %s:%s (协议: %s)", target.host, target.port, target.protocol.value)

            # 建立连接
            if target.protocol == Protocol.TCP:
                self._connect_tcp(target)
            elif target.protocol == Protocol.SERIAL:
                self._connect_serial(target)
            else:
                raise ValueError(f"不支持的协议: {target.protocol}")

            self._status = DNCStatus.TRANSFERRING
            logger.info("连接成功，开始传输 G-code")

            # 准备 G-code（添加控制器特定的头部和尾部）
            prepared_gcode = self._prepare_gcode(gcode, target.controller_type)
            data_bytes = prepared_gcode.encode("utf-8")
            total_bytes = len(data_bytes)

            # 发送数据（带进度回调）
            if target.protocol == Protocol.TCP:
                bytes_sent = self._send_tcp(data_bytes, on_progress, total_bytes)
            else:
                bytes_sent = self._send_serial(data_bytes, on_progress, total_bytes)

            self._status = DNCStatus.COMPLETE
            duration = time.time() - start_time
            logger.info("传输完成: %d 字节, 耗时 %.2f 秒", bytes_sent, duration)

            return DNCResult(success=True, bytes_sent=bytes_sent, duration_seconds=duration)

        except Exception as e:
            self._status = DNCStatus.ERROR
            duration = time.time() - start_time
            error_msg = "传输失败: 连接异常或发送中断，请检查机床连接"
            logger.error("传输失败: %s", e, exc_info=True)

            return DNCResult(success=False, bytes_sent=bytes_sent, duration_seconds=duration, error_message=error_msg)
        finally:
            self._disconnect()

    def send_file(self, file_path: Path, target: DNCTarget) -> DNCResult:
        """发送 G-code 文件到目标机床

        Args:
            file_path: G-code 文件路径
            target: 传输目标配置

        Returns:
            DNCResult: 传输结果
        """
        logger.info("读取文件: %s", file_path)

        if not file_path.exists():
            return DNCResult(
                success=False, bytes_sent=0, duration_seconds=0.0, error_message=f"文件不存在: {file_path}"
            )

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                gcode = f.read()
            return self.send_gcode(gcode, target)
        except UnicodeDecodeError:
            # 尝试使用其他编码
            with open(file_path, "r", encoding="gbk") as f:
                gcode = f.read()
            return self.send_gcode(gcode, target)
        except Exception as e:
            logger.error("读取文件失败: %s", e, exc_info=True)
            return DNCResult(
                success=False, bytes_sent=0, duration_seconds=0.0, error_message="读取文件失败: 文件不可读或权限不足"
            )

    def test_connection(self, target: DNCTarget) -> bool:
        """测试与目标机床的连接

        Args:
            target: 传输目标配置

        Returns:
            bool: 连接是否成功
        """
        try:
            self._status = DNCStatus.CONNECTING
            logger.info("测试连接到 %s:%s", target.host, target.port)

            if target.protocol == Protocol.TCP:
                self._connect_tcp(target)
            elif target.protocol == Protocol.SERIAL:
                self._connect_serial(target)
            else:
                raise ValueError(f"不支持的协议: {target.protocol}")

            self._disconnect()
            self._status = DNCStatus.IDLE
            logger.info("连接测试成功")
            return True

        except Exception as e:
            self._status = DNCStatus.ERROR
            logger.error("连接测试失败: %s", str(e))
            self._disconnect()
            return False

    def _connect_tcp(self, target: DNCTarget) -> None:
        """建立 TCP 连接"""
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(self.config.timeout)
            self._socket.connect((target.host, target.port))
            logger.debug("TCP 连接已建立")
        except socket.error as e:
            logger.warning("TCP connect failed: %s", e)
            raise ConnectionError("TCP 连接失败: 无法连接到目标地址或网络不通") from e

    def _connect_serial(self, target: DNCTarget) -> None:
        """建立串口连接"""
        if not SERIAL_AVAILABLE:
            raise RuntimeError("pyserial 未安装。请运行: pip install pyserial")

        try:
            # 解析数据位
            data_bits_map = {5: serial.FIVEBITS, 6: serial.SIXBITS, 7: serial.SEVENBITS, 8: serial.EIGHTBITS}
            data_bits = data_bits_map.get(self.config.data_bits, serial.EIGHTBITS)

            # 解析校验位
            parity_map = {
                "N": serial.PARITY_NONE,
                "E": serial.PARITY_EVEN,
                "O": serial.PARITY_ODD,
                "M": serial.PARITY_MARK,
                "S": serial.PARITY_SPACE,
            }
            parity = parity_map.get(self.config.parity.upper(), serial.PARITY_NONE)

            # 解析停止位
            stop_bits_map = {1: serial.STOPBITS_ONE, 1.5: serial.STOPBITS_ONE_POINT_FIVE, 2: serial.STOPBITS_TWO}
            stop_bits = stop_bits_map.get(self.config.stop_bits, serial.STOPBITS_ONE)

            # 根据流控模式配置
            flow = self.config.flow_control.lower()
            xonxoff = flow == "xonxoff"
            rtscts = flow == "rtscts"
            dsrdtr = flow == "dsrdtr"

            # 对于串口，host 应该是串口名称（如 COM1 或 /dev/ttyUSB0）
            self._serial = serial.Serial(
                port=target.host,
                baudrate=target.baud_rate,
                timeout=self.config.timeout,
                bytesize=data_bits,
                parity=parity,
                stopbits=stop_bits,
                xonxoff=xonxoff,
                rtscts=rtscts,
                dsrdtr=dsrdtr,
            )
            logger.debug(
                "串口连接已建立: %s @ %d (流控: %s, %d%s%s)",
                target.host,
                target.baud_rate,
                self.config.flow_control,
                self.config.data_bits,
                self.config.parity,
                self.config.stop_bits,
            )
        except serial.SerialException as e:
            logger.warning("Serial connect failed: %s", e)
            raise ConnectionError("串口连接失败: 端口不可用、被占用或参数错误") from e

    def _send_tcp(
        self,
        data: bytes,
        on_progress: Callable[[int, int, float], None] | None = None,
        total_bytes: int | None = None,
    ) -> int:
        """通过 TCP 发送数据

        .. note::
            仅同步上下文使用：本方法使用 ``time.sleep`` 控制发送节流，
            不应在 async 上下文中直接调用。如需 async 支持，请使用
            ``asyncio.to_thread`` 包装或在调用方协程中 ``await asyncio.sleep``。

        Args:
            data: 待发送的数据
            on_progress: 进度回调函数
            total_bytes: 总字节数（用于计算进度百分比）
        """
        if not self._socket:
            raise RuntimeError("TCP 连接未建立")

        total_sent = 0
        data_len = len(data)
        report_total = total_bytes if total_bytes is not None else data_len

        while total_sent < data_len:
            chunk = data[total_sent : total_sent + self.config.chunk_size]
            sent = self._socket.send(chunk)
            if sent == 0:
                raise ConnectionError("TCP 连接中断")
            total_sent += sent
            logger.debug("已发送 %d/%d 字节", total_sent, data_len)

            # 触发进度回调
            if on_progress is not None:
                progress_pct = (total_sent / report_total * 100.0) if report_total > 0 else 0.0
                try:
                    on_progress(total_sent, report_total, progress_pct)
                except Exception as cb_err:
                    logger.warning("进度回调执行失败: %s", cb_err)

            # 使用配置的延迟时间，避免机床缓冲区溢出
            if self.config.tcp_send_delay > 0:
                time.sleep(self.config.tcp_send_delay)

        return total_sent

    def _send_serial(
        self,
        data: bytes,
        on_progress: Callable[[int, int, float], None] | None = None,
        total_bytes: int | None = None,
    ) -> int:
        """通过串口发送数据

        .. note::
            仅同步上下文使用：本方法使用 ``time.sleep`` 控制发送节流，
            不应在 async 上下文中直接调用。如需 async 支持，请使用
            ``asyncio.to_thread`` 包装或在调用方协程中 ``await asyncio.sleep``。

        Args:
            data: 待发送的数据
            on_progress: 进度回调函数
            total_bytes: 总字节数（用于计算进度百分比）
        """
        if not self._serial:
            raise RuntimeError("串口连接未建立")

        total_sent = 0
        data_len = len(data)
        report_total = total_bytes if total_bytes is not None else data_len

        while total_sent < data_len:
            chunk = data[total_sent : total_sent + self.config.chunk_size]
            sent = self._serial.write(chunk)
            total_sent += sent
            logger.debug("已发送 %d/%d 字节", total_sent, data_len)

            # 触发进度回调
            if on_progress is not None:
                progress_pct = (total_sent / report_total * 100.0) if report_total > 0 else 0.0
                try:
                    on_progress(total_sent, report_total, progress_pct)
                except Exception as cb_err:
                    logger.warning("进度回调执行失败: %s", cb_err)

            # 使用配置的延迟时间，等待机床处理
            if self.config.serial_send_delay > 0:
                time.sleep(self.config.serial_send_delay)

        return total_sent

    def _disconnect(self) -> None:
        """断开所有连接"""
        if self._socket:
            try:
                self._socket.close()
                logger.debug("TCP 连接已关闭")
            except Exception as e:
                logger.warning("关闭 TCP 连接时出错: %s", e)
            finally:
                self._socket = None

        if self._serial:
            try:
                self._serial.close()
                logger.debug("串口连接已关闭")
            except Exception as e:
                logger.warning("关闭串口连接时出错: %s", e)
            finally:
                self._serial = None

        self._status = DNCStatus.IDLE

    def _prepare_gcode(self, gcode: str, controller_type: ControllerType) -> str:
        """根据控制器类型准备 G-code

        添加控制器特定的头部和尾部标记。

        Args:
            gcode: 原始 G-code 内容
            controller_type: 控制器类型

        Returns:
            str: 处理后的 G-code
        """
        # 清理输入
        gcode = gcode.strip()

        if controller_type == ControllerType.FANUC:
            # Fanuc 使用 % 开始和结束
            if not gcode.startswith("%"):
                gcode = "%" + gcode
            if not gcode.endswith("%"):
                gcode = gcode + "%"
            # 确保以换行符结束
            if not gcode.endswith("\n"):
                gcode += "\n"

        elif controller_type == ControllerType.SIEMENS:
            # Siemens 使用 % 开始和结束
            if not gcode.startswith("%"):
                gcode = "%" + gcode
            if not gcode.endswith("%"):
                gcode = gcode + "%"
            if not gcode.endswith("\n"):
                gcode += "\n"

        elif controller_type == ControllerType.HEIDENHAIN:
            # Heidenhain 使用 BEGIN PGM 和 END PGM
            if not gcode.startswith("BEGIN PGM"):
                gcode = "BEGIN PGM\n" + gcode
            if not gcode.endswith("END PGM"):
                if not gcode.endswith("\n"):
                    gcode += "\n"
                gcode += "END PGM\n"

        return gcode


__all__ = [
    "DNCTransfer",
    "DNCConfig",
    "DNCStatus",
    "DNCTarget",
    "DNCResult",
    "ControllerType",
    "Protocol",
]
