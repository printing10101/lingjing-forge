# 数据采集管道 V1 使用文档（M0.5）

> 模块路径：`python/app/pipelines/`
> 适用版本：M0.5（数据采集管道 V1）
> 上游依赖：M0.3 MTConnect 适配器 / M0.4 MachiningRecord 模型
> 下游任务：M0.6+ 数据分析、可视化

本目录实现从 MTConnect Agent 到 PostgreSQL + TDengine 的端到端数据采集管道。
本文档说明模块组成、运行方式、配置项、错误处理策略、单元测试入口与二次开发指引。

---

## 1. 模块组成

| 文件 | 作用 |
| ---- | ---- |
| `python/app/pipelines/__init__.py` | 顶层包入口，导出公共 API |
| `python/app/pipelines/converter.py` | `Sample → MachiningRecordCreate` 纯函数转换层 + 批量聚合器 |
| `python/app/pipelines/machining_collector.py` | 异步采集主程序，含断线重连 / 写库重试 / 异常隔离 |
| `python/app/pipelines/tests/test_collector.py` | 单元测试套件 |
| `docs/pipelines/collector-usage.md` | 本文档 |

---

## 2. 快速开始

### 2.1 最简运行方式（验收脚本）

```bash
cd python && python -c "
import asyncio
from app.pipelines.machining_collector import start_collector
asyncio.run(start_collector(duration=60, agent_url='http://demo.mtconnect.org:80'))
"
```

运行 60 秒后会自动停止，期间从 demo agent 拉取数据并写入数据库。

### 2.2 自定义采集配置

```python
import asyncio
from app.pipelines import (
    CollectorConfig,
    start_collector,
    stop_collector,
)

async def main():
    config = CollectorConfig(
        agent_url="http://192.168.1.10:80",
        machine_id="CNC-01",
        tool_id="T-EM-10",
        material="45号钢",
        sample_interval=1.0,   # 1 Hz
        batch_size=100,         # 累积 100 条 Sample 触发 flush
        flush_interval=5.0,     # 或每 5 秒触发 flush
        aggregation_strategy="mean",  # mean / last / max / min
    )
    job_id = await start_collector(config=config, duration=300)
    print("job_id:", job_id)
    # 也可在外部主动停止
    # stats = await stop_collector()

asyncio.run(main())
```

### 2.3 长驻服务模式

```python
import asyncio
from app.pipelines import start_collector, stop_collector, get_collector

async def main():
    await start_collector()  # 不传 duration → 持续运行
    try:
        while True:
            await asyncio.sleep(10)
            c = get_collector()
            if c:
                print(c.get_stats())
    except KeyboardInterrupt:
        await stop_collector()

asyncio.run(main())
```

---

## 3. 配置项说明（`CollectorConfig`）

| 字段 | 类型 | 默认值 | 说明 |
| ---- | ---- | ------ | ---- |
| `agent_url` | str | `http://demo.mtconnect.org:80` | MTConnect Agent base URL |
| `machine_id` | str | `CNC-01` | 机床标识，写入 `process_params` 与 `machine_id` |
| `tool_id` | str | `T-DEFAULT` | 刀具标识 |
| `material` | str | `45号钢` | 工件材料 |
| `sample_interval` | float | `1.0` | MTConnect 拉取间隔（秒）。`1.0` 即 1 Hz |
| `batch_size` | int | `100` | 累积多少条 Sample 后强制 flush |
| `flush_interval` | float | `5.0` | 累积多少秒后强制 flush（与 `batch_size` 满足任一即触发） |
| `aggregation_strategy` | str | `mean` | `mean` / `last` / `max` / `min` |
| `series_id_prefix` | str | `mach` | TDengine 时序子表 ID 前缀 |
| `process_params` | dict | `{}` | 附加工艺参数（depth_of_cut / coolant / operation 等） |
| `max_write_retries` | int | `3` | PostgreSQL / TDengine 写库失败重试次数 |
| `retry_backoff` | float | `2.0` | 写库重试初始退避（秒），指数递增，封顶 30s |
| `use_task_manager` | bool | `False` | 是否把外层调度委托给 `AsyncTaskManager`（预留位） |
| `tdengine_table` | str | `mtconnect` | TDengine 时序子表名 |

