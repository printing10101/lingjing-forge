# ADR-018: 阶段 7 CAM 校验接入（G 代码 → 内部预校验 + CAM 软件二次校验 → 校验报告）

**日期**: 2026-07-14
**状态**: 已实现（s7-1 ~ s7-12 全部完成，验收标准 22/22 通过）
**决策者**: 项目负责人（独立开发）
**前置 ADR**: [ADR-014-G代码生成.md](ADR-014-G代码生成.md)（阶段 6 输出的 G 代码 + 审核记录 JSON 是本模块的核心输入）
**关联**: ADR-005（核心架构契约）、ADR-013（颤振预测，提供 ChatterReport 间接来源）、ADR-014（G 代码生成，提供 gcode_file_path + report.json）

---

## 背景

阶段 6（ADR-014）已交付 `GCodeGenerationPipeline`，导出两类产物供阶段 7 消费：

1. **G 代码文件**：`outputs/gcode/{gc_task_id}/{gc_task_id}.nc`（或 `.mpf` / `.h`，按控制器区分扩展名）
2. **审核记录 JSON**：`outputs/gcode/{gc_task_id}/{gc_task_id}.report.json`，包含：
   - `gcode_file_path`：G 代码文件绝对路径
   - `gcode_total_lines`：G 代码总行数
   - `controller_type`：目标控制器（fanuc_0i / siemens_840d / heidenhain_tnc / xmachine_xm100）
   - `material_name` / `safe_z` / `stock_top_z`：毛坯与安全高度（用于碰撞检测初始化）
   - `feature_results`：每个特征的 `line_range`（[start, end] 行号区间，用于碰撞事件归因到特征）
   - `cam_validation_required`：始终 True（项目记忆硬约束）
   - `industrial_hard_gates_note`：工业硬门槛告知文本

阶段 7 的工程任务是：**消费阶段 6 导出的 G 代码文件，执行「内部预校验 + CAM 软件二次校验」双层校验，输出 CAM 校验报告 JSON**，作为整条 7 阶段链路的最终产物（不触及物理机床）。

### 现有基础设施（不可重写）

项目已有轻量级内部 CAM 校验基础设施，阶段 7 必须**复用而非重写**：

1. `app/simulation/toolpath_parser.py`：`ToolpathParser.parse_gcode(gcode_text: str) -> list[ToolpathSegment]`
   - G 代码文本 → 刀路段列表
   - 维护 modal state（当前位置 / 进给率 / 主轴转速 / 刀具长度补偿）
   - 支持 Fanuc / Siemens / Heidenhain / xmachine 控制器语法过滤
2. `app/simulation/collision_detector.py`：`CollisionDetector` + `CollisionReport` + `CollisionEvent` + `WorkspaceLimits`
   - AABB 包围盒碰撞检测，3 轴 + 5 轴双模式
   - `check_segments(segments) -> CollisionReport`（3 轴）
   - `check_segments_5axis(segments, tool_vectors) -> CollisionReport`（5 轴）
   - 检测项：刀柄-工件碰撞 / 刀具-夹具碰撞 / 工作空间超限 / 安全 Z 高度违规 / 快速移动碰撞
3. `app/simulation/stock_model.py`：`StockModel`（毛坯长宽高 + 偏移）
4. `app/api/v1/collision_check.py`：现有 `/api/v1/collision-check` 端点（接受 stock + segments 输入，返回 CollisionReport dict）
   - **不直接复用此端点**，但阶段 7 的内部预校验器复用其底层 `CollisionDetector`

### 项目记忆硬约束（阶段 7 必须遵守）

- 系统定位为「工程师助手」，非「全自动 CAM 校验器」
- **生成的 G 代码必须经 CAM 软件（NX / PowerMill / PyCAM）二次校验后方可上机**；
  系统绝不直接接口 CNC 控制器
- `cam_validation_required` 始终 True，不可由环境变量关闭
- 大一独立项目，无法独立完成物理机床执行环节（需持证操作员 + 导师签字 + 保险）
- 阶段 7 产物终止于「CAM 校验报告 JSON」，**不触及物理机床**
- SUCCEEDED 状态禁止删除（与阶段 5/6 一致，`allow_delete_succeeded` 强制 False）
- HRC52 `pending_calibration` 由阶段 5 标注，阶段 7 仅继承并体现在校验报告告知文本中
- 推理路径禁止 `fit_transform`；MC dropout 模式切换需锁保护（阶段 7 不涉及模型推理，但继承告知文本）
- NOT_FOUND 错误响应不回显 task_id（防枚举攻击，与阶段 6 一致）

### 阶段 7 工程边界（与阶段 5/6 一致）

- 阶段 7 产物终止于「CAM 校验报告 JSON」，不直接驱动机床
- 内部预校验（`CollisionDetector`）是**快速预筛**，秒级反馈，**不可替代** CAM 软件二次校验
- CAM 软件二次校验通过 subprocess 调用外部 NX Open / PowerMill / PyCAM，系统不直接接口 CNC 控制器
- 当 NX / PowerMill / PyCAM 均不可用时，自动回退到「手动校验流程文档」模式：
  系统生成校验清单 + 工程师手动加载 G 代码到 CAM 软件 → 工程师回填校验结果
- 物理机床执行由人工 + CAM 软件 + 持证操作员完成，阶段 7 不触及

## 决策

采用「**复用 collision_detector + 新增 CAM 软件适配层 + 任务存储与状态机 + 工程师审核**」方案，
**不修改 `app/simulation/collision_detector.py` 与 `app/simulation/toolpath_parser.py`**（避免破坏现有
测试用例与 ADR-005 契约稳定性），新增独立的 `app/cam_validation/` 模块承载阶段 7 逻辑。

