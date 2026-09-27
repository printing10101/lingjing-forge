"""本地 MTConnect 模拟 Agent（无真实机床时的联调验证基准）。

在无真实 CNC 机床的条件下，为「连接 → 采集 → 实时监控 → 落库 → 反馈」
整条链路提供符合 MTConnect 规范的本地端点，作为协议级联调与测试基准。
真实机床到位后仅需替换 agent_url 指向内网 Agent，上层代码无需改动。

端点（对齐 MTConnect 标准）：
    GET /probe     → MTConnectDevices（设备能力描述，含 Header 标识）
    GET /current   → MTConnectStreams（当前状态快照）
    GET /sample    → MTConnectStreams（动态样本，每次请求值有规律变化）

数据项（同时兼容两套项目内解析器）：
    1. ``app.integrations.mtconnect.parser.parse_sample_response``
       （adapter 采集管道）：SpindleSpeed / SpindleLoad / Feedrate / Execution
    2. ``app.dnc.mtconnect_client.MTConnectClient``（DNC 状态查询）：
       SpindleSpeed(ACTUAL) / PathFeedrate(ACTUAL) / Xabs / Yabs / Zabs /
       Execution / ControllerMode / Availability

实现约束
--------
* 仅依赖 Python 标准库（``http.server`` + ``xml.etree.ElementTree``），
  可独立于 FastAPI 启动，方便测试与无外部依赖环境联调。
* 模拟状态由 :class:`MachineSimulator` 维护：主轴转速 / 负载 / 进给 /
  三轴位置随 tick 有规律波动，执行状态周期切换 ACTIVE/IDLE，负载周期性
  超过 80% 触发主轴过载（供告警逻辑验证）。
* 线程安全：``ThreadingHTTPServer`` 每请求一线程，内部状态访问加锁。
"""

from __future__ import annotations

import logging
import threading
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from app.config.limits import DEFAULT_THREAD_JOIN_TIMEOUT_SEC

logger = logging.getLogger(__name__)

# MTConnect 命名空间（v1.5 为项目解析器默认兼容目标）
_NS_STREAMS = "urn:mtconnect.org:MTConnectStreams:1.5"
_NS_DEVICES = "urn:mtconnect.org:MTConnectDevices:1.5"

# 默认监听端口（避开 5000 MTConnect 常见端口 / 4840 OPC UA，降低本地冲突）
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5010

# 端口绑定失败（Windows 端口排除区 / 被占用）时的最大重试次数
_MAX_BIND_ATTEMPTS = 5

# 默认设备名（与 dnc 客户端默认 device_name 对齐）
DEFAULT_DEVICE_NAME = "VM-001"

# 主轴过载阈值（%）：负载超过该值触发主轴过载告警（对齐 streaming._check_alerts）
_SPINDLE_OVERLOAD_THRESHOLD = 80.0


