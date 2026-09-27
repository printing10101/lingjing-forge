# OPC UA 协议适配器使用指南

## 概述

OPC UA 适配器是工业设备数据采集系统的核心组件之一，专为欧洲客户及工业 4.0 技术需求设计。该适配器与 MTConnect 适配器并行工作，提供对 OPC UA 协议服务器的数据订阅、转换和持久化能力。

**核心功能**：
- OPC UA 客户端连接与会话管理
- 数据节点订阅与实时数据获取
- OPC UA 数据到系统内部格式的转换
- 批处理机制与 TDengine 时序数据库集成
- 指数退避重试策略处理网络异常

## 目录结构

```
python/app/integrations/opcua/
├── __init__.py          # 包导出文件
├── adapter.py           # 核心适配器实现
├── parser.py            # 数据解析模块
├── cli.py               # 命令行入口
└── tests/
    ├── __init__.py
    ├── test_adapter.py  # 适配器单元测试
    ├── test_parser.py   # 解析器单元测试
    └── test_cli.py      # CLI 单元测试
```

## 安装与依赖

### 系统要求

- Python 3.8+
- TDengine 3.0+（用于数据持久化）
- 网络连接至 OPC UA 服务器

### 安装 Python 依赖

```bash
cd python
pip install -r requirements.txt
```

**关键依赖包**：
- `asyncua` - OPC UA 客户端库
- `tdengine-connector` - TDengine 数据库连接器
- `pytest` - 单元测试框架

## 快速开始

### 1. 基本使用

使用 CLI 工具快速启动数据采集：

```bash
cd python
python -m app.integrations.opcua.cli --endpoint opc.tcp://localhost:4840
```

### 2. 指定采集时长

采集 20 秒数据后自动停止：

```bash
python -m app.integrations.opcua.cli \
  --endpoint opc.tcp://localhost:4840 \
  --duration 20
```

### 3. 自定义订阅节点

指定要订阅的 OPC UA 节点 ID：

```bash
python -m app.integrations.opcua.cli \
  --endpoint opc.tcp://localhost:4840 \
  --nodes "ns=2;s=SpindleSpeed" "ns=2;s=SpindleLoad" "ns=2;s=Feedrate"
```

### 4. 配置批处理参数

调整批处理大小和间隔以优化性能：

```bash
python -m app.integrations.opcua.cli \
  --endpoint opc.tcp://localhost:4840 \
  --batch-size 100 \
  --batch-interval 5.0
```

## 命令行参数详解

### 必需参数

| 参数 | 说明 | 示例 |
|------|------|------|
| `--endpoint` | OPC UA 服务器端点 URL | `opc.tcp://localhost:4840` |

### 可选参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--interval` | 1.0 | 数据轮询间隔（秒） |
| `--duration` | None | 采集时长（秒），None 表示持续运行 |
| `--batch-size` | 50 | 触发批量写入的样本数量阈值 |
| `--batch-interval` | 2.0 | 触发批量写入的时间间隔（秒） |
| `--nodes` | 默认节点列表 | 要订阅的 OPC UA 节点 ID 列表 |
| `--tdengine-dsn` | `tdengine://root:taosdata@localhost:6030/industrial_data` | TDengine 连接字符串 |
| `--tdengine-table` | `opcua_samples` | TDengine 表名 |

## API 使用

### 1. 程序化调用

在 Python 代码中使用适配器：

```python
from app.integrations.opcua import OPCUAAdapter, AdapterConfig

# 创建配置
config = AdapterConfig(
    endpoint="opc.tcp://localhost:4840",
    interval=1.0,
    batch_size=50,
    batch_interval=2.0,
    node_ids=["ns=2;s=SpindleSpeed", "ns=2;s=SpindleLoad"]
)

# 初始化适配器
adapter = OPCUAAdapter(config=config)

# 连接到服务器
info = adapter.connect()
print(f"已连接到: {info['endpoint']}")

# 运行采集（阻塞 20 秒）
ingested = adapter.run(duration=20.0)
print(f"采集样本数: {ingested}")

# 手动刷新缓冲区
flushed = adapter.flush()
print(f"持久化样本数: {flushed}")
```

### 2. 自定义回调函数

在数据采样时执行自定义逻辑：

