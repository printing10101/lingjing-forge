"""端到端数据采集管道（M0.5 交付物）。

本子包负责把 M0.3 阶段实现的 :mod:`app.integrations.mtconnect` 适配器
输出的高频时序数据，转换为 M0.4 阶段定义的 :class:`app.models.machining_record.MachiningRecord`
关系型记录，并完成批量持久化。

模块结构
--------

* :mod:`app.pipelines.converter` – MTConnect ``Sample`` → ``MachiningRecordCreate``
  的纯函数转换层，可独立单元测试。
* :mod:`app.pipelines.machining_collector` – 异步采集主程序：
    - 拉取 MTConnect ``Sample``；
    - 在 N 条时序样本累积或时间窗口到达后聚合成 1 条加工记录；
    - 通过 ``AsyncTaskManager`` 调度批量写入 PostgreSQL / TDengine；
    - 断线重连 / 写库失败重试 / 异常隔离等可靠性策略。
* :mod:`app.pipelines.tests.test_collector` – 单元测试套件。

不包含的工作
------------

本阶段**不**涉及：
    - 实时数据分析（分析任务由独立模块负责）；
    - 数据清洗 / 预处理（计划在后续任务中实现）；
    - 前端监控界面。
"""

from app.pipelines.converter import (
    SampleBatchAggregator,
    convert_sample_to_record,
    aggregate_samples_to_record,
    CollectorContext,
)

from app.pipelines.machining_collector import (
    CollectorConfig,
    MachiningCollector,
    CollectorStats,
    start_collector,
    stop_collector,
    get_collector,
    reset_collector,
)

__all__ = [
    "SampleBatchAggregator",
    "convert_sample_to_record",
    "aggregate_samples_to_record",
    "CollectorContext",
    "CollectorConfig",
    "MachiningCollector",
    "CollectorStats",
    "start_collector",
    "stop_collector",
    "get_collector",
    "reset_collector",
]
