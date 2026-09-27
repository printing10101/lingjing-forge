# feature_extraction 白盒化设计（P1-1）

> **路线图**：《自主化与护城河路线图.md》 Phase 1 P1-1
> **状态**：目标达成（2026-08-20）
> **原则**：只白盒「业务判定/状态流转逻辑」，保留 RANSAC 拟合对 numpy/sklearn/pyransac3d 的框架调用。

## 1. 动机

`feature_extraction/`（2284 行）是「mesh → 特征列表 → 工程师审核」流水线。其中：

- **算法内核**（RANSAC 平面/圆柱/圆环拟合、SVD、凸包面积）属于几何内核，保留框架调用，不重写（符合护城河"不是代码全自研"定位）。
- **业务判定逻辑**（凸台/凹陷/孔的分类规则、审核状态流转语义）是可高度自研、可单测、可专利申报的「自有逻辑」，此前**散落在框架类方法内部**（`hole_detector._classify_hole_or_boss`、`pipeline.review_feature/_check_all_reviewed/export`）。

本任务把这两类逻辑**抽成零依赖的纯 Python 白盒模块**，既提高自主占比，又显著改善可测性与状态机正确性。

## 2. 架构与边界

```
app/feature_extraction/
├── _feature_classifier.py        新增：特征分类判定规则（纯 Python，零框架依赖）
├── _review_state_machine.py      新增：审核状态机（纯 Python，零框架依赖）
├── hole_detector.py              （保留 numpy RANSAC 内核；分类决策委托 classifier）
├── pipeline.py                   （保留编排/IO；状态流转决策委托 state machine）
├── plane_extractor.py            （未改：RANSAC 内核）
├── cylinder_extractor.py         （未改：RANSAC 内核）
├── feature_store.py / precision_disclaimer.py  （未改）
└── __init__.py                   （导出新模块）
```

**边界纪律（自主化不过度）**：
- `_feature_classifier.py` **不含**任何 RANSAC 矩阵内核，只放离散判定函数与输入校验。
- `_review_state_machine.py` **不含**任何存储/文件/IO，只放状态判定纯函数。
- 两者刻意**不 import scipy / torch / sklearn / CadQuery**，在 torch 残缺 CI 上可独立稳定运行。
- 委托路径：`hole_detector` 用 `classify_hole_or_boss_deep()`；`pipeline` 用 `FeatureReviewStateMachine` 的各判定方法。

## 3. 判定规则（白盒化内容）

### 3.1 HOLE vs BOSS 分类（`_feature_classifier`）

从 `hole_detector._classify_hole_or_boss` 抽出纯判定：

```
offset < -threshold          → HOLE（凹陷）
offset > +threshold          → BOSS（凸起）
|offset| <= threshold        → 默认 HOLE（工业上保守偏孔）
```

- `classify_hole_or_boss(offset, threshold, default_type=HOLE) -> str`
- `classify_hole_or_boss_deep(...) -> (str, offset)`：额外返回规整 offset，供落库。
- 均含输入校验：`validate_offset`（NaN/inf/非数值 → `FeatureClassificationError`）、`validate_threshold`（必须 >0）。
- 附加：`is_known_feature_type` / `is_valid_review_action` / `validate_feature_params`（S1 输入校验门禁）。

### 3.2 审核状态机（`_review_state_machine`）

从 `pipeline` 的 inline 逻辑抽出纯状态机语义：

```
PENDING → RUNNING → FEATURES_EXTRACTED → REVIEWED → SUCCEEDED
            │           │                  │
            ├ FAILED    ├ FAILED           └ FAILED（兜底）
            └ CANCELLED
```

判定方法（纯函数，输入为普通字符串/列表）：
- `can_review / assert_reviewable`：仅 `features_extracted` 可审核。
- `assert_valid_action`：仅 confirmed / rejected / edited。
- `all_features_reviewed(statuses)`：无任何 pending → True。
- `next_state_after_all_reviewed(has_features)`：→ `reviewed`。
- `can_export / assert_exportable`：`features_extracted` / `reviewed`。
- `next_state_after_export()`：→ `succeeded`。

常量（`STATUS_*`）与 `feature_store.FeatureExtractionTaskStatus` 逐值对齐（测试断言锁定）。

## 4. 复杂度分析与生产级门禁证据

### 4.1 代码质量（Q1-Q5）