```python
from app.integrations.opcua import OPCUAAdapter, AdapterConfig, Sample

def on_sample_callback(sample: Sample):
    """每个采样周期触发"""
    print(f"主轴转速: {sample.spindle_speed} RPM")
    print(f"主轴负载: {sample.spindle_load}%")
    print(f"进给速度: {sample.feedrate} mm/min")
    print(f"执行状态: {sample.execution}")

config = AdapterConfig(endpoint="opc.tcp://localhost:4840")
adapter = OPCUAAdapter(config=config)
adapter.connect()
adapter.run(duration=10.0, on_sample=on_sample_callback)
```

### 3. 数据解析工具

直接使用解析函数处理 OPC UA 数据：

```python
from app.integrations.opcua import parse_opcua_data

# OPC UA 原始数据
raw_data = {
    "SpindleSpeed": 12000.0,
    "SpindleLoad": 42.5,
    "Feedrate": 1500.0,
    "Execution": "ACTIVE"
}

# 解析为 Sample 对象
sample = parse_opcua_data(raw_data)
print(f"主轴转速: {sample.spindle_speed}")
print(f"是否为空样本: {sample.is_empty()}")
```

## TDengine 表结构

适配器自动创建以下表结构用于存储采集数据：

```sql
CREATE TABLE IF NOT EXISTS industrial_data.opcua_samples (
    ts TIMESTAMP,
    spindle_speed DOUBLE,
    spindle_load DOUBLE,
    feedrate DOUBLE,
    execution NCHAR(50),
    extras NCHAR(500)
);
```

**字段说明**：
- `ts` - 数据观测时间戳（UTC）
- `spindle_speed` - 主轴转速（RPM）
- `spindle_load` - 主轴负载（百分比）
- `feedrate` - 进给速度（mm/min）
- `execution` - 执行状态（ACTIVE/IDLE/STOPPED 等）
- `extras` - 扩展字段（JSON 格式存储额外数据项）

## 数据模型

### Sample 类

`Sample` 是系统内部的标准数据格式，与 MTConnect 适配器保持一致：

```python
@dataclass
class Sample:
    spindle_speed: Optional[float] = None      # 主轴转速
    spindle_load: Optional[float] = None       # 主轴负载
    feedrate: Optional[float] = None           # 进给速度
    execution: Optional[str] = None            # 执行状态
    extras: Dict[str, Any] = field(default_factory=dict)  # 扩展数据
    observed_at: Optional[datetime] = None     # 观测时间
    
    def is_empty(self) -> bool:
        """判断样本是否为空"""
        return all(
            getattr(self, field) is None 
            for field in ("spindle_speed", "spindle_load", "feedrate", "execution")
        )
```

### AdapterConfig 类

适配器配置类，封装所有可配置参数：

```python
@dataclass
class AdapterConfig:
    endpoint: str                              # OPC UA 服务器端点
    interval: float = 1.0                      # 轮询间隔（秒）
    batch_size: int = 50                       # 批处理大小阈值
    batch_interval: float = 2.0                # 批处理时间间隔（秒）
    node_ids: Optional[List[str]] = None       # 订阅节点 ID 列表
    tdengine_dsn: str = "tdengine://..."       # TDengine 连接字符串
    tdengine_table: str = "opcua_samples"      # TDengine 表名
```

## 故障排查

### 1. 连接失败

**问题**：无法连接到 OPC UA 服务器

**可能原因**：
- 端点 URL 格式错误
- 网络不可达
- 服务器未启动或端口未监听

**解决方案**：
```bash
# 检查端点格式
python -m app.integrations.opcua.cli --endpoint opc.tcp://192.168.1.100:4840

# 使用 telnet 测试端口连通性
telnet 192.168.1.100 4840

# 检查 OPC UA 服务器日志
```

### 2. 订阅节点无数据

**问题**：连接成功但无法接收数据

**可能原因**：
- 节点 ID 不正确
- 节点未被发布到地址空间
- 权限不足

**解决方案**：
```bash
# 使用 OPC UA 客户端工具浏览服务器节点
# 确认节点 ID 格式正确（如 ns=2;s=SpindleSpeed）
# 检查用户认证和权限配置
```

### 3. TDengine 写入失败

**问题**：数据采集正常但无法写入数据库

**可能原因**：
- TDengine 服务未启动
- 连接字符串错误
- 数据库或表不存在
- 权限不足