> **采样频率** 与 **批量写入策略** 的关系：
> - 在 1 Hz 采样下，每 5 秒累计 ~5 条 Sample。
> - 当采样频率提高到 10 Hz 时，每 5 秒累计 ~50 条 Sample，仍由 `flush_interval=5.0` 触发。
> - 当突发流量导致短时间累积 100 条以上时，由 `batch_size=100` 立即触发。
> - 两条规则满足任一即触发 flush。

---

## 4. 运行时数据流

```
MTConnect Agent
      │  HTTP /sample
      ▼
MTConnectAdapter.fetch_sample()    ← M0.3 适配器（含断线重连）
      │  Sample
      ▼
SampleBatchAggregator.add()         ← 累积 + 时间窗/条数窗判定
      │  should_flush()?
      ▼  (yes)
aggregate_samples_to_record()       ← N 条 Sample → 1 条 MachiningRecordCreate
      │
      ├──► postgres_sink()           → PostgreSQL  (relational record)
      │     └─ retry → _retry_queue
      │
      └──► tdengine_sink()           → TDengine    (time-series rows)
            └─ retry → _tdengine_retry

最终：MachiningRecord + 原始 Sample 时序数据落库
```

---

## 5. 错误处理与可靠性

| 失败场景 | 策略 |
| -------- | ---- |
| MTConnect Agent 不可达 | 适配器内部 5 次指数退避重试；采集器 `_stats.poll_errors++`，进程不退出 |
| MTConnect XML 解析失败 | 适配器抛出异常 → 采集器捕获 → 记录 `poll_errors` 并继续下一轮 |
| PostgreSQL 写库失败 | `_write_with_retry` 最多 `max_write_retries=3` 次指数退避，全部失败则 `_retry_queue` 入队；下一轮 flush 优先重试队列 |
| TDengine 写库失败 | 同上，写入 `_tdengine_retry` 队列 |
| 适配器 / Sink 抛异常 | 异常隔离于单次循环；记录日志和 stats，采集器继续运行 |
| 重复调用 `start()` | 返回已存在的 `job_id`（不重复创建 task） |
| 进程信号 / KeyboardInterrupt | `stop()` 优雅退出，flush 残留 buffer |
| Stop 事件触发于重试等待中 | 立即中断重试，进入最终 flush 与清理流程 |

---

## 6. 状态查询

```python
from app.pipelines import get_collector

c = get_collector()
if c:
    # 实时统计
    print(c.get_stats())
    # 完整状态（包含配置、聚合器状态、重试队列大小）
    print(c.dump_state())
```

`get_stats()` 输出示例：

```python
{
    "samples_consumed": 312,
    "records_written": 4,
    "tdengine_rows_written": 312,
    "write_retries": 0,
    "write_failures": 0,
    "poll_errors": 0,
    "started_at": 1749639625.12,
    "stopped_at": 1749639685.18,
    "runtime_seconds": 60.06,
}
```

---

## 7. 数据库表结构（M0.4 已定义，本任务不修改）

### 7.1 PostgreSQL `machining_records`

主记录表，由 `app/database/models/machining_record.py` 定义。
关键字段：`record_id`, `machine_id`, `tool_id`, `material`, `timestamp`,
`spindle_speed`, `feed_rate`, `tdengine_series_id`, `process_params`。

### 7.2 TDengine `mtconnect` 子表

时序子表，由 `app/integrations/mtconnect/adapter.py::DEFAULT_TABLE_DDL` 定义。
关键列：`ts TIMESTAMP, spindle_speed DOUBLE, spindle_load DOUBLE, feedrate DOUBLE, execution BINARY(32)`。

> 本任务**不**修改上述结构。如需新增字段请在 M0.6+ 任务中协调。

---

## 8. 单元测试

```bash
cd python && pytest app/pipelines/tests/test_collector.py -v
```

