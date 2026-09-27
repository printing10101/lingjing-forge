# P4-1 FastAPI 路由声明化

**创建日期**: 2026-08-21  
**状态**: 白盒声明模块 + 测试已落地；engineering.py 委托接线待文件锁解除

---

## 目标

将 `app/api/routers/engineering.py` 的手写 `app.include_router(...)` 序列
改为**声明式路由表**（P1-1 方法论复用），集中管理路由注册，
并内置冲突/重复注册校验。

## 已交付

### 白盒声明模块
`app/api/routers/_route_registry.py`（纯 stdlib，不 import FastAPI）：

| 声明/函数 | 说明 |
|---|---|
| `RouterSpec` | 路由声明（name/router/domain/description） |
| `validate_spec(spec)` | 单条校验（name 非空、prefix 属性） |
| `validate_specs(specs)` | 整表校验（前缀冲突、name 重复） |
| `is_duplicate_registration` | 同一 router 对象重复检测（幂等） |
| `register_routers(specs, include_fn)` | 统一注册执行（冲突可 fail-fast） |
| `group_by_domain(specs)` | 按域分组（文档/审计用） |

### 测试
`engineering/python/tests/unit/test_route_registry.py`（~17 用例）：
单条校验 / 整表冲突 / 幂等 / 注册顺序 / 分组。

## 待接线（文件锁解除后执行）

### engineering.py 委托
```python
# app/api/routers/engineering.py
from app.api.routers._route_registry import RouterSpec, register_routers

ENGINEERING_ROUTERS: list[RouterSpec] = [
    RouterSpec("simulation", simulation_api.router, "engineering", "仿真"),
    RouterSpec("chatter", chatter_api.router, "engineering", "颤振仿真"),
    RouterSpec("cutting_force", cutting_force_api.router, "engineering", "切削力仿真"),
    RouterSpec("project", project_routes.router, "engineering", "项目管理"),
    RouterSpec("step_import", step_import_api.router, "engineering", "STEP 导入"),
    RouterSpec("rules", rules_router, "engineering", "规则引擎"),
    RouterSpec("dxf_pipeline", dxf_pipeline_routes.router, "engineering", "DXF 流水线"),
    RouterSpec("collision_check", collision_check.router, "engineering", "碰撞检查"),
    RouterSpec("tools", tools.router, "engineering", "刀具管理"),
    RouterSpec("nl2cad", nl2cad_router, "engineering", "NL-to-CAD"),
    RouterSpec("postprocessor_dialects", postprocessor_dialects.router, "engineering", "方言管理"),
]

def register(app: FastAPI) -> None:
    register_routers(ENGINEERING_ROUTERS, app.include_router)
```

### 新增路由接线（experience/optimizer/monitor_ws）
```python
# 追加到 ENGINEERING_ROUTERS：
RouterSpec("experience", experience_routes.router, "engineering", "加工经验飞轮"),
RouterSpec("optimizer", optimizer_routes.router, "engineering", "参数优化"),
RouterSpec("monitor_ws", monitor_ws.router, "engineering", "实时监控 WS"),
```

## 验收标准（门禁）

1. ruff check app/api/routers/ 全绿
2. mypy 0 错误
3. `_route_registry.py` 行覆盖 ≥90%（目标 100%）
4. 既有 engineering API 测试（委托后行为不变）全绿
5. 冲突/重复注册检测用例覆盖

## 变更日志

### v1.0 (2026-08-21)
- 白盒声明模块 `_route_registry.py` 落地
- 测试 `test_route_registry.py` 落地（~17 用例）
- 待办：engineering.py 委托 + 新路由接线（文件锁解除后）