**解决方案**：
```bash
# 检查 TDengine 服务状态
systemctl status taosd

# 验证连接字符串
python -c "import tdengine; conn = tdengine.connect('tdengine://root:taosdata@localhost:6030'); print('连接成功')"

# 手动创建数据库和表
taos -s "CREATE DATABASE IF NOT EXISTS industrial_data; USE industrial_data; CREATE TABLE IF NOT EXISTS opcua_samples (ts TIMESTAMP, spindle_speed DOUBLE, spindle_load DOUBLE, feedrate DOUBLE, execution NCHAR(50), extras NCHAR(500));"
```

### 4. 批处理未触发

**问题**：数据缓冲但未按预期写入

**可能原因**：
- `batch_size` 设置过大
- `batch_interval` 设置过长
- 数据采样频率过低

**解决方案**：
```bash
# 降低批处理阈值进行测试
python -m app.integrations.opcua.cli \
  --endpoint opc.tcp://localhost:4840 \
  --batch-size 10 \
  --batch-interval 1.0
```

### 5. 内存占用过高

**问题**：长时间运行后内存持续增长

**可能原因**：
- 批处理缓冲区未及时刷新
- TDengine 写入失败导致数据堆积

**解决方案**：
```bash
# 启用调试日志查看缓冲区状态
export LOG_LEVEL=DEBUG
python -m app.integrations.opcua.cli --endpoint opc.tcp://localhost:4840

# 减小批处理大小
python -m app.integrations.opcua.cli \
  --endpoint opc.tcp://localhost:4840 \
  --batch-size 20 \
  --batch-interval 1.0
```

## 验收测试

### 1. 单元测试

运行完整的单元测试套件：

```bash
cd python
pytest app/integrations/opcua/tests/ -v
```

**预期结果**：所有测试用例通过，覆盖率 ≥ 80%

### 2. 集成连接测试

使用标准 OPC UA 模拟器验证完整流程：

```bash
# 启动 OPC UA 模拟器（如 Prosys Simulation Server）
# 端点：opc.tcp://localhost:4840

# 运行 CLI 工具
cd python
python -m app.integrations.opcua.cli --endpoint opc.tcp://localhost:4840 --duration 10

# 预期输出：
# 已连接到: opc.tcp://localhost:4840
# 订阅节点: ns=2;s=SpindleSpeed, ns=2;s=SpindleLoad, ...
# 采样: spindle_speed=12000.0, spindle_load=42.5, ...
# 采集完成，总样本数: 10
```

### 3. 稳定性测试

验证 24 小时连续运行稳定性：

```bash
# 运行 24 小时采集测试
cd python
python -m app.integrations.opcua.cli \
  --endpoint opc.tcp://localhost:4840 \
  --duration 86400 \
  --batch-size 100 \
  --batch-interval 5.0

# 监控指标：
# - 无连接中断
# - 无数据丢失
# - 内存占用稳定
```

## 与 MTConnect 适配器对比

| 特性 | OPC UA 适配器 | MTConnect 适配器 |
|------|--------------|-----------------|
| 协议 | OPC UA（订阅模式） | HTTP（轮询模式） |
| 数据获取 | 事件驱动订阅 | 定时轮询 |
| 实时性 | 高（毫秒级） | 中（秒级） |
| 复杂度 | 中等 | 低 |
| 适用场景 | 工业 4.0、欧洲客户 | 传统 CNC 设备 |
| 数据格式 | OPC UA 节点值 | MTConnect XML |

## 最佳实践

1. **批处理配置**：根据数据采样频率调整 `batch_size` 和 `batch_interval`，平衡性能与实时性
2. **节点选择**：仅订阅必要节点，减少网络带宽和服务器负载
3. **错误处理**：实现自定义重试逻辑处理临时网络故障
4. **监控告警**：集成监控系统，对连接中断、数据延迟等异常告警
5. **日志记录**：生产环境启用 INFO 级别日志，调试时启用 DEBUG 级别

## 参考资料

- [OPC UA 协议规范](https://opcfoundation.org/about/opc-technologies/opc-ua/)
- [asyncua 库文档](https://github.com/FreeOpcUa/opcua-asyncio)
- [TDengine 官方文档](https://docs.tdengine.com/)
- MTConnect 适配器文档：`docs/integrations/mtconnect-usage.md`
- M0.5 采集管道文档：`docs/data/pipeline.md`

## 许可证

本模块遵循项目整体许可证，详见 `LICENSE` 文件。