def _now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串（MTConnect Header/DataItem 时间戳）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class MachineSimulator:
    """模拟机床状态机。

    每次 :meth:`next_sample` 递增 tick，并按周期性函数生成有规律变化的数据，
    便于端到端验证「数据随时间流动」的采集链路。
    """

    def __init__(
        self,
        device_name: str = DEFAULT_DEVICE_NAME,
        base_spindle_speed: float = 6000.0,
        base_spindle_load: float = 40.0,
        base_feedrate: float = 300.0,
        overload_cycle: int = 20,
    ) -> None:
        self.device_name = device_name
        self.uuid = str(uuid.uuid4())
        self._lock = threading.Lock()
        self._tick = 0
        self._sequence = 0
        self.base_spindle_speed = base_spindle_speed
        self.base_spindle_load = base_spindle_load
        self.base_feedrate = base_feedrate
        self.overload_cycle = overload_cycle

    def tick(self) -> int:
        """返回当前 tick（测试只读辅助）。"""
        with self._lock:
            return self._tick

    def next_sample(self) -> dict[str, Any]:
        """生成下一帧样本（线程安全）。

        Returns:
            dict：包含设备名、时间戳、主轴转速/负载/进给、三轴位置、执行状态、
            ControllerMode、Availability 以及原始 tick/sequence。
        """
        with self._lock:
            self._tick += 1
            self._sequence += 1
            t = self._tick
            seq = self._sequence

            # 有规律波动：主轴转速在基础值附近 ±5% 波动
            spindle_speed = round(self.base_spindle_speed * (1.0 + 0.05 * (t % 7 - 3) / 3.0), 1)
            # 负载：基础值 + 周期性过载（每 overload_cycle 帧短暂超过阈值）
            load = self.base_spindle_load + 8.0 * (t % 11 - 5) / 5.0
            if t % self.overload_cycle == 0:
                load = _SPINDLE_OVERLOAD_THRESHOLD + 12.0  # 触发过载告警
            spindle_load = round(max(0.0, load), 1)
            feedrate = round(self.base_feedrate * (1.0 + 0.1 * (t % 5 - 2) / 2.0), 1)
            # 三轴位置缓慢进给（模拟刀具移动）
            x_pos = round((t % 100) * 1.5, 2)
            y_pos = round((t % 50) * 0.8, 2)
            z_pos = round(50.0 - (t % 45) * 0.5, 2)
            # 执行状态周期切换：每 5 帧一次 IDLE
            execution = "IDLE" if t % 5 == 0 else "ACTIVE"
            controller_mode = "AUTOMATIC"

            return {
                "device_name": self.device_name,
                "uuid": self.uuid,
                "timestamp": _now_iso(),
                "tick": t,
                "sequence": seq,
                "spindle_speed": spindle_speed,
                "spindle_load": spindle_load,
                "feedrate": feedrate,
                "x_position": x_pos,
                "y_position": y_pos,
                "z_position": z_pos,
                "execution": execution,
                "controller_mode": controller_mode,
                "availability": "AVAILABLE",
            }


def build_devices_xml(device_name: str, device_uuid: str) -> str:
    """构造 MTConnectDevices 探测文档（``/probe`` 响应）。

    Returns:
        符合 MTConnect Devices 规范的 XML 字符串。
    """
    now = _now_iso()
    root = ET.Element(f"{{{_NS_DEVICES}}}MTConnectDevices")
    header = ET.SubElement(root, f"{{{_NS_DEVICES}}}Header")
    header.set("creationTime", now)
    header.set("instanceId", "1")
    header.set("sender", "mock-agent")
    header.set("version", "1.0.0")
    header.set("mtconnectVersion", "1.5")
    header.set("bufferSize", "1024")
    header.set("nextSequence", "1")

    devices = ET.SubElement(root, f"{{{_NS_DEVICES}}}Devices")
    device = ET.SubElement(devices, f"{{{_NS_DEVICES}}}Device")
    device.set("id", device_uuid)
    device.set("name", device_name)
    device.set("uuid", device_uuid)
    desc = ET.SubElement(device, f"{{{_NS_DEVICES}}}Description")
    desc.text = "Mock CNC Machine (local MTConnect simulator)"

    # 数据项声明（覆盖 DNC 状态查询所需字段）
    data_items = ET.SubElement(device, f"{{{_NS_DEVICES}}}DataItems")
    for item_id, item_type in (
        ("s1", "SPINDLE_SPEED"),
        ("s2", "SPINDLE_LOAD"),
        ("a1", "PATH_FEEDRATE"),
        ("a2", "LINEAR_FEEDRATE"),
        ("x1", "LINEAR_POSITION"),
        ("y1", "LINEAR_POSITION"),
        ("z1", "LINEAR_POSITION"),
        ("c1", "EXECUTION"),
        ("c2", "CONTROLLER_MODE"),
        ("c3", "AVAILABILITY"),
    ):
        di = ET.SubElement(data_items, f"{{{_NS_DEVICES}}}DataItem")
        di.set("id", item_id)
        di.set("type", item_type)
        di.set("category", "SAMPLE" if item_type not in ("EXECUTION", "CONTROLLER_MODE", "AVAILABILITY") else "EVENT")

    return ET.tostring(root, encoding="unicode")


