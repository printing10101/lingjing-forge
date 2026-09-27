"""lomo — 灵境制造 SDK.

通过 HTTP 调用后端 ``/api/v1/`` 接口，提供同步与异步两套 API。
SDK 独立于后端代码，可被任意第三方 Python 脚本 / notebook 使用，只需
后端服务处于运行状态即可。

快速入门（同步）::

    from lomo import LomoClient

    client = LomoClient(base_url="http://127.0.0.1:8000")

    # 创建数据集
    ds = client.datasets.create(
        name="phm2010",
        schema={
            "fields": {"sample_id": {"type": "int", "required": True}},
            "primary_key": ["sample_id"],
        },
        owner_id="alice",
    )

    # 提交版本
    ver = client.datasets.commit_version(
        ds["dataset_id"],
        records=[{"sample_id": 1, "force": 12.3}],
    )

    # 创建并运行工作流
    spec = {
        "name": "demo",
        "version": "1.0.0",
        "nodes": [...],
        "edges": [],
        "inputs": {},
        "outputs": [],
        "metadata": {},
    }
    run_id = client.workflows.run(spec, inputs={})

    # 订阅事件流
    for ev in client.workflows.subscribe(run_id):
        print(ev)

快速入门（异步，适用于 asyncio 应用与 Jupyter Notebook）::

    import asyncio
    from lomo import AsyncLomoClient

    async def main():
        async with AsyncLomoClient() as client:
            ds = await client.datasets.create(
                name="phm2010",
                schema={"fields": {...}, "primary_key": ["sample_id"]},
                owner_id="alice",
            )
            run_id = await client.workflows.run(spec={...}, owner_id="alice")
            async for ev in client.workflows.subscribe(run_id):
                if ev["event"] == "workflow_completed":
                    break

    asyncio.run(main())

契约版本:
    :data:`CONTRACTS_VERSION` 与后端 ``app.contracts.CONTRACTS_VERSION`` 严格对齐。
    SDK 启动时会与后端 ``/api/v1/health`` 暴露的契约版本做兼容性校验（可选）。
"""

from __future__ import annotations

from lomo._async import (
    AsyncLomoClient,
    AsyncSSEEventStream,
    AsyncStreamingJSONL,
)
from lomo.client import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    LomoClient,
    SSEEventStream,
    StreamingJSONL,
)
from lomo.exceptions import (
    LomoAPIError,
    LomoAuthError,
    LomoConnectionError,
    LomoError,
    LomoInternalError,
    LomoNotFoundError,
    LomoServiceUnavailableError,
    LomoTimeoutError,
    LomoValidationError,
)

# 契约层版本（与后端 app.contracts.CONTRACTS_VERSION 对齐）。
# 当后端契约发生 breaking change 时，需新开 ADR 并提升主版本号。
CONTRACTS_VERSION = "1.0.0"

__version__ = "1.0.0"


# 资源类懒导出
# 阶段 5 验收标准要求支持 ``from lomo import Workflow, Dataset, Snapshot``。
# 通过 __getattr__ 在首次访问时导入对应资源类，避免循环依赖与启动开销。
# 同步与异步资源类均采用懒导出，保持模块加载开销最小。


def __getattr__(name: str):
    if name == "Workflow":
        from lomo.workflow import Workflow as _Workflow

        return _Workflow
    if name == "Dataset":
        from lomo.dataset import Dataset as _Dataset

        return _Dataset
    if name == "Snapshot":
        from lomo.snapshot import Snapshot as _Snapshot

        return _Snapshot
    if name == "AsyncWorkflow":
        from lomo._async import AsyncWorkflow as _AsyncWorkflow

        return _AsyncWorkflow
    if name == "AsyncDataset":
        from lomo._async import AsyncDataset as _AsyncDataset

        return _AsyncDataset
    if name == "AsyncSnapshot":
        from lomo._async import AsyncSnapshot as _AsyncSnapshot

        return _AsyncSnapshot
    raise AttributeError(f"module 'lomo' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(
        list(globals().keys())
        + ["Workflow", "Dataset", "Snapshot"]
        + ["AsyncWorkflow", "AsyncDataset", "AsyncSnapshot"]
    )


__all__ = [
    # 同步客户端
    "LomoClient",
    "StreamingJSONL",
    "SSEEventStream",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
    # 异步客户端
    "AsyncLomoClient",
    "AsyncStreamingJSONL",
    "AsyncSSEEventStream",
    # 同步资源类（懒导出）
    "Workflow",
    "Dataset",
    "Snapshot",
    # 异步资源类（懒导出）
    "AsyncWorkflow",
    "AsyncDataset",
    "AsyncSnapshot",
    # 异常
    "LomoError",
    "LomoAPIError",
    "LomoConnectionError",
    "LomoTimeoutError",
    "LomoNotFoundError",
    "LomoValidationError",
    "LomoAuthError",
    "LomoInternalError",
    "LomoServiceUnavailableError",
    # 元信息
    "CONTRACTS_VERSION",
    "__version__",
]
