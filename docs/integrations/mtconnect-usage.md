# MTConnect 适配器 V1 使用说明

> 任务编号：**M0.3**
> 适用版本：`v1.x`（对应 MTConnect 1.5+）
> 维护模块：`python/app/integrations/mtconnect/`

## 1. 概述

MTConnect 适配器 V1 是 APT 数据底座的"设备 - 时序库"桥梁，负责：

1. 周期性（默认 **1 Hz**）从 MTConnect Agent 的 `/sample` 端点拉取 XML 数据。
2. 将响应解析为强类型的 [`Sample`](../../python/app/integrations/mtconnect/parser.py) 对象，仅保留 M0.3 任务指定的四个核心数据项：
   - `spindle_speed`（主轴转速）
   - `spindle_load`（主轴负载）
   - `feedrate`（进给速度）
   - `execution`（执行状态）
3. 通过 M0.2 阶段交付的 `tdengine_client` 批量写入 TDengine 时序库。

> 本任务范围明确**不包含**与具体机床型号的底层协议通信、错误恢复策略以及前端展示。

## 2. 目录结构

```
python/app/integrations/mtconnect/
├── __init__.py            # 包导出
├── adapter.py             # 核心：轮询 + 批写 + 重试
├── parser.py              # 纯函数：MTConnect XML → Sample
├── cli.py                 # CLI 入口（python -m ... cli）
└── tests/
    ├── __init__.py
    └── test_adapter.py    # 单元测试套件
```

## 3. 安装

`requests` 与 `lxml` 已分别声明在两份依赖清单中：

- 根目录 [`requirements.txt`](../../requirements.txt)：包含 `lxml==5.3.0`。
- 子目录 [`python/requirements.txt`](../../python/requirements.txt)：包含 `requests>=2.32.0` 与 `lxml>=5.0.0`。

首次拉取代码后：

```bash
cd python
pip install -r requirements.txt
```

> 如果只做单元测试，可不必安装 `taospy`——测试套件使用内存桩，不依赖真实 TDengine。

## 4. 快速开始

### 4.1 启动 TDengine（如尚未运行）

```bash
docker compose up -d lnn-tdengine
```

### 4.2 运行 CLI（采集 20 秒，写入 demo 库）

```bash
cd python
timeout 30 python -m app.integrations.mtconnect.cli \
    --agent http://demo.mtconnect.org:80 \
    --duration 20 \
    --output tds://localhost:6030/test.mtconnect
```

预期输出（节选）：

```
[2026-06-11 10:23:45.123] speed=12000.00 load=42.50 feed=1500.00 exec=ACTIVE
[2026-06-11 10:23:46.124] speed=12000.00 load=42.50 feed=1500.00 exec=ACTIVE
...
已写入 20 条 (errors=0, buffer_left=0)
```

### 4.3 验证数据已落库

```bash
cd python && python -c "
from app.services.tdengine_client import get_tdengine
c = get_tdengine()
result = c.query('SELECT COUNT(*) FROM test.mtconnect')
print('Records:', result)
"
```

预期 `Records` ≥ 20。

## 5. CLI 参数详解

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--agent` | `http://demo.mtconnect.org:80` | MTConnect Agent 的根 URL |
| `--duration` | （无限） | 运行时长（秒），省略时持续到 `Ctrl-C` |
| `--interval` | `1.0` | 轮询间隔（秒），即采样频率 |
| `--output` | `tds://localhost:6030/test.mtconnect` | TDengine 连接串 |
| `--batch-size` | `10` | 达到此条数后立即批量写入 |
| `--batch-interval` | `5.0` | 缓冲超过此秒数后强制 flush |
| `--max-retries` | `5` | 单次轮询的最大重试次数 |
| `--timeout` | `10.0` | HTTP 请求超时（秒） |
| `--log-level` | `INFO` | 日志等级（`DEBUG/INFO/WARNING/ERROR`） |
| `--dry-run` | _关闭_ | 跳过 TDengine 接线，仅做连通性验证 |

`--output` 接受 `tds://host:port/database` 形式，CLI 会自动改写 `TDENGINE_URL` / `TDENGINE_DB` 环境变量。

## 6. Python API 编程使用