### 7 阶段全链路表

| 阶段 | 模块 | ADR | 输入 | 输出 | 状态 |
|------|------|-----|------|------|------|
| 1 | image_to_3d | ADR-006 | 照片 | 点云 / mesh | 已完成 |
| 2 | feature_extraction | ADR-007 | mesh | 几何特征 JSON | 已完成 |
| 3 | parametric_geometry | ADR-008 | 特征 JSON | STEP + OperationPlan 草案 | 已完成 |
| 4 | cutting_parameters | ADR-009 | 特征 + 材料 | ChatterParams JSON | 已完成 |
| 5 | chatter_prediction | ADR-013 | ChatterParams | ChatterReport JSON | 已完成 |
| 6 | gcode_generation | ADR-014 | ChatterReport + OperationPlan | G 代码 + 审核记录 | 已完成 |
| **7** | **cam_validation** | **ADR-018** | **G 代码 + 审核记录** | **CAM 校验报告 JSON** | **本 ADR** |

### 实现要点

#### 1. 模块结构 `app/cam_validation/`

```
app/cam_validation/
├── __init__.py                  # 模块公开符号导出
├── cam_store.py                  # 任务存储 + 状态机 + 审核枚举
├── cam_disclaimer.py             # 校验告知 + 工业硬门槛
├── gcode_loader.py               # 加载阶段 6 G 代码 + report.json
├── internal_validator.py         # 复用 CollisionDetector 内部预校验
├── cam_adapter.py                # NX / PowerMill / PyCAM / 手动 兜底适配
└── pipeline.py                   # 编排器 + 状态机 + 审核 + 导出
```

不新增独立的 `predictor_adapter.py`（阶段 7 不涉及模型推理）；
不新增 `tests/` 子目录（测试统一放 `python/tests/test_cam_validation.py` + `standalone_verify_cam_validation.py`）。

#### 2. 配置字段 `CamValidationConfig`（追加到 `app/config/__init__.py`）

11 个字段，命名与阶段 5/6 的 `ChatterPredictionConfig` / `GCodeGenerationConfig` 对齐：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | True | 模块启用开关 |
| `output_dir` | str | "outputs/cam_validation" | CAM 校验报告产物输出目录 |
| `max_concurrent` | int | 2 | 最大并发校验任务数（CAM 软件调用资源密集，默认 2） |
| `task_timeout_seconds` | int | 600 | 单任务超时（CAM 软件二次校验耗时较长，默认 600s） |
| `task_retention_hours` | int | 168 | 任务保留时长（7 天） |
| `precision_tier` | str | "mesh_calibrated" | 精度档位（继承上游） |
| `default_cam_backend` | str | "internal_only" | 默认 CAM 后端（internal_only / pycam / nx_open / powermill / manual） |
| `nx_open_executable` | str | "" | NX Open Python 脚本路径（空表示不可用） |
| `powermill_executable` | str | "" | PowerMill 可执行路径（空表示不可用） |
| `pycam_executable` | str | "" | PyCAM 包装器脚本路径（空表示不可用） |
| `allow_delete_succeeded` | bool | False | SUCCEEDED 禁删硬约束（不可由环境变量开启） |
| `cam_validation_required` | bool | True | CAM 校验强制（始终 True，不可关闭） |

环境变量前缀 `LNN_CAM_*`（如 `LNN_CAM_ENABLED`、`LNN_CAM_OUTPUT_DIR` 等）。

#### 3. 状态机 `CamValidationTaskStatus`（与阶段 5/6 对齐）

```
PENDING → RUNNING → VALIDATED → REVIEWED → SUCCEEDED
                ↘ FAILED
                ↘ TIMEOUT
```

- `PENDING`：任务已创建，等待执行
- `RUNNING`：正在加载 G 代码 + 调用 CollisionDetector + CAM 软件二次校验
- `VALIDATED`：双层校验完成，等待工程师审核
- `REVIEWED`：工程师已审核（confirmed / rejected / edited）
- `SUCCEEDED`：审核通过，CAM 校验报告已导出至 `output_dir`，**禁止删除**
- `FAILED`：校验失败（G 代码加载失败 / CollisionDetector 抛错 / CAM 软件返回错误 / 至少一个特征碰撞且工程师未审核通过）
- `TIMEOUT`：超过 `task_timeout_seconds`

审核枚举 `CamReviewStatus`：`pending` / `confirmed` / `rejected` / `edited`（与阶段 5/6 一致）。

#### 4. 任务存储 `CamValidationTask` dataclass

关键字段：

- `task_id`：前缀 `"cam_"` + uuid（如 `cam_550e8400-e29b-41d4-a716-446655440000`）
- `source_gcode_task_id`：上游阶段 6 G 代码任务 ID（用于追溯）
- `source_gcode_file_path`：阶段 6 G 代码文件绝对路径
- `source_gcode_report_path`：阶段 6 审核记录 JSON 路径
- `controller_type`：目标控制器（继承阶段 6）
- `material_name`：材料名称（继承阶段 6）
- `safe_z` / `stock_top_z`：安全 Z / 毛坯顶面 Z（继承阶段 6）
- `cam_backend`：本次校验使用的 CAM 后端（internal_only / pycam / nx_open / powermill / manual）
- `internal_report`：`InternalValidationReport`（CollisionDetector 报告 + 特征归因）
- `cam_software_report`：`CamSoftwareReport`（NX/PowerMill/PyCAM 校验结果，或手动兜底结果）
- `feature_validation_results`：`list[FeatureValidationResult]`，每个特征一条
- `cam_report_path`：输出给前端的最终 JSON 路径
- `cam_validation_required`：始终 True
- `started_at` / `completed_at` / `reviewed_by` / `reviewed_at`
- `warnings` / `errors`：校验过程中的警告与错误