def build_streams_xml(sample: dict[str, Any]) -> str:
    """构造 MTConnectStreams 文档（``/current`` 与 ``/sample`` 响应）。

    Args:
        sample: :class:`MachineSimulator.next_sample` 返回的样本字典。

    Returns:
        符合 MTConnect Streams 规范的 XML 字符串。
    """
    now = sample["timestamp"]
    root = ET.Element(f"{{{_NS_STREAMS}}}MTConnectStreams")
    header = ET.SubElement(root, f"{{{_NS_STREAMS}}}Header")
    header.set("creationTime", now)
    header.set("instanceId", "1")
    header.set("sender", "mock-agent")
    header.set("version", "1.0.0")
    header.set("bufferSize", "1024")
    header.set("nextSequence", str(sample["sequence"] + 1))

    streams = ET.SubElement(root, f"{{{_NS_STREAMS}}}Streams")
    device_stream = ET.SubElement(streams, f"{{{_NS_STREAMS}}}DeviceStream")
    device_stream.set("name", sample["device_name"])
    device_stream.set("uuid", sample["uuid"])

    def _add_sample(parent: ET.Element, tag: str, value: float, item_id: str, sequence: int) -> None:
        elem = ET.SubElement(parent, f"{{{_NS_STREAMS}}}{tag}")
        elem.set("dataItemId", item_id)
        elem.set("timestamp", now)
        elem.set("sequence", str(sequence))
        elem.set("subType", "ACTUAL")  # DNC 客户端按 subType=ACTUAL 匹配
        elem.text = f"{value:g}"

    def _add_event(parent: ET.Element, tag: str, value: str, item_id: str, sequence: int) -> None:
        elem = ET.SubElement(parent, f"{{{_NS_STREAMS}}}{tag}")
        elem.set("dataItemId", item_id)
        elem.set("timestamp", now)
        elem.set("sequence", str(sequence))
        elem.text = value

    seq = sample["sequence"]

    # 主轴组件
    spindle = ET.SubElement(device_stream, f"{{{_NS_STREAMS}}}ComponentStream")
    spindle.set("component", "Spindle")
    spindle.set("name", "spindle")
    spindle_samples = ET.SubElement(spindle, f"{{{_NS_STREAMS}}}Samples")
    _add_sample(spindle_samples, "SpindleSpeed", sample["spindle_speed"], "s1", seq)
    _add_sample(spindle_samples, "SpindleLoad", sample["spindle_load"], "s2", seq)

    # 控制器组件（事件）
    controller = ET.SubElement(device_stream, f"{{{_NS_STREAMS}}}ComponentStream")
    controller.set("component", "Controller")
    controller.set("name", "controller")
    controller_events = ET.SubElement(controller, f"{{{_NS_STREAMS}}}Events")
    _add_event(controller_events, "Execution", sample["execution"], "c1", seq)
    _add_event(controller_events, "ControllerMode", sample["controller_mode"], "c2", seq)
    _add_event(controller_events, "Availability", sample["availability"], "c3", seq)

    # 轴组件
    axes = ET.SubElement(device_stream, f"{{{_NS_STREAMS}}}ComponentStream")
    axes.set("component", "Axes")
    axes.set("name", "axes")
    axes_samples = ET.SubElement(axes, f"{{{_NS_STREAMS}}}Samples")
    _add_sample(axes_samples, "PathFeedrate", sample["feedrate"], "a1", seq)
    _add_sample(axes_samples, "Feedrate", sample["feedrate"], "a2", seq)
    _add_sample(axes_samples, "Xabs", sample["x_position"], "x1", seq)
    _add_sample(axes_samples, "Yabs", sample["y_position"], "y1", seq)
    _add_sample(axes_samples, "Zabs", sample["z_position"], "z1", seq)

    return ET.tostring(root, encoding="unicode")


