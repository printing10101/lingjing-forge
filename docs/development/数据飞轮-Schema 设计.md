# 数据飞轮 Schema 设计文档（P2-1/P2-2/P2-3）

**文档版本**: 2.0  
**创建日期**: 2026-08-20  
**完成日期**: 2026-08-25  
**状态**: P2-1 契约 + ORM+ 测试全部完成并通过所有门禁

---

## 目标

打通"实测数据 → 参数优化 → 再实测"的数据飞轮闭环：

```
机床加工 ──► 采集(cutting_experience) ──► 存储(SQLAlchemy)
                                              │
优化参数 ◄── LNN 推荐 ── 统计/训练数据 ────────┘
```

## 契约层（P2-1，已完成）

`app/contracts/cutting_experience.py`：

| 契约 | 说明 |
|---|---|
| `CuttingParameters` | 工艺参数：切深/进给/转速/切削速度/步距/冷却液 |
| `CuttingResults` | 实测结果：节拍/粗糙度/磨损/尺寸误差/结果判定 |
| `MachiningAnomaly` | 异常快照：类型/严重度/实测值/阈值 |
| `CuttingExperience` | 主记录：任务/机床/程序/刀具/材料/参数/结果/异常/来源 |
| `ExperienceQuery` | 查询条件（machine/tool/material/type/result/时间窗/分页） |
| `ExperienceStats` | 聚合统计（均值/合格率/异常率） |

**设计要点**：
- `extra="forbid"`：严格字段校验，防止脏数据进入飞轮
- 枚举（MachiningType/Result/CoolantMode）约束取值
- `tags: dict[str, Any]` 兼容未来传感器扩展（MTConnect 振动/功率）

## 存储层（P2-2，已完成）

`app/database/models/cutting_experience.py` → 表 `cutting_experiences`：

- 筛选字段扁平建列 + 索引：`machine_id` / `tool_id` / `material` / `created_at`
- 嵌套字段 JSONB（SQLite 回退 JSON）：`parameters` / `results_extra` / `anomalies` / `tags`
- `anomaly_count` 冗余列：`has_anomaly` 过滤无需 JSON 扫描

`app/services/domain/cutting_experience_service.py`：

| 函数 | 用途 |
|---|---|
| `create_cutting_experience` | 单条落库（手工录入/API） |
| `create_many_cutting_experiences` | 批量落库（MTConnect 管道） |
| `list_cutting_experiences` | 条件分页查询 |
| `get_cutting_experience` | 单条详情 |
| `aggregate_experience_stats` | 仪表盘统计 |
| `delete_cutting_experience` | 管理删除 |

## 采集 API（P2-3，下一步）

```http
POST /api/v1/experience/capture        # 单条采集（权限 experience:write）
POST /api/v1/experience/batch          # 批量采集（MTConnect 落库）
GET  /api/v1/experience?machine_id=…   # 查询（分页）
GET  /api/v1/experience/stats          # 聚合统计
GET  /api/v1/experience/{id}           # 详情
DELETE /api/v1/experience/{id}         # 删除（管理）
```

## 与既有模型的关系

- **`MachiningRecord`**（既有）：实时高频数据（转速/进给/振动时序），TDengine 引用
- **`CuttingExperience`**（新增）：**工艺参数 + 结果**全要素，飞轮优化信号源
- 关系：一次加工 = 1 条 MachiningRecord（时序）+ 1 条 CuttingExperience（要素）

## 测试计划

- 契约：字段校验/枚举/默认值/extra=forbid（15 用例）
- ORM 转换：from_contract/to_contract_dict 往返一致（10 用例）
- 服务层：SQLite 内存库 CRUD + 聚合统计（15 用例）

## 飞轮闭环（Phase D 联动）

1. 采集 N 条 CuttingExperience
2. `aggregate_experience_stats` 体检数据质量
3. LNN `ParameterOptimizer.recommend_params(geometry, material)` 
4. 推荐参数 → 新加工 → 新 CuttingExperience（带推荐标记 tag）
5. 统计对比：推荐组 vs 基线组节拍/粗糙度提升

## 变更日志

### v1.0 (2026-08-20)
- 契约层落地 `app/contracts/cutting_experience.py`
- ORM 落地 `app/database/models/cutting_experience.py`
- 服务层落地 `app/services/domain/cutting_experience_service.py`
- 待办：P2-3 API 接线、models/__init__.py 导出（文件锁待解）