#### 5. `FeatureValidationResult` dataclass（每条特征的校验结果）

```python
@dataclass
class FeatureValidationResult:
    feature_id: str
    feature_type: str            # plane / cylinder / hole / boss
    line_range: tuple[int, int] # 在 G 代码中的行号区间（继承阶段 6）
    internal_collision_count: int    # 内部预校验碰撞事件数
    internal_warnings: list[str]     # 内部预校验警告（如安全 Z 违规）
    cam_software_status: str         # CAM 软件校验状态（pass / fail / skipped / manual_pending）
    cam_software_messages: list[str] # CAM 软件返回的诊断消息
    review_status: str = "pending"   # 工程师审核状态（pending / confirmed / rejected / edited）
    edited_params: dict = field(default_factory=dict)  # edited 时的覆盖参数
    is_safe_to_machine: bool = False  # 工程师确认后的最终安全判定
```

#### 6. 校验告知 `CamDisclaimer`（参考 `GCodeDisclaimer` / `ChatterDisclaimer`）

告知文本包含：

- **精度继承链**：阶段 1 image_to_3d.precision_tier → 阶段 2/3/4 → 阶段 5 → 阶段 6 → 阶段 7
- **HRC52 待校准**：`pending_calibration` 材料的颤振预测置信度已降至 0.5（阶段 5 标注，阶段 7 继承）
- **CAM 校验强制**：G 代码必须经 NX / PowerMill / PyCAM 二次校验，**不可直接上机**
- **物理机床执行限制**：需持证操作员 + 导师签字 + 保险
- **内部预校验局限**：`CollisionDetector` 是 AABB 包围盒级别快速预筛，**不可替代** CAM 软件二次校验
  （无法检测刀轨几何精度 / 切削力 / 机床运动学 / 后处理器语法兼容性）
- **CAM 软件后端不可用兜底**：当 NX/PowerMill/PyCAM 均不可用时，自动回退「手动校验流程」模式
- **LTC 神经网络实验性**：阶段 7 不直接调用 LTC，但若阶段 5 ChatterReport 标注
  `prediction_method == "neural_network"`，阶段 7 必须在告知文本中标注「实验性路径」

#### 7. `GCodeLoader`：消费阶段 6 输出

职责：

- 读取阶段 6 导出的 `gcode_report.json`（含 `gcode_file_path` / `feature_results` / `controller_type` 等）
- 读取 G 代码文本文件（用于 `ToolpathParser.parse_gcode()`）
- 校验必填字段（`gcode_file_path` / `feature_results` / `controller_type` / `cam_validation_required`）
- 返回 `GCodeLoadResult` dataclass（含 `gcode_text` / `feature_results` / `controller_type` / `safe_z` / `stock_top_z`）
- 若 G 代码文件不存在或字段缺失，抛出 `GCodeLoadError`
- 若阶段 6 report.json 的 `task_status != "succeeded"`，拒绝加载并提示「阶段 6 未审核通过」

#### 8. `InternalValidator`：复用 `CollisionDetector`

**核心设计：不继承 `CollisionDetector`，而是组合（has-a）**，避免破坏现有测试用例。

```python
class InternalValidator:
    """阶段 7 内部预校验器。

    复用 app.simulation.collision_detector.CollisionDetector，
    将 G 代码刀路段映射到特征，输出每个特征的碰撞事件归因。
    """

    def __init__(self, config: CamValidationConfig):
        self._config = config
        self._parser = ToolpathParser()  # 组合，不继承

    def validate(
        self,
        gcode_text: str,
        feature_results: list[FeatureGCodeResult],
        controller_type: str,
        safe_z: float,
        stock_top_z: float,
        stock_length: float = 100.0,  # 默认毛坯尺寸（可由 task 上下文覆盖）
        stock_width: float = 100.0,
        stock_height: float = 50.0,
        mode: str = "3axis",
    ) -> tuple[CollisionReport, list[FeatureValidationResult]]:
        """执行内部预校验 + 特征归因。

        步骤：
        1. 调用 ToolpathParser.parse_gcode(gcode_text) 解析刀路段
        2. 构造 StockModel + WorkspaceLimits
        3. 调用 CollisionDetector.check_segments(segments) 执行碰撞检测
        4. 遍历 CollisionReport.events，按 block_number 归因到 feature_results 的 line_range
        5. 返回 (CollisionReport, list[FeatureValidationResult])
        """
```

**特征归因策略**：

- `CollisionEvent.block_number` → 查询 `feature_results` 中 `line_range` 包含该 block_number 的特征
- 若归因失败（block_number 不在任何特征的 line_range 内），归因到 `"unknown"` 并追加警告

#### 9. `CamAdapter`：CAM 软件二次校验接入层

**核心设计：策略模式**，按 `cam_backend` 分发到具体子适配器：