class _MockAgentHandler(BaseHTTPRequestHandler):
    """HTTP 处理：/probe /current /sample。"""

    simulator: MachineSimulator  # 由 Server 注入

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("mock-agent: " + fmt, *args)

    def _send_xml(self, body: str, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 方法名约定
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            if path == "/probe":
                sample = self.simulator.next_sample()
                body = build_devices_xml(sample["device_name"], sample["uuid"])
                self._send_xml(body)
            elif path in ("/current", "/sample"):
                sample = self.simulator.next_sample()
                body = build_streams_xml(sample)
                self._send_xml(body)
            else:
                self._send_xml("<error>Not Found</error>", status=404)
        except Exception as exc:  # pragma: no cover - 防御性
            logger.error("mock-agent handler error: %s", exc)
            self._send_xml("<error>Internal Server Error</error>", status=500)

    def do_HEAD(self) -> None:  # noqa: N802
        # 健康检查辅助：无 body 的 200
        self.send_response(200)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", "0")
        self.end_headers()


class MockMTConnectAgent:
    """本地 MTConnect 模拟 Agent 服务。

    用法::

        agent = MockMTConnectAgent(port=5010)
        agent.start()
        url = agent.url                       # http://127.0.0.1:5010
        # ... 用 MTConnectClient / MTConnectAdapter 连接 url ...
        agent.stop()

    也支持上下文管理器：``with MockMTConnectAgent() as agent: ...``。
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        device_name: str = DEFAULT_DEVICE_NAME,
    ) -> None:
        self.host = host
        self.port = port
        self.simulator = MachineSimulator(device_name=device_name)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        """Agent 基础 URL（MTConnectClient / Adapter 用）。"""
        return f"http://{self.host}:{self.port}"

    @property
    def is_running(self) -> bool:
        return self._server is not None

    def start(self) -> "MockMTConnectAgent":
        """启动模拟 Agent（非阻塞）。重复调用幂等。

        端口策略：``port=0`` 时由 OS 自动分配空闲端口（避免测试中先探测后
        绑定的 TOCTOU 竞态）；绑定失败（Windows 端口排除区 / 已被占用）时
        自动换端口重试。实际端口可通过 :attr:`url` 读取。
        """
        if self._server is not None:
            logger.debug("MockMTConnectAgent already running at %s", self.url)
            return self
        handler = type(
            "_BoundHandler",
            (_MockAgentHandler,),
            {"simulator": self.simulator},
        )
        # port=0 OS 分配；绑定失败 换端口重试（WinError 10013 / EADDRINUSE）
        last_exc: OSError | None = None
        for _ in range(_MAX_BIND_ATTEMPTS):
            try:
                self._server = ThreadingHTTPServer((self.host, self.port), handler)
                break
            except OSError as exc:
                last_exc = exc
                if self.port == 0:
                    # 已是自动分配端口仍失败 放弃（环境级问题）
                    raise
                logger.warning("MockMTConnectAgent bind %s:%s failed: %s；换端口重试", self.host, self.port, exc)
                self.port = 0
        else:
            raise last_exc  # type: ignore[misc]  # 循环必然 break 或 raise，此分支不可达
        # port=0 时回读 OS 实际分配端口
        if self.port == 0:
            self.port = int(self._server.server_address[1])
        # 允许端口快速重绑（测试中频繁启停）
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info("MockMTConnectAgent started at %s (device=%s)", self.url, self.simulator.device_name)
        return self

    def stop(self) -> None:
        """停止模拟 Agent。重复调用幂等。"""
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        if self._thread is not None:
            self._thread.join(timeout=DEFAULT_THREAD_JOIN_TIMEOUT_SEC)
            self._thread = None
        logger.info("MockMTConnectAgent stopped")

    def __enter__(self) -> "MockMTConnectAgent":
        return self.start()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()


__all__ = [
    "MachineSimulator",
    "MockMTConnectAgent",
    "build_devices_xml",
    "build_streams_xml",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_DEVICE_NAME",
]