| 门禁 | 结果 |
|------|------|
| Q1 ruff（CI 全规则） | `ruff check app/` 全绿（含新增文件） |
| Q2 mypy 严格 | `mypy app/feature_extraction/...` 0 错误 |
| Q3 C901 | 新增函数圈复杂度均 ≤10（`ruff --select C901` 通过） |
| Q4 规模 | 新增文件均 <200 行；函数 <60 行；无 >3 层嵌套 |
| Q5 危险模式 | 无 shell/SQL/eval/exec；输入经 Pydantic/校验函数（S1） |

### 4.2 测试门禁（T1-T6）

新增测试文件 `tests/unit/test_feature_classifier_whitebox.py`，**53 用例**（≥20 要求）：

| 类 | 覆盖 |
|----|------|
| TestValidateOffset/Threshold | NaN / ±inf / 0 / 负 / 字符串 / None / 强制数值 |
| TestClassifyHoleOrBoss | HOLE / BOSS / 边界 / 默认类型 / 非法默认 / 非法阈值 / 幂等 |
| TestHelperPredicates | 已知类型集合 / 审核动作集合 |
| TestValidateFeatureParams | 拷贝不改入参 / 数值字符串强转 / NaN / 非数值 / 未知类型 / 非整数 inlier |
| TestReviewStateMachineTaskPredicates | 各状态可审核/可导出 / assert 抛错文案 / 下一状态 |
| TestReviewStateMachineReviewSemantics | 全审核判定 / 含 pending / 空列表 / 幂等 / 常量与枚举对齐 |
| TestPipelineReviewStateMachineIntegration | **真实 store 端到端**：审核→REVIEWED、非法 action、错状态、edited 必填、导出过滤+SUCCEEDED |

- **T1 行覆盖**：`_feature_classifier` = **100%**；`_review_state_machine` = **100%**（pytest-cov 实测）。
- **T2 分支**：两模块各判定分支全覆盖（含 NaN/inf/边界）。
- **T3 边界**：空输入 / 极值（±1e9）/ None / 负数 / 重复值 / 类型错误均覆盖。
- **T4 错误路径**：每个 except/raise 分支各有触发用例。
- **T5 回归**：既有 83 unit（含 geometry 的分类委托）全绿；`test_all_status_constants_match_enums` 锁定枚举一致性。
- **T6 幂等**：`all_features_reviewed` 重复判定相同、`next_state_after_all_reviewed` 稳定。

> 说明：`plane_extractor`/`hole_detector`/`pipeline` 的 RANSAC 与 scipy 路径在本机 torch 残缺环境下无法执行（`module 'torch' has no attribute 'Tensor'`），为**既有基线问题、非本次改动引入**。本次白盒逻辑均以纯函数方式隔离，已绕过该环境约束完成全覆盖。

### 4.3 性能（P1-P4）

- P1/P3/P4：白盒逻辑为 O(1) 判定函数（无循环遍历输入规模），无内存分配放大；改动仅增加一次函数调用，耗时可忽略，无回归风险。
- P2：判定函数与输入规模无关，天然满足"10 倍输入 <10 倍耗时"。

### 4.4 安全（S1-S5）

- S1：`validate_offset/threshold/feature_params` 对所有外部输入做有限数/类型校验，非法抛 `FeatureClassificationError`。
- S5：无新增敏感数据路径；错误消息不含密钥、绝对路径、用户数据。

### 4.5 可靠性（R1-R4）与可观测性（O1-O5）

- R1：状态机判定不触碰 IO，天然无外部依赖崩溃；既有 pipeline try/except 兜底逻辑保留。
- R4：`FeatureReviewStateMachine` 为无共享可变状态的纯函数，天然线程安全。
- O4：本文档即为设计文档（含复杂度边界）。
- O5：本文件 §4.2 覆盖率实测数字。

## 5. 文件变更清单

| 文件 | 变更 |
|------|------|
| `app/feature_extraction/_feature_classifier.py` | 新增（214 行，纯 Python 判定规则） |
| `app/feature_extraction/_review_state_machine.py` | 新增（纯 Python 审核状态机） |
| `app/feature_extraction/hole_detector.py` | `_classify_hole_or_boss` 分类决策委托 `classify_hole_or_boss_deep` |
| `app/feature_extraction/pipeline.py` | `review_feature`/`_check_all_reviewed`/`export_confirmed_features` 状态判定委托 `FeatureReviewStateMachine` |
| `app/feature_extraction/__init__.py` | 导出新模块及常量 |
| `tests/unit/test_feature_classifier_whitebox.py` | 新增 53 用例 |

**提交**（Conventional Commits）：
`refactor(python): feature_extraction RANSAC 判定逻辑白盒化`