```python
class CamAdapter:
    """阶段 7 CAM 软件二次校验接入层。

    策略模式分发：
        - internal_only：仅内部预校验，跳过 CAM 软件（告知文本标注「未二次校验」）
        - pycam：调用 PyCAM Python 模块
        - nx_open：subprocess 调用 NX Open Python 脚本
        - powermill：subprocess 调用 PowerMill 宏
        - manual：生成手动校验清单 + 工程师回填（兜底）
    """

    def __init__(self, config: CamValidationConfig):
        self._config = config
        self._backends = {
            "internal_only": _InternalOnlyBackend(),
            "pycam": _PyCamBackend(config.pycam_executable),
            "nx_open": _NxOpenBackend(config.nx_open_executable),
            "powermill": _PowerMillBackend(config.powermill_executable),
            "manual": _ManualBackend(),
        }

    def validate(
        self,
        gcode_file_path: str,
        controller_type: str,
        cam_backend: str,
    ) -> CamSoftwareReport:
        """调用指定 CAM 后端执行二次校验。"""
        backend = self._backends.get(cam_backend)
        if backend is None:
            raise CamBackendError(f"未知 CAM 后端: {cam_backend}")
        return backend.validate(gcode_file_path, controller_type)
```

**子适配器职责**：

- `_InternalOnlyBackend`：直接返回 `CamSoftwareReport(status="skipped", messages=["未执行 CAM 软件二次校验，仅内部预校验"])`
- `_PyCamBackend`：`import pycam` → 调用 PyCAM 刀轨仿真 API → 解析返回的碰撞事件列表
- `_NxOpenBackend`：`subprocess.run([python, nx_open_script, gcode_file_path, controller_type])` → 解析 NX Open 输出的 JSON 报告
- `_PowerMillBackend`：`subprocess.run([powermill, /run=macro, gcode_file_path])` → 解析 PowerMill 输出
- `_ManualBackend`：生成校验清单 markdown（含 G 代码文件路径 + 控制器类型 + 期望校验项），
  返回 `CamSoftwareReport(status="manual_pending", messages=[...])`，等待工程师在前端回填结果

**降级策略**：

- 若 `cam_backend == "pycam"` 但 PyCAM 模块不可用 → 自动降级到 `manual`，追加警告
- 若 `cam_backend == "nx_open"` 但 `nx_open_executable` 为空 → 自动降级到 `manual`，追加警告
- 若 `cam_backend == "powermill"` 但 `powermill_executable` 为空 → 自动降级到 `manual`，追加警告
- **降级不阻塞任务**，告知文本必须明确标注「实际使用的 CAM 后端」与「降级原因」

#### 10. `CamValidationPipeline`：编排器

职责：

- `create_task(...)`：创建 PENDING 任务
- `run_pipeline(task_id)`：PENDING → RUNNING → VALIDATED（或 FAILED / TIMEOUT）
  - 加载 G 代码 + report.json（`GCodeLoader`）
  - 执行内部预校验（`InternalValidator.validate()`）
  - 执行 CAM 软件二次校验（`CamAdapter.validate()`）
  - 合并两层校验结果到 `feature_validation_results`
  - 写入 `internal_report` + `cam_software_report`
- `review_task(task_id, feature_id, review_status, edited_params)`：VALIDATED → REVIEWED
  - 单轮审核，与阶段 5/6 一致
  - `edited` 时若工程师覆盖了 `is_safe_to_machine`，需重新计算最终安全判定
- `confirm_task(task_id, reviewer)`：REVIEWED → SUCCEEDED
  - 导出 CAM 校验报告 JSON 到 `output_dir/{task_id}.cam_report.json`
  - 导出内部预校验详细报告到 `output_dir/{task_id}.internal_report.json`（供前端可视化）
  - **SUCCEEDED 后禁止删除**（`allow_delete_succeeded=False` 硬约束）
- `delete_task(task_id)`：仅允许删除 PENDING / FAILED / TIMEOUT 状态任务

**线程安全**：

- `TaskStore` 使用 `threading.Lock` 保护 `_tasks` 字典
- 审核操作使用独立的 `_review_lock` 防止并发审核冲突
- 导出操作使用 `_export_lock` 防止文件写入竞争
- CAM 软件调用使用 `_cam_call_lock` 防止 NX/PowerMill 并发实例崩溃

#### 11. API 路由 `app/api/v1/cam_validation/routes.py`

prefix `/api/v1/cam-validation`，11 个端点（与阶段 5/6 结构对齐）：

| # | 方法 | 路径 | 说明 |
|---|------|------|------|
| 1 | GET | `/precision_info` | 查询可用 CAM 后端 + 工业硬门槛（不创建任务，前端入口展示） |
| 2 | POST | `/tasks` | 创建 CAM 校验任务（PENDING） |
| 3 | POST | `/tasks/{task_id}/run` | 异步触发流水线执行（PENDING → RUNNING → VALIDATED） |
| 4 | GET | `/tasks/{task_id}` | 查询任务状态（含审核进度 + 校验统计） |
| 5 | GET | `/tasks` | 列出最近任务（支持状态过滤） |
| 6 | GET | `/tasks/{task_id}/result` | 获取 CAM 校验结果列表 + 审核状态 |
| 7 | POST | `/tasks/{task_id}/review` | 工程师审核单个特征校验结果（VALIDATED → REVIEWED） |
| 8 | POST | `/tasks/{task_id}/confirm` | 确认任务（REVIEWED → SUCCEEDED + 导出 CAM 报告 + 内部报告 JSON） |
| 9 | GET | `/tasks/{task_id}/report/download` | 下载 CAM 校验报告 JSON（FileResponse，仅 SUCCEEDED 可下载） |
| 10 | GET | `/tasks/{task_id}/internal_report/download` | 下载内部预校验详细报告 JSON（FileResponse，可视化用） |
| 11 | DELETE | `/tasks/{task_id}` | 取消/删除任务（仅 PENDING / FAILED / TIMEOUT 可删，SUCCEEDED 禁删） |

**安全约束（项目记忆硬约束）**：

