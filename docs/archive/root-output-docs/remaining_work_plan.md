# 剩余优化计划（V3.0 后续）

## 状态：磁盘已满(0G)，需释放空间后才能继续文件写入

---

## 阶段A：config全局单例 → FastAPI Depends

### 子任务
1. `dependencies.py` 添加 `get_config()` —— 返回 `app.config.config` 单例（←当前写入失败）
2. 更新15处顶层导入的API文件，add `Depends(get_config)` 到router或端点
3. 非API文件的lazy import保持不变（已是最佳实践）

### 目标文件（API层顶层导入）
- `api/v1/auth.py:22`
- `api/v1/health.py:16`
- `api/v1/cam_validation/routes.py:58`
- `api/v1/chatter_prediction/routes.py:42`
- `api/v1/gcode_generation/routes.py:48`
- `api/v1/cutting_parameters/routes.py:36`
- `api/v1/feature_extraction/routes.py:25`
- `api/v1/image_to_3d/routes.py:16`
- `api/v1/parametric_geometry/routes.py:24`
- `api/v1/project_packages.py:44`
- `main.py:50`
- `middleware_stack.py:26`
- `router_registry.py:31`
- `api/routers/adr_pipeline.py:30`
- `service/production_service.py` → 已迁移至services/domain/

---

## 阶段B：核心链路集成测试

### 子任务
1. `tests/integration/test_predict.py` — LNN predict端点
2. `tests/integration/test_training.py` — LNN train端点
3. `tests/integration/test_simulation.py` — simulation端点

所有测试使用 `TestClient` + SQLite内存数据库 + mock外部依赖。

---

## 阶段C：shared/ 契约进一步激活

### 重复枚举（可统一）
- `ChatterReviewStatus` — shared/lnn/types.py vs chatter_prediction/chatter_store.py
- `PredictionMethod` — shared/lnn/types.py vs chatter_prediction/chatter_store.py
- `ChatterPredictionTaskStatus` — shared/lnn/types.py vs chatter_prediction/chatter_store.py

### 步骤
1. 让 chatter_store.py 从 shared/ 导入这些枚举
2. 让 chatter_prediction/__init__.py 重新导出
3. 验证无下游破坏

---

## 阶段D：其他

1. 物理目录重命名（脚本就绪，需关闭IDE后执行）
2. 6个巨型Vue组件拆分（逐项进行）
3. 视图组件切换到features/api导入（15个API模块已就绪）