```python
from app.integrations.mtconnect import MTConnectAdapter
from app.integrations.mtconnect.adapter import AdapterConfig
from app.services.tdengine_client import get_tdengine

config = AdapterConfig(
    agent_url="http://demo.mtconnect.org:80",
    interval=1.0,
    batch_size=20,
    database="lnn_tsdb",
    table="mtconnect",
)

adapter = MTConnectAdapter(config=config, tdengine_client=get_tdengine())
adapter.probe()                                # 失败会抛出 RuntimeError
ingested = adapter.run(duration=60.0)          # 阻塞 60 秒
print(f"ingested {ingested} samples")
```

如果不需要落库（仅做连通性验证或边缘调试），可不传 `tdengine_client`——适配器将仅打印 / 回调，不再尝试写库。

## 7. TDengine 表结构

CLI 启动时会自动 `CREATE DATABASE IF NOT EXISTS` 并创建以下结构：

```sql
CREATE TABLE IF NOT EXISTS <db>.mtconnect (
    ts             TIMESTAMP,
    spindle_speed  DOUBLE,
    spindle_load   DOUBLE,
    feedrate       DOUBLE,
    execution      BINARY(32)
);
```

列顺序与 `MTConnectAdapter._row_for_storage` 严格一致；如需新增字段，请同时修改：

- `MTConnectAdapter.DEFAULT_TABLE_DDL`（表结构）
- `MTConnectAdapter._row_for_storage`（行转换）
- 单元测试 `TestAdapterRun::test_run_persists_to_tdengine`（断言）

## 8. 重试与退避策略

`AdapterConfig.initial_backoff` 与 `max_backoff`（默认 0.5s / 16s）共同控制指数退避：

```
delay = min(initial_backoff * 2^(attempt-1), max_backoff) * jitter
```

`jitter` ∈ `[0.5, 1.5)`，避免多实例同时重试。`max_retries=5` 确保单次轮询不会无限阻塞；当所有重试耗尽时，本轮记为一次错误计入 `adapter.error_count`，然后适配器继续下一轮——确保最终仍能落库后续健康数据。

## 9. 单元测试

```bash
cd python && pytest app/integrations/mtconnect/tests/ -v
```

测试覆盖：

| 测试类 | 覆盖点 |
| --- | --- |
| `TestParser` | XML 解析、缺值、`UNAVAILABLE` 哨兵、容错 |
| `TestAdapterConfig` | 默认值、URL 规范化、参数校验 |
| `TestAdapterProbe` | `/probe` 成功 / 失败 / 非法响应 |
| `TestAdapterFetch` | `/sample` 解析 |
| `TestAdapterRun` | 轮询循环、重试退避、TDengine 写入、`stop()` 中断 |
| `TestAdapterFlush` | 空 buffer 行为、手动 flush |
| `TestCLI*` | 输出格式、argparse、`main()` 入口（成功 / 失败两条路径） |

> 测试不需要网络或 TDengine——所有外部依赖通过 `unittest.mock` 与内存桩注入。

## 10. 故障排查

| 现象 | 可能原因 | 处置建议 |
| --- | --- | --- |
| `Probe failed: HTTPError('HTTP 5xx')` | Agent 不可达 / 协议不匹配 | 检查 `--agent` URL 与网络连通性 |
| `TDengine client could not be initialised` | `taospy` 未安装或服务未启动 | `pip install taospy` + `docker compose up -d lnn-tdengine` |
| `已写入 0 条` | Agent 返回数据被 `is_empty()` 过滤 | 启用 `--log-level DEBUG` 查看原始响应 |
| 长时间没有打印 | `interval` 过大 | 调小 `--interval` 至 0.1 验证 |

## 11. 后续扩展点（M0.3+）

- **丰富数据项**：在 `parser.Sample` 中新增字段（如 `vibration`、`temperature`），同步调整表 DDL。
- **`/assets` 端点**：使用 `lxml` 解析设备能力描述，构造更智能的数据项过滤器。
- **异常检测钩子**：在 `on_sample` 回调中接入规则引擎或 LNN 推理服务。
- **Prometheus exporter**：将 `ingested_count` / `error_count` / `buffer_size` 暴露为指标。

---

如有问题，请联系数据底座工作组或在仓库 `Issues` 中反馈。