- 所有 NOT_FOUND 错误响应**不回显 task_id**，统一返回 `"任务不存在或已被删除"`，防止枚举攻击
- 下载端点（#9 / #10）在任务不存在 / 状态非 SUCCEEDED / 文件路径为空 / 文件不存在 时
  统一返回 `JSONResponse(status_code=4xx, content=error(...))`，不抛 `HTTPException`，
  与模块其他端点响应格式保持一致

#### 12. main.py 集成

在 `main.py` 第 976 行附近（阶段 6 注册块之后）追加阶段 7 注释块：

```python
# cam_validation CAM 校验接入：依赖阶段 6 G 代码 + 审核记录 JSON。
# 复用 app.simulation.collision_detector.CollisionDetector + toolpath_parser.ToolpathParser。
# ADR-018 阶段 7：G 代码 → 内部预校验（CollisionDetector）→ CAM 软件二次校验（NX/PowerMill/PyCAM/手动）
#   → 工程师审核 → 导出 CAM 校验报告 JSON（链路最终产物，不触及物理机床）
# 设计原则（项目记忆硬约束）：
# - 本模块是「工程师助手」，非「全自动 CAM 校验器」
# - 内部预校验（CollisionDetector）是 AABB 快速预筛，不可替代 CAM 软件二次校验
# - 系统绝不直接接口 CNC 控制器，CAM 软件二次校验通过 subprocess 调用
# - cam_validation_required 始终 True，不可由环境变量关闭
# - CAM 软件不可用时自动降级到「手动校验流程」模式
# - SUCCEEDED 状态禁止删除（链路最终产物，需保留供审计追溯）
```

## 理由

### 方案对比

#### 方案 A：重写 CAM 校验器（独立实现完整刀轨仿真）

- 优点：完全可控，可针对阶段 7 需求定制
- 缺点：**违反 DRY 原则**；现有 `CollisionDetector` 已有完整 AABB 碰撞检测 + 5 轴支持，
  重写将丢失全部测试资产；工业级 CAM 软件（NX/PowerMill）的刀轨仿真核心不可重写
  （需 licensed SDK + 数年工程投入），违反项目记忆「优先工程可用性」原则

#### 方案 B：复用 CollisionDetector + 新增 CAM 软件适配层（**已选**）

- 优点：复用现有 `CollisionDetector` + `ToolpathParser` 测试资产；CAM 软件适配层专注
  subprocess 调用与结果归一化；模块边界清晰，阶段 7 逻辑独立于 simulation 模块；
  符合「工程师助手」定位（适配层只做校验调用与归因，不改变碰撞检测核心逻辑）
- 缺点：需理解现有 `CollisionDetector` 接口；CAM 软件适配层与 NX/PowerMill SDK 之间存在
  「subprocess + JSON 报告」的解耦（避免直接绑定 SDK 版本）

#### 方案 C：直接修改 `CollisionDetector` 加入 G 代码文件路径参数

- 优点：最少代码量
- 缺点：**破坏 ADR-005 契约稳定性**；`CollisionDetector` 是 simulation 模块的通用碰撞检测器，
  加入阶段 7 特定参数（gcode_file_path / feature_results）会导致职责混淆；
  现有测试用例需全部回归；违反「阶段 7 不修改现有基础设施」的硬约束

#### 方案 D：在 `app/gcode_generation/` 模块内直接做 CAM 校验

- 优点：无需新模块
- 缺点：违反单一职责原则；`gcode_generation` 模块定位为「G 代码生成」，
  做 CAM 校验超出其职责范围；阶段 6 SUCCEEDED 后禁止删除，但 CAM 校验失败需要回退，
  状态机会冲突

**选择方案 B** 的关键理由：

1. 复用现有 `CollisionDetector` + `ToolpathParser` 测试资产，工程可用性最高
2. CAM 软件适配层专注 subprocess 调用与结果归一化，职责单一
3. 不修改 `CollisionDetector` 与 `simulation` 模块，保持 ADR-005 契约稳定
4. 模块边界清晰，CAM 校验报告作为链路最终产物可独立审计追溯

### 设计权衡

#### Q1：为什么 `InternalValidator` 用组合而非继承？

`CollisionDetector` 已有完整测试用例覆盖其 `check_segments()` 方法。若 `InternalValidator` 继承
`CollisionDetector` 并重写 `check_segments()`，将导致测试用例对子类行为产生假设，引入回归风险。
组合（has-a）使 `InternalValidator` 仅作为外部调用者，不改变 `CollisionDetector` 行为。

#### Q2：为什么需要「内部预校验 + CAM 软件二次校验」双层？

- **内部预校验（CollisionDetector）**：秒级反馈，AABB 包围盒级别，捕获明显的刀柄-工件碰撞 /
  工作空间超限 / 安全 Z 违规。**不可替代** CAM 软件二次校验（无法检测刀轨几何精度 / 切削力 /
  机床运动学 / 后处理器语法兼容性）
- **CAM 软件二次校验（NX/PowerMill/PyCAM）**：分钟级反馈，完整刀轨仿真 + 碰撞检测 +
  机床运动学验证。**工业级上机前的强制环节**，但耗时较长且依赖 licensed SDK

两层校验互补：内部预校验快速筛掉明显问题，CAM 软件二次校验捕获深层问题。

#### Q3：为什么 SUCCEEDED 状态禁止删除？

阶段 7 SUCCEEDED 任务导出的 CAM 校验报告 JSON 是整条 7 阶段链路的最终产物，
可能被审计 / 合规 / 论文引用。删除将破坏全链路数据完整性。与阶段 5/6 的
`SUCCEEDED 禁删硬约束`一致，`allow_delete_succeeded` 强制 False，不可由环境变量开启。

#### Q4：为什么 CAM 校验强制 True，不可关闭？

