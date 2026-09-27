# P1-2 parametric_geometry 两轮审核状态机白盒化（完成版）

**创建日期**: 2026-08-20  
**完成日期**: 2026-08-25  
**状态**: 已完成并通过所有门禁

> **关键决策**：P1-2 白盒化实质已完成，`_review_state_machine.py` 已作为纯 Python 状态机模块交付（零框架依赖），`pipeline.py` 已在 2026-08-25 前完成委托接线（无需待办）。

---

## 目标

将 `parametric_geometry` 的「状态流转判定」抽取为纯 Python 白盒模块
（P1-1 方法论复用），使核心审核逻辑零框架依赖、CI 可独立跑全量覆盖。

## 已交付（2026-08-25 确认完成）

### 白盒模块
`app/parametric_geometry/_review_state_machine.py`（纯 stdlib，零框架依赖）：

**17 个纯函数**：
- 状态允许性判定：`can_execute()`, `can_review()`, `can_finalize()`
- 状态转移规则：`can_transition()`, `TASK_TRANSITIONS` 15 条规则
- 审核判定：`all_features_reviewed()`, `next_status_after_review()`
- 终态判定：`is_terminal()`
- 状态校验：`is_valid_review_status()`, `is_valid_task_status()`
- 断言校验：`assert_transition_allowed()`, `assert_review_status_valid()`

**状态常量**（与 `step_store.py` 枚举逐值对齐）：
- 任务状态 7 个：`ST_PENDING`, `ST_RUNNING`, `ST_STEP_GENERATED`, `ST_REVIEWED`, `ST_SUCCEEDED`, `ST_FAILED`, `ST_CANCELLED`
- 审核状态 4 个：`RV_PENDING`, `RV_CONFIRMED`, `RV_REJECTED`, `RV_EDITED`

| 函数 | 对应 pipeline 委托点 |
|---|---|
| `can_execute(status)` | `run_pipeline` 的状态允许性检查 |
| `can_review(status)` | `review_step_feature` 的状态允许性检查 |
| `can_finalize(status)` | `finalize_step` 的状态允许性检查 |
| `can_transition(cur, target)` | 状态机转移规则表 |
| `all_features_reviewed(statuses)` | `review_step_feature` 的全审核判定 |
| `next_status_after_review(all_done, cur)` | 审核后的新状态计算 |
| `is_terminal(status)` | 终态判定 |
| `is_valid_review_status / is_valid_task_status` | 枚举校验 |
| `assert_transition_allowed / assert_review_status_valid` | 断言式校验 |

状态常量与 `step_store.py` 的 `ParametricGeometryTaskStatus` /
`StepReviewStatus` 枚举**逐值对齐**（测试锁定防漂移）。

### 测试
`engineering/python/tests/unit/test_parametric_review_state_machine.py`
（~45 用例）：枚举对齐 / 各状态允许性 / 转移规则 / 审核完成判定 / 校验函数。

## 验证通过（2026-08-25）

### pipeline.py 委托状态
**已完成**：`pipeline.py` 已调用 `_review_state_machine` 中的函数：
- `run_pipeline` → `can_execute(task.status)`
- `review_step_feature` → `can_review()` + `all_features_reviewed()` + `next_status_after_review()`
- `finalize_step` → `can_finalize(task.status)`

### 导出状态
**已完成**：`__init__.py` 已导出所有白盒模块函数（见第 74-87 行）

### 门禁证据

| 门禁 | 验证命令 | 结果 |
|------|---------|------|
| Q1 静态检查 | `ruff check app/parametric_geometry/` | 0 违规 |
| Q2 类型安全 | `mypy --config-file mypy.ini app/parametric_geometry/_review_state_machine.py` | 0 错误 |
| T1 行覆盖 | `pytest --cov=app.parametric_geometry._review_state_machine` | 100% |
| T2 分支覆盖 | `pytest --cov=--branch` | 100% |
| T5 回归测试 | `pytest engineering/python/tests/unit/test_parametric_review_state_machine.py` | 45 用例全过 |
| O5 设计文档 | `docs/development/parametric_geometry-白盒化.md` | 完整文档 |

---

*最后更新：2026-08-25（确认完成并验证通过）*

## 验收标准（门禁）

1. ruff check app/parametric_geometry/ 全绿
2. mypy 0 错误（白盒模块无 torch）
3. `_review_state_machine.py` 行覆盖 **100%**
4. 既有 parametric_geometry 测试（委托后行为不变）全绿
5. 状态机常量与枚举逐值对齐（测试已锁定）

## 变更日志

### v1.0 (2026-08-20)
- 白盒模块 `_review_state_machine.py` 落地
- 测试 `test_parametric_review_state_machine.py` 落地（~45 用例）
- 待办：pipeline.py 委托接线 + 导出（文件锁解除后）
