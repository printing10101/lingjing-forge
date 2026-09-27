"""服务层模块。

目录结构（V3.0 重构）:
  domain/         — 领域服务：业务逻辑（CRUD、流程编排、领域规则）
  infrastructure/ — 基础设施：Redis、TDengine、内存缓存等外部系统连接
  tool_wear/      — 刀具磨损子域（预测/校准/补偿）
  explainability/ — 可解释性子域
  project_sync_service/ — 项目 Git 同步子域
  _shared/        — 服务基类

领域服务顶层的便捷导入（原 ``app.service`` shim 已迁移至此）:
>>> from app.services import materials_service
"""

from app.services.domain import (
    materials_service,
    equipment_service,
    process_routes_service,
    tools_service,
    quality_service,
    documents_service,
    production_service,
)