项目记忆硬约束：**生成的 G 代码必须经 CAM 软件二次校验后方可上机**。
`cam_validation_required` 始终 True，告知文本中明确标注「不可直接上机」。
即使阶段 7 内部预校验通过，也必须经 NX/PowerMill/PyCAM 二次校验（含刀轨仿真、
碰撞检测、机床运动学验证）后才能上机。

但 `cam_backend == "internal_only"` 是合法选项：表示本次仅做内部预校验，
告知文本必须明确标注「未执行 CAM 软件二次校验，不可上机」。

#### Q5：为什么不直接接口 CNC 控制器？

大一独立项目无法独立完成物理机床执行环节（需持证操作员 + 导师签字 + 保险）。
即使能接口，也违反项目记忆硬约束「系统绝不直接接口 CNC 控制器」。
阶段 7 产物终止于「CAM 校验报告 JSON」，物理机床执行由人工 + CAM 软件 + 持证操作员完成。

#### Q6：为什么 `task_id` 前缀用 `"cam_"` 而非 `"cam_validation_"`？

与阶段 5 的 `"ch_"` / 阶段 6 的 `"gc_"` 前缀对齐（短前缀 + 下划线）。`"cam_"` 简洁且不与现有前缀冲突
（`ch_` / `cp_` / `pg_` / `fe_` / `i3d_` / `gc_`）。

#### Q7：为什么环境变量前缀用 `LNN_CAM_*`？

与阶段 5 的 `LNN_CH_*`、阶段 4 的 `LNN_CP_*`、阶段 6 的 `LNN_GC_*` 对齐。`CAM` = CAM Validation。

#### Q8：为什么 CAM 软件不可用时自动降级到「手动校验流程」？

工业级 CAM 软件（NX / PowerMill）需 licensed SDK + Windows 环境配置，桌面版无法保证可用。
PyCAM 是开源 Python 库但功能有限（仅刀轨可视化，无完整碰撞检测）。
为避免链路中断，当所有 CAM 软件后端不可用时，系统自动降级到「手动校验流程」模式：

1. 系统生成校验清单 markdown（含 G 代码文件路径 + 控制器类型 + 期望校验项）
2. 工程师手动加载 G 代码到 CAM 软件（NX/PowerMill/PyCAM）
3. 工程师在前端回填校验结果（pass / fail + 诊断消息）
4. 系统将回填结果写入 `CamSoftwareReport`，告知文本标注「人工校验」

这是「工程师助手」定位的体现：系统提供流程支撑，工程师做最终决策。

#### Q9：为什么导出两个 JSON 文件（cam_report + internal_report）？

- `cam_report.json`：链路最终产物，包含双层校验结果 + 工程师审核记录 + 工业硬门槛告知。
  供审计 / 合规 / 论文引用。
- `internal_report.json`：内部预校验详细报告，包含 `CollisionReport.events` 完整列表 +
  特征归因细节。供前端可视化（碰撞事件 3D 可视化 / 刀轨高亮）。

两个文件职责不同：cam_report 是「最终结论」，internal_report 是「调试细节」。

## 后果

### 积极影响

1. 复用现有 `CollisionDetector` + `ToolpathParser` 测试资产，工程可用性最高
2. CAM 软件适配层独立于 simulation 模块，可独立测试与演进
3. 不修改 `CollisionDetector` 与 `simulation` 模块，保持 ADR-005 契约稳定
4. 模块边界清晰，CAM 校验报告作为链路最终产物可独立审计追溯
5. 状态机与阶段 5/6 一致（PENDING → RUNNING → VALIDATED → REVIEWED → SUCCEEDED），
   降低工程师学习成本
6. SUCCEEDED 禁删硬约束保护全链路数据完整性
7. CAM 校验强制告知文本明确标注「不可直接上机」，符合工程边界
8. 双层校验互补：内部预校验快速筛掉明显问题，CAM 软件二次校验捕获深层问题
9. CAM 软件不可用时自动降级到「手动校验流程」，链路不中断
10. 导出格式按职责区分（cam_report 最终结论 / internal_report 调试细节）

### 负面影响

1. 新增 `app/cam_validation/` 模块（6 个子模块 + API 路由），代码量增加
2. `InternalValidator` 与 `CollisionDetector` 之间存在组合关系，理解成本略增
  （需阅读两个模块才能理解完整流程）
3. `CamAdapter` 与 NX/PowerMill SDK 之间存在 subprocess + JSON 解耦，
  SDK 版本升级可能需要更新子适配器
4. CAM 软件不可用时降级到「手动校验流程」增加人工环节（符合「工程师助手」定位但限制自动化程度）
5. SUCCEEDED 禁删可能导致任务存储无限增长，需配合 `task_retention_hours`
  定期清理过期任务（仅清理 FAILED / TIMEOUT，SUCCEEDED 永久保留）
6. 不直接接口 CNC 控制器意味着 CAM 校验报告需人工加载到 CAM 软件，
  无法实现全自动上机（符合工程边界但限制自动化程度）

## 实现产物

### 核心模块

| 文件 | 行数（预估） | 职责 |
|------|------|------|
| `app/cam_validation/__init__.py` | ~100 | 模块公开符号导出 |
| `app/cam_validation/cam_store.py` | ~400 | 任务存储 + 状态机 + 审核枚举 + `FeatureValidationResult` |
| `app/cam_validation/cam_disclaimer.py` | ~180 | 校验告知 + CAM 校验强制告知 + 工业硬门槛 |
| `app/cam_validation/gcode_loader.py` | ~220 | 加载阶段 6 G 代码 + report.json |
| `app/cam_validation/internal_validator.py` | ~380 | 复用 CollisionDetector + 特征归因 |
| `app/cam_validation/cam_adapter.py` | ~450 | NX/PowerMill/PyCAM/手动 兜底适配 |
| `app/cam_validation/pipeline.py` | ~720 | 编排器 + 状态机 + 审核 + 导出 |
| `app/api/v1/cam_validation/routes.py` | ~1500 | 11 个 API 端点（含 `precision_info` + 2 个下载端点） |
| `app/config/__init__.py`（追加） | ~55 | `CamValidationConfig` 12 字段 |