测试覆盖：
- `CollectorContext` 必填校验与冻结行为
- `convert_sample_to_record` 单条转换（包含 None / NaN / 字符串兜底）
- `aggregate_samples_to_record` 四种聚合策略（mean/last/max/min）
- `SampleBatchAggregator` 时间窗 / 条数窗 / 容量上限
- `MachiningCollector` 生命周期、双重 start、probe 失败优雅降级
- 写库重试、TDengine 重试、瞬时失败自愈
- 异常隔离（适配器抛错不崩溃）
- 单例外观（`start_collector` / `stop_collector` / `reset_collector`）

测试**不**依赖真实网络与数据库，CI 中可安全执行。

---

## 9. 与 AsyncTaskManager 集成（可选）

`CollectorConfig.use_task_manager = True` 时（当前为预留位），采集任务
可被 `app.tasks.task_system.AsyncTaskManager` 调度：

```python
# 注意：M0.5 阶段尚未启用 use_task_manager；以下为后续任务规划。
from app.tasks.task_system import AsyncTaskManager

manager = AsyncTaskManager()
task_id = await manager.submit(
    task_type="DATA_COLLECTION",
    fn=start_collector,
    params={"duration": 60, "agent_url": "http://demo.mtconnect.org:80"},
)
```

采集器自身已具备完整生命周期管理（lock / 状态机 / 优雅停止），
是否再叠加 TaskManager 仅影响外部可观测性，不影响数据正确性。

---

## 10. 二次开发指引

### 10.1 替换存储后端

实现两个 async 函数并注入：

```python
from app.pipelines import (
    CollectorConfig,
    MachiningCollector,
    start_collector,
)

async def my_postgres_sink(records): ...
async def my_tdengine_sink(samples): ...

config = CollectorConfig(agent_url="...", batch_size=50)
collector = MachiningCollector(
    config=config,
    record_sink=my_postgres_sink,
    tdengine_sink_fn=my_tdengine_sink,
)
await collector.start()
```

### 10.2 替换 MTConnect 适配器

任何满足 `probe() / fetch_sample() / stop()` 协议的对象皆可注入：

```python
class MyAdapter:
    def probe(self): return {"instance_id": "x"}
    def fetch_sample(self): return Sample(...)
    def stop(self): pass

collector = MachiningCollector(config=cfg, adapter=MyAdapter())
```

### 10.3 自定义聚合策略

在 `app.pipelines.converter.aggregate_samples_to_record` 中扩展 `strategy` 集合。
注意同步更新 `SampleBatchAggregator.flush_records` 的 docstring 与测试。

---

## 11. 已知限制

- **不包含**数据清洗 / 异常值剔除（计划在 M0.6+ 任务中实现）。
- **不包含**实时数据分析（如 LNN 推理、振动特征工程等）。
- TDengine 子表 ID 包含 UTC 时间戳，超长运行后可能需要轮转策略（M0.7+ 考虑）。
- 同一台机器/刀具/工艺的 series_id 不可复用；如需跨 session 关联，建议在 `process_params` 中显式标注 `job_id`。

---

## 12. 验收对照

| 验收项 | 实现位置 | 状态 |
| ------ | -------- | ---- |
| 后台采集任务可正常启动并运行 | `start_collector()` / `MachiningCollector.start()` | |
| 模拟数据可流经整个管道 | `__init__.py` + 单元测试 | |
| 数据准确写入 PostgreSQL / TDengine | `postgres_sink()` / `tdengine_sink()` | |
| 支持任务状态查询 | `get_collector()` / `get_stats()` / `dump_state()` | |
| 支持正常停止 | `stop_collector()` / `MachiningCollector.stop()` | |
| 完善的异常处理 | 重试 / 隔离 / 入队 | |
| 单元测试全部通过 | `pytest app/pipelines/tests/test_collector.py -v` | |

---

## 13. 参考资料

- M0.3 MTConnect 适配器实现：`python/app/integrations/mtconnect/adapter.py`
- M0.4 MachiningRecord 数据模型：`python/app/models/machining_record.py`
- AsyncTaskManager：`python/app/tasks/task_system.py`
- TDengine 客户端：`python/app/services/tdengine_client.py`
