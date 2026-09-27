"""Agent Gateway API 子包入口。

P1-7：从原 ``agent_gateway.py``（941 行）拆分为 4 个子模块：

- :mod:`._state`     —— 共享状态 + :class:`TrainingCoordinator`（P3-2）
- :mod:`.training`   —— 训练端点（含训练并发控制）
- :mod:`.inference`  —— 推理 + 模型/执行/审计/Token/管线端点
- :mod:`.sse_stream` —— SSE 流式响应 + 心跳逻辑

对外保持向后兼容：

.. code-block:: python

    from app.api.v1.agent_gateway import router  # 仍可用

``router`` 对象的 ``prefix``、``tags`` 以及所有路由的 URL、HTTP 方法、
请求/响应 schema 均与拆分前等价。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.agent_gateway import (
    cam,
    dxf,
    gcode_jobs,
    inference,
    process_planning_api,
    runtime_api,
    sse_stream,
    training,
)

# 主 router：保留原 ``APIRouter(prefix="/api/agent/v1", tags=["Agent Gateway"])``
# 的 prefix 与 tags，子模块的 sub-router 不带 prefix，include_router 后路径
# 自动拼接为 ``/api/agent/v1/<path>``，与拆分前完全等价。
router = APIRouter(prefix="/api/agent/v1", tags=["Agent Gateway"])
router.include_router(training.router)
router.include_router(inference.router)
router.include_router(sse_stream.router)
router.include_router(cam.router)
router.include_router(gcode_jobs.router)
router.include_router(dxf.router)
router.include_router(process_planning_api.router)
router.include_router(runtime_api.router)


__all__ = ["router"]