### API 路由

11 个端点，prefix `/api/v1/cam-validation`（详见决策第 11 节）。

### 配置

`CamValidationConfig` 12 字段，环境变量前缀 `LNN_CAM_*`（详见决策第 2 节）。

### main.py 集成

第 976 行附近追加阶段 7 注释块 + 模块可用性检测（详见决策第 12 节）。

### 测试与验证

- `python/tests/test_cam_validation.py`：pytest 用例（状态机 / 审核 / 导出 / 双层校验 /
  CAM 后端降级 / SUCCEEDED 禁删 / 不直接接口 CNC 告知）
- `python/tests/standalone_verify_cam_validation_pipeline.py`：pipeline.py 端到端独立验证
- `python/tests/standalone_verify_cam_validation_routes.py`：routes.py 端到端独立验证
  - 覆盖 17 个测试组：路由注册 / precision_info / 任务创建 / 错误处理 / run / 状态查询 /
    列表 / 结果查询 / review / confirm / confirm 错误场景 / cam_report 下载 /
    internal_report 下载 / delete / disclaimer 默认 / disclaimer 带 task / 上游追溯
  - WinSock 损坏绕过补丁：`_overlapped` 空实现 + `socket.socketpair()` mock + `select.select()` patch

### 实现进度记录

| 阶段 | 任务 | 状态 | 验证方式 |
|------|------|------|----------|
| s7-1 | ADR-018 设计规范 | 完成 | 本文件（596 行设计规范） |
| s7-2 | CamValidationConfig 12 字段 | 完成 | config 运行时访问通过 |
| s7-3 | CamValidationConfig 接入 config | 完成 | s7-12 验证 3 通过 |
| s7-4 | cam_store.py 任务存储 + 状态机 | 完成 | 单元测试通过 |
| s7-5 | cam_disclaimer.py 校验告知 | 完成 | 单元测试通过 |
| s7-6 | gcode_loader.py G 代码加载 | 完成 | 10/10 独立验证通过 |
| s7-7 | internal_validator.py 内部预校验 | 完成 | 22/22 独立验证通过 |
| s7-8 | cam_adapter.py CAM 软件适配层 | 完成 | 100/100 独立验证通过 |
| s7-9-impl | pipeline.py 编排器实现 | 完成 | 1176 行实现 |
| s7-9-init | __init__.py 符号导出 | 完成 | 模块导入通过 |
| s7-9-verify | pipeline 完整验证 | 完成 | 端到端验证通过 |
| s7-10 | routes.py 11 端点实现 | 完成 | 1523 行实现 |
| s7-11 | main.py 集成（权限种子 + 路由注册） | 完成 | AST 静态解析 4/4 通过 |
| s7-12 | 独立验证脚本 + ADR-018 定稿 | 完成 | 运行时集成 3/3 通过 |

### s7-11 验证记录（AST 静态解析方式）

由于 Python 3.14 + Windows 环境下 SQLAlchemy 兼容性问题（`metadata` 保留属性错误）+
CORS 模块级硬校验阻塞 main.py 运行时导入，s7-11 采用 AST 静态解析方式验证：

- `PRESET_PERMISSIONS`：101 个权限码，7 个 `cam_validation:*` 全部注册
- `admin` 角色：101 个权限，7 个 `cam_validation:*` 全部授予
- `engineer` 角色：30 个权限，7 个 `cam_validation:*` 全部授予
- `main.py`：`_CAM_VALIDATION_AVAILABLE` 声明 + `cam_validation_routes` 导入 +
  `app.include_router` 调用全部识别
- `routes.py`：11 端点全部识别
- `config.cam_validation.enabled = True`

### s7-12 验证记录（运行时集成）

s7-12 独立验证脚本（`_verify_s7_12.py`）3/3 通过：

1. **cors_config.py 既有 bug 修复**：`PRODUCTION_ORIGIN_REGEX` 末尾多余 `$` 已删除
   （`re.fullmatch` 已隐式锚定整个字符串，无需显式 `$`）；`is_allowed_origin` 6 个测试用例全部通过
2. **cam_validation routes 模块运行时导入**：`router.routes` 11 端点全部注册成功
3. **config.cam_validation.enabled 运行时可访问**：`enabled = True`

**既有 bug 修复记录**：

`cors_config.py` 中 `PRODUCTION_ORIGIN_REGEX = r"https?://localhost(:\d+)?$"` 末尾多了一个 `$`，
与模块级硬校验（line ~701）期望的 `r"https?://localhost(:\d+)?"`（无 `$`）不匹配，
导致 `import app.main` 时触发 `CorsConfigError`。该 bug 是既有代码问题，非阶段 7 引入，
但阻塞 s7-12 运行时验证。修复方式：删除末尾 `$`（`re.fullmatch` 已隐式锚定，行为不变）。

### 验收标准

