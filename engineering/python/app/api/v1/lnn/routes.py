"""LNN API 路由聚合器。

本模块原为 1727 行的单体路由文件，P0-2.3 子路由拆分后改为薄聚合器：
- 业务端点已分散到 5 个子路由文件（routes_prediction / routes_training /
  routes_quantization / routes_models / routes_system）；
- 模块级状态（_ALLOWED_DATA_BASE_DIRS / _TRAINING_QUEUES / _hybrid_engine 等）
  集中到 ``dependencies.py``；
- 本文件仅保留主 ``router``（含 prefix/tags/dependencies）并通过
  ``router.include_router(...)`` 聚合所有子路由。

子路由的 ``APIRouter()`` 不再重复声明 prefix/tags/dependencies，统一由
本聚合器注入，避免重复鉴权与路径前缀错配。
"""

from fastapi import APIRouter, Depends

from app.auth.permissions import require_permission
from app.api.v1.lnn.routes_prediction import router as prediction_router
from app.api.v1.lnn.routes_training import router as training_router
from app.api.v1.lnn.routes_quantization import router as quantization_router
from app.api.v1.lnn.routes_models import router as models_router
from app.api.v1.lnn.routes_system import router as system_router

router = APIRouter(
    prefix="/api/v1/lnn",
    tags=["LNN Models"],
    dependencies=[Depends(require_permission("lnn:read"))],
)

# 聚合所有子路由（端点路径已在各子路由装饰器中声明，此处不再加 prefix）
router.include_router(prediction_router)
router.include_router(training_router)
router.include_router(quantization_router)
router.include_router(models_router)
router.include_router(system_router)
