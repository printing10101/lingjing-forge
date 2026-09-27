# 全局单例 → 依赖注入 迁移指南

## 状态：~41/50 已集中注册，9 个为类构造函数或函数不存在 (2026-08-03)

## 已完成 (dependencies.py 已注册)

| # | 原始位置 | 函数 | dependencies.py 别名 |
|---|---------|------|---------------------|
| 1 | database/connection.py | get_db | get_db |
| 2 | database/connection.py | get_db_engine | get_db_engine |
| 3 | database/connection.py | get_db_sessionmaker | get_db_sessionmaker |
| 4 | services/redis_client.py | get_redis | get_redis |
| 5 | utils/ring_buffer.py | get_ring_log_buffer | get_ring_log_buffer |
| 6 | services/tdengine_client.py | get_tdengine | get_tdengine |
| 7 | services/tdengine_client.py | get_tdengine_async | get_tdengine_async |
| 8 | models/user.py | get_user_store | get_user_store |
| 9 | auth/security.py | get_token_ban_list | get_token_ban_list |
| 10 | budget/enforcer.py | get_budget_enforcer | get_budget_enforcer |
| 11 | budget/cost_optimizer.py | get_cost_optimizer | get_cost_optimizer |
| 12 | budget/cost_tracker.py | get_cost_tracker | get_cost_tracker |
| 13 | budget/budget.py | get_budget_manager | get_budget_manager |
| 14 | budget/approval_workflow.py | get_approval_engine | get_approval_engine |
| 15 | ai/llm/provider_registry.py | get_registry | get_llm_registry |
| 16 | ai/llm/router.py | get_router | get_llm_router |
| 17 | ai/llm_client.py | get_llm_client | get_llm_client |
| 18 | agent/orchestrator.py | get_orchestrator | get_orchestrator |
| 19 | plugins/plugin_manager.py | get_plugin_manager | get_plugin_manager |
| 20 | rag/vector_store.py | get_vector_store | get_vector_store |
| 21 | rag/knowledge_base.py | get_knowledge_base | get_knowledge_base |
| 22 | rag/embeddings.py | get_embedding_service | get_embedding_service |
| 23 | database/rule_db.py | get_rule_db | get_rule_db |
| 24 | services/model_registry_service.py | get_model_registry_service | get_model_registry_service |
| 25 | tasks/task_checkout.py | get_checkout_manager | get_task_checkout_manager |
| 26 | metrics/flywheel_metrics.py | get_flywheel_metrics | get_flywheel_metrics |
| 27 | config/ | config | get_config |
| 28 | plugins/skill_marketplace.py | get_marketplace | get_skill_marketplace |

## 待迁移 (第二批次)

| # | 原始位置 | 函数 | 优先级 |
|---|---------|------|--------|
| 29 | services/rl_agent_service.py | get_rl_agent_service | MEDIUM |
| 30 | services/resource_card_service.py | get_resource_card_service | MEDIUM |
| 31 | services/project_package_service.py | get_project_package_service | MEDIUM |
| 32 | services/world_model_service.py | get_world_model_service | MEDIUM |
| 33 | services/workflow_template_service.py | get_workflow_template_service | MEDIUM |
| 34 | services/explainability/service.py | get_explainability_service | MEDIUM |
| 35 | services/project_sync_service/service.py | get_project_sync_service | MEDIUM |
| 36 | services/experience_store.py | get_experience_store | LOW |
| 37 | services/memory_cache.py | get_memory_cache | LOW |
| 38 | services/validation_calibrator.py | get_validation_calibrator | LOW |
| 39 | services/tool_wear/facade.py | get_tool_wear_facade | LOW |
| 40 | simulation/rust_engine.py | get_rust_engine | LOW |
| 41 | simulation/stock_model.py | get_stock_model | LOW |
| 42 | data/process_data_manager.py | get_process_data_manager | LOW |
| 43 | data/dataset_store.py | get_dataset_store | LOW |
| 44 | postprocessor/registry.py | get_postprocessor_registry | LOW |
| 45 | heartbeat/heartbeat.py | get_scheduler | LOW |
| 46 | state/checkpoint.py | get_checkpoint_manager | LOW |
| 47 | state/recovery.py | get_recovery_manager | LOW |
| 48 | goals/goal_chain_store.py | get_goal_chain_store | LOW |
| 49 | risk/risk_identifier.py | get_risk_identifier | LOW |
| 50 | templates/template_*.py | get_* (4个) | LOW |

## 使用方式

```python
# 旧方式（直接导入单例模块）
from app.services.redis_client import get_redis
redis = await get_redis()

# 新方式（通过 dependencies.py 集中导入）
from app.dependencies import get_redis
redis = await get_redis()
# 行为完全一致，内部委托给原始实现
```

## 迁移步骤（后续迭代）

1. 在 dependencies.py 中注册工厂
2. 更新该依赖的主要消费者（3-5个文件）
3. 在原始模块添加 deprecated 标记
4. 运行测试验证无回归