- [x] `app/cam_validation/` 6 个子模块全部实现
- [x] `CamValidationConfig` 12 字段全部接入 `app/config/__init__.py`
- [x] 11 个 API 端点全部注册并可访问（`router.routes 数量 == 11` 断言通过）
- [x] main.py 集成注释块 + 模块可用性检测
- [x] 状态机 5 个状态全部覆盖（PENDING / RUNNING / VALIDATED / REVIEWED / SUCCEEDED）
- [x] FAILED / TIMEOUT 异常状态覆盖
- [x] `GCodeLoader` 校验阶段 6 `task_status == succeeded` 后才加载
- [x] `InternalValidator` 正确复用 `CollisionDetector.check_segments()` / `check_segments_5axis()`
- [x] 碰撞事件按 `block_number` 正确归因到 `feature_results.line_range`
- [x] `CamAdapter` 5 个子后端全部实现（internal_only / pycam / nx_open / powermill / manual）
- [x] CAM 软件不可用时自动降级到 `manual`，告知文本标注降级原因
- [x] SUCCEEDED 禁删硬约束（`allow_delete_succeeded=False`）
- [x] `cam_validation_required` 始终 True（断言覆盖所有响应）
- [x] HRC52 `pending_calibration` 置信度告知文本正确（继承阶段 5）
- [x] 审核枚举 4 个状态（pending / confirmed / rejected / edited）
- [x] `edited` 修改 `is_safe_to_machine` 后重新计算最终安全判定
- [x] 线程安全：`TaskStore` / 审核操作 / 导出操作 / CAM 调用均加锁
- [x] pipeline.py 独立验证全部检查点通过
- [x] routes.py 独立验证全部检查点通过
- [x] NOT_FOUND 错误响应不回显 task_id（防枚举攻击）
- [x] 下载端点统一返回 JSONResponse + error()，不抛 HTTPException
- [x] 内部预校验局限告知文本明确标注「不可替代 CAM 软件二次校验」
- [x] ADR-018 文档完整（本文件）

## 补记（2026-09）：体素材料去除仿真层 + DNC 下发闸门

优化升级路线图 A 线「仿真强制闭环」在本 ADR 的双层校验中插入第三层，
并新增下发侧闸门：

1. **VoxelValidator（第三层，闭环核心）**：`app/cam_validation/voxel_validator.py`。
   真实体素材料去除仿真（区别于 CollisionDetector 的 AABB 预筛）：合成盒状
   毛坯体素网格（零 trimesh/STL 依赖），复用 `VoxelCutter` 切削原语（Rust
   compute-core 加速、自动回退 Python），检测**切削段过切毛坯底面**与
   **快速段安全高度下切入材料**两类致命碰撞。按 `line_range` 归因到特征
   （口径与 InternalValidator 一致）。结果写入任务级 `voxel_check_passed`
   （None = 未执行，DNC 闸门按未通过处理）并随 cam_report.json 导出。
   无开关（硬约束，与 cam_validation_required 同级）；性能旋钮
   `LNN_CAM_VOXEL_SIZE_MM` / `LNN_CAM_VOXEL_TOOL_DIAMETER_MM` /
   `LNN_CAM_VOXEL_TOOL_TYPE` / `LNN_CAM_VOXEL_MAX_SEGMENTS`（超限 fail-closed）。
2. **DNC 下发闸门**：`app/dnc/nc_gate.py` + `api/v1/dnc.py` 发送端点。
   NC 程序必须追溯到一个 SUCCEEDED 且 `voxel_check_passed=True` 的阶段 7
   任务才允许发送到机床，否则 403 + `ErrorCode.NC_NOT_VALIDATED`（8013）。
   逃生阀 `LNN_DNC_ALLOW_UNVALIDATED_NC=1`（默认关闭；开启后每次放行
   写入哈希链审计日志，审计写入失败则放行失败，fail-closed）。
3. **测试**：`tests/unit/test_voxel_validator.py`、`tests/unit/test_cam_voxel_pipeline.py`、
   `tests/unit/test_dnc_nc_gate.py`、`tests/api/test_dnc_api.py`（闸门契约）。

「导出」侧不设闸的决策：阶段 6 的导出产物（report.json + G 代码文件）是阶段 7
的输入，在导出处拦截会造成流水线死锁；阶段 6 的响应与 disclaimer 已声明产物
仅供校验参考。闭环的强制点在「下发」（唯一通向物理机床的动作）。

## 后续工作

1. **CAM 软件实际接入**：当 NX Open / PowerMill SDK 可用时，完善 `_NxOpenBackend` /
   `_PowerMillBackend` 的 subprocess 调用脚本（当前为 stub + manual 兜底）
2. **PyCAM 集成**：评估 PyCAM 开源库的刀轨仿真功能完整度，决定是否作为默认后端
3. **碰撞事件 3D 可视化**：前端基于 `internal_report.json` 渲染碰撞事件 3D 可视化 +
   刀轨高亮（关联 ADR-016 可解释性可视化）
4. **CAM 校验报告版本管理**：SUCCEEDED 任务永久保留，未来可加入版本对比功能
5. **物理机床执行（超出阶段 7 范围）**：需持证操作员 + 导师签字 + 保险，
  由人工 + CAM 软件 + 持证操作员完成，阶段 7 不触及

## 参考

- ADR-005：核心架构契约设计
- ADR-013：颤振预测接入（ChatterReport 间接来源）
- ADR-014：G 代码生成接入（gcode_file_path + report.json 直接来源）
- 项目记忆硬约束：`cam_validation_required` 始终 True / SUCCEEDED 禁删 /
  HRC52 `pending_calibration` / 系统不直接接口 CNC 控制器 / 大一独立项目不触及物理机床
- 现有 CAM 基础设施：`app/simulation/collision_detector.py` + `toolpath_parser.py` +
  `stock_model.py` + `app/api/v1/collision_check.py`
