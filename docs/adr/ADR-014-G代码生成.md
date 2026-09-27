# ADR-014: 阶段 6 G 代码生成接入

**日期**: 2026-07-14
**状态**: 已接受
**决策者**: 灵境制造团队
**关联**: ADR-008（参数化几何输出）、ADR-009（切削参数推荐）、ADR-013（颤振预测接入）

---

## 背景

阶段 5（ADR-013）已交付 `ChatterPredictionPipeline`，输出 `ChatterReport` JSON，其中每个特征携带
`limit_depth_mm`（极限切深）、`axial_depth_mm`（实际切深）、`stable`（0/1）与 `confidence`
（HRC52 强制降至 0.5）。阶段 6 的工程任务是：**消费 ChatterReport，结合阶段 4 切削参数与
阶段 3 OperationPlan，生成可在 CAM 软件（NX/PowerMill/PyCAM）中二次校验的数控 G 代码**。

### 现有基础设施（不可重写）

项目已有完善的 G 代码生成基础设施，阶段 6 必须**复用而非重写**：

1. `app/postprocessor/` 包：模块化后处理器，支持 10+ 控制器（fanuc / siemens / heidenhain /
   knd / gsk / mitsubishi / hnc / fagor / xmachine），由 YAML 配置驱动，212 个测试用例覆盖。
2. `app/process_planning/gcode_generator.py`：已有 `GCodeGenerator` 类 + `GCodeResult` dataclass，
   接受 `OperationPlan` 输入，输出完整 G 代码文本 + 断点续传标记 + 语法校验。
3. `app/process_planning/operation_sequencer.py`：`OperationPlan` + `Operation` dataclass。
4. `app/toolpath/gcode_postprocessor.py`：**已废弃**（仅向后兼容保留），新代码必须使用
   `app.postprocessor` 包，禁止使用此文件。

### 项目记忆硬约束（阶段 6 必须遵守）

- 系统定位为「工程师助手」，非「全自动生产线」
- **生成的 G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验后方可上机**；
  系统绝不直接接口 CNC 控制器
- `cam_validation_required` 始终 True，不可由环境变量关闭
- HRC52 数据 `pending_calibration` 时置信度强制降至 0.5（阶段 5 已实现，阶段 6 继承）
- 安全裕度 `SAFETY_MARGIN_RATIO=0.8`：实际切深超过极限切深 80% 时发出警告，
  阶段 6 生成 G 代码时若 `axial_depth > 0.8 × limit_depth`，必须在 `warnings` 中标注
- K_s → cutting_force_coeff 直接传递（阶段 4/5 已实现，阶段 6 不二次拟合）
- SUCCEEDED 状态禁止删除（阶段 7 CAM 校验可能已引用 G 代码产物）
- 推理路径禁止 `fit_transform`；MC dropout 模式切换需锁保护（阶段 6 不涉及模型推理，
  但继承告知文本必须保留）

### 阶段 6 工程边界（与阶段 5 一致）

- 大一独立项目，无法独立完成物理机床执行环节（需持证操作员 + 导师签字 + 保险）
- 阶段 6 的产物终止于「CAM 软件可加载的 G 代码文件 + 工程师审核记录」，不触及物理机床
- mesh → parametric CAD 自动转换在工业界未解决；阶段 6 假设上游 OperationPlan 已由
  工程师确认（阶段 3 已实现 human-in-the-loop）

## 决策

采用「**复用现有 GCodeGenerator + 新增 ChatterReport 适配层 + 任务存储与状态机 + 工程师审核**」
方案，**不修改 `app/postprocessor/` 包与 `app/process_planning/gcode_generator.py`**（避免破坏
212 个测试用例与 ADR-005 契约稳定性），新增独立的 `app/gcode_generation/` 模块承载阶段 6 逻辑。

### 7 阶段全链路表

| 阶段 | 模块 | ADR | 输入 | 输出 | 状态 |
|------|------|-----|------|------|------|
| 1 | image_to_3d | ADR-006 | 照片 | 点云 / mesh | 已完成 |
| 2 | feature_extraction | ADR-007 | mesh | 几何特征 JSON | 已完成 |
| 3 | parametric_geometry | ADR-008 | 特征 JSON | STEP + OperationPlan 草案 | 已完成 |
| 4 | cutting_parameters | ADR-009 | 特征 + 材料 | ChatterParams JSON | 已完成 |
| 5 | chatter_prediction | ADR-013 | ChatterParams | ChatterReport JSON | 已完成 |
| **6** | **gcode_generation** | **ADR-014** | **ChatterReport + OperationPlan** | **G 代码 + 审核记录** | **本 ADR** |
| 7 | cam_validation | ADR-015 | G 代码 | CAM 校验报告 | 待启动 |

### 实现要点

#### 1. 模块结构 `app/gcode_generation/`

```
app/gcode_generation/
├── __init__.py                  # 模块公开符号导出
├── gcode_store.py               # 任务存储 + 状态机 + 审核枚举
├── gcode_disclaimer.py          # 精度告知 + CAM 校验强制告知
├── chatter_report_loader.py     # 消费阶段 5 ChatterReport JSON
├── generator_adapter.py         # 封装 GCodeGenerator + 安全裕度适配
└── pipeline.py                  # 编排器 + 状态机 + 审核 + 导出
```

不新增独立的 `predictor_adapter.py`（阶段 6 不涉及模型推理）；
不新增 `tests/` 子目录（测试统一放 `python/tests/test_gcode_generation.py` + `standalone_verify_gcode_generation.py`）。

#### 2. 配置字段 `GCodeGenerationConfig`（追加到 `app/config/__init__.py`）

11 个字段，命名与阶段 5 的 `ChatterPredictionConfig` 对齐：

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | True | 模块启用开关 |
| `output_dir` | str | "outputs/gcode" | G 代码产物输出目录 |
| `max_concurrent` | int | 4 | 最大并发任务数 |
| `task_timeout_seconds` | int | 300 | 单任务超时 |
| `task_retention_hours` | int | 168 | 任务保留时长（7 天） |
| `precision_tier` | str | "mesh_calibrated" | 精度档位（继承上游） |
| `default_controller_type` | str | "fanuc_0i" | 默认控制器（fanuc_0i / siemens_840d / heidenhain_tnc / xmachine_xm100） |
| `default_safe_z` | float | 80.0 | 默认安全 Z 高度 (mm) |
| `default_stock_top_z` | float | 50.0 | 默认毛坯顶面 Z (mm) |
| `allow_delete_succeeded` | bool | False | SUCCEEDED 禁删硬约束（不可由环境变量开启） |
| `cam_validation_required` | bool | True | CAM 校验强制（始终 True，不可关闭） |

环境变量前缀 `LNN_GC_*`（如 `LNN_GC_ENABLED`、`LNN_GC_OUTPUT_DIR` 等）。

#### 3. 状态机 `GCodeGenerationTaskStatus`（与阶段 5 对齐）

```
PENDING → RUNNING → GENERATED → REVIEWED → SUCCEEDED
                ↘ FAILED
                ↘ TIMEOUT
```

- `PENDING`：任务已创建，等待执行
- `RUNNING`：正在加载 ChatterReport + 调用 GCodeGenerator
- `GENERATED`：G 代码已生成，等待工程师审核
- `REVIEWED`：工程师已审核（confirmed / rejected / edited）
- `SUCCEEDED`：审核通过，G 代码已导出至 `output_dir`，**禁止删除**
- `FAILED`：生成失败（ChatterReport 加载失败 / GCodeGenerator 抛错 / 语法校验失败）
- `TIMEOUT`：超过 `task_timeout_seconds`

审核枚举 `GCodeReviewStatus`：`pending` / `confirmed` / `rejected` / `edited`（与阶段 5 一致）。

#### 4. 任务存储 `GCodeGenerationTask` dataclass

关键字段：

- `task_id`：前缀 `"gc_"` + uuid（如 `gc_550e8400-e29b-41d4-a716-446655440000`）
- `source_chatter_report_path`：阶段 5 ChatterReport JSON 路径
- `source_operation_plan_path`：阶段 3 OperationPlan JSON 路径
- `controller_type`：目标控制器（fanuc_0i / siemens_840d / heidenhain_tnc / xmachine_xm100）
- `material_name`：材料名称（用于 G 代码注释）
- `program_number`：程序号（O 号，默认 1000）
- `safe_z` / `stock_top_z`：安全 Z / 毛坯顶面 Z
- `feature_gcode_results`：`list[FeatureGCodeResult]`，每个特征一条
- `gcode_text`：最终合并的 G 代码文本
- `gcode_report_path`：输出给阶段 7 的 JSON 路径
- `cam_validation_required`：始终 True
- `started_at` / `completed_at` / `reviewed_by` / `reviewed_at`
- `warnings` / `errors`：生成过程中的警告与错误

#### 5. `FeatureGCodeResult` dataclass（每条特征的 G 代码结果）

```python
@dataclass
class FeatureGCodeResult:
    feature_id: str
    feature_type: str  # plane / cylinder / hole / boss
    material_id: str
    spindle_rpm: float
    axial_depth_mm: float       # 实际切深（来自阶段 5）
    limit_depth_mm: float       # 极限切深（来自阶段 5）
    stable: bool                # 是否稳定（来自阶段 5）
    safety_margin_ratio: float  # 安全裕度（axial / limit，应 ≤ 0.8）
    gcode_lines: list[str]      # 该特征的 G 代码行
    line_range: tuple[int, int] # 在最终程序中的行号范围 [start, end]
    warning: str = ""           # 安全裕度警告（若 axial > 0.8 × limit）
```

#### 6. 精度告知 `GCodeDisclaimer`（参考 `ChatterDisclaimer`）

告知文本包含：

- **精度继承链**：阶段 1 image_to_3d.precision_tier → 阶段 2/3/4 → 阶段 5 → 阶段 6
- **HRC52 待校准**：`pending_calibration` 材料的置信度强制降至 0.5
- **CAM 校验强制**：G 代码必须经 NX / PowerMill / PyCAM 二次校验，**不可直接上机**
- **物理机床执行限制**：需持证操作员 + 导师签字 + 保险
- **安全裕度**：`SAFETY_MARGIN_RATIO=0.8`，超过时 `warnings` 中标注
- **LTC 神经网络实验性**：阶段 6 不直接调用 LTC，但若阶段 5 ChatterReport 标注
  `prediction_method == "neural_network"`，阶段 6 必须在告知文本中标注「实验性路径」

#### 7. `ChatterReportLoader`：消费阶段 5 输出

职责：

- 读取阶段 5 导出的 `ChatterReport` JSON
- 校验必填字段（`feature_results` / `material_id` / `prediction_method` / `confidence`）
- 返回 `list[FeatureChatterResult]`（直接复用阶段 5 的 dataclass，不重新定义）
- 若 ChatterReport 不存在或字段缺失，抛出 `ChatterReportLoadError`
- 若 ChatterReport 的 `task_status != "SUCCEEDED"`，拒绝加载并提示「阶段 5 未审核通过」

#### 8. `GeneratorAdapter`：封装现有 `GCodeGenerator`

**核心设计：不继承 `GCodeGenerator`，而是组合（has-a）**，避免破坏 212 个测试用例。

```python
class GeneratorAdapter:
    """阶段 6 G 代码生成适配器。

    封装 app.process_planning.gcode_generator.GCodeGenerator，
    新增 ChatterReport 安全裕度适配层。
    """

    def __init__(self, config: GCodeGenerationConfig):
        self._config = config
        self._generator = GCodeGenerator()  # 组合，不继承

    def generate(
        self,
        operation_plan: OperationPlan,
        chatter_results: list[FeatureChatterResult],
        controller_type: str,
        material_name: str,
        program_number: int = 1000,
        safe_z: float = 80.0,
        stock_top_z: float = 50.0,
    ) -> tuple[GCodeResult, list[FeatureGCodeResult]]:
        """生成 G 代码 + 安全裕度标注。

        步骤：
        1. 调用现有 GCodeGenerator.generate() 生成基础 G 代码
        2. 遍历 chatter_results，为每个特征计算 safety_margin_ratio
        3. 若 axial_depth > 0.8 × limit_depth，在 warnings 中追加警告
        4. 若 stable == False，在 errors 中追加「不稳定特征」错误（可由工程师审核覆盖）
        5. 返回 (GCodeResult, list[FeatureGCodeResult])
        """
```

**安全裕度策略**：

- `safety_margin_ratio = axial_depth_mm / limit_depth_mm`（limit_depth_mm > 0 时）
- 若 `safety_margin_ratio > 0.8`：追加警告
  `"特征 {feature_id}: 实际切深 {axial:.2f}mm 超过极限切深 {limit:.2f}mm 的 80%，建议降低切深"`
- 若 `stable == False`：追加错误
  `"特征 {feature_id}: 颤振不稳定，禁止生成 G 代码（需工程师审核降低切深或主轴转速）"`
  - 此时 `GCodeResult.errors` 非空，`is_valid == False`，任务状态转为 `FAILED`
  - 工程师可在审核阶段 `edited` 修改切深后重新生成

#### 9. `GCodeGenerationPipeline`：编排器

职责：

- `create_task(...)`：创建 PENDING 任务
- `run_pipeline(task_id)`：PENDING → RUNNING → GENERATED（或 FAILED / TIMEOUT）
  - 加载 ChatterReport（`ChatterReportLoader`）
  - 加载 OperationPlan（从 JSON）
  - 调用 `GeneratorAdapter.generate()`
  - 执行语法校验（复用 `GCodeGenerator._validate_syntax`）
  - 写入 `gcode_text` + `feature_gcode_results`
- `review_task(task_id, review_status, edited_params)`：GENERATED → REVIEWED
  - 单轮审核，与阶段 5 一致
  - `edited` 时若修改了 `axial_depth`，需重新计算 `safety_margin_ratio` 并可能触发警告
- `confirm_task(task_id, reviewer)`：REVIEWED → SUCCEEDED
  - 导出 G 代码文本到 `output_dir/{task_id}.nc`（或 `.gcode` / `.mpf`，按控制器扩展名）
  - 导出审核记录 JSON 到 `output_dir/{task_id}.report.json`（供阶段 7 CAM 校验读取）
  - **SUCCEEDED 后禁止删除**（`allow_delete_succeeded=False` 硬约束）
- `export_gcode(task_id, format)`：导出 G 代码（fanuc → `.nc` / siemens → `.mpf` /
  heidenhain → `.h` / xmachine → `.nc`）
- `delete_task(task_id)`：仅允许删除 PENDING / FAILED / TIMEOUT 状态任务

**线程安全**：

- `TaskStore` 使用 `threading.Lock` 保护 `_tasks` 字典
- 审核操作使用独立的 `_review_lock` 防止并发审核冲突
- 导出操作使用 `_export_lock` 防止文件写入竞争

#### 10. API 路由 `app/api/v1/gcode_generation/routes.py`

prefix `/api/v1/gcode-generation`，11 个端点（与阶段 5 结构对齐，含独立的 `precision_info` 信息端点）：

| # | 方法 | 路径 | 说明 |
|---|------|------|------|
| 1 | GET | `/precision_info` | 查询精度档位 + 控制器类型 + 工业硬门槛（不创建任务，前端入口展示） |
| 2 | POST | `/tasks` | 创建 G 代码生成任务（PENDING） |
| 3 | POST | `/tasks/{task_id}/run` | 异步触发流水线执行（PENDING → RUNNING → GENERATED） |
| 4 | GET | `/tasks/{task_id}` | 查询任务状态（含审核进度 + 生成统计） |
| 5 | GET | `/tasks` | 列出最近任务（支持状态过滤） |
| 6 | GET | `/tasks/{task_id}/result` | 获取 G 代码生成结果列表 + 审核状态 |
| 7 | POST | `/tasks/{task_id}/review` | 工程师审核单个特征 G 代码段（GENERATED → REVIEWED） |
| 8 | POST | `/tasks/{task_id}/confirm` | 确认任务（REVIEWED → SUCCEEDED + 导出 G 代码 + 报告 JSON） |
| 9 | GET | `/tasks/{task_id}/gcode/download` | 下载 G 代码文件（FileResponse，仅 SUCCEEDED 可下载） |
| 10 | GET | `/tasks/{task_id}/report/download` | 下载审核记录 JSON（FileResponse，供阶段 7 CAM 校验读取） |
| 11 | DELETE | `/tasks/{task_id}` | 取消/删除任务（仅 PENDING / FAILED / TIMEOUT 可删，SUCCEEDED 禁删） |

**安全约束（项目记忆硬约束）**：

- 所有 NOT_FOUND 错误响应**不回显 task_id**，统一返回 `"任务不存在或已被删除"`，防止枚举攻击
- 下载端点（#9 / #10）在任务不存在 / 状态非 SUCCEEDED / 文件路径为空 / 文件不存在 时
  统一返回 `JSONResponse(status_code=4xx, content=error(...))`，不抛 `HTTPException`，
  与模块其他端点响应格式保持一致

#### 11. main.py 集成

在 `main.py` 第 212 行附近（阶段 5 注释块之后）追加阶段 6 注释块：

```python
# gcode_generation G 代码生成接入：依赖阶段 5 ChatterReport + 阶段 3 OperationPlan。
# 复用现有 app.postprocessor 包 + app.process_planning.gcode_generator.GCodeGenerator。
# ADR-014 阶段 6：ChatterReport + OperationPlan → GCodeGenerator 适配 → 安全裕度标注 → 工程师审核
#   → 导出 G 代码 + 审核记录 JSON（供阶段 7 CAM 校验）
# 设计原则（项目记忆硬约束）：
# - 本模块是「工程师助手」，非「全自动 G 代码生成器」
# - 生成的 G 代码必须经 CAM 软件（NX/PowerMill/PyCAM）二次校验，绝不直接接口 CNC 控制器
# - cam_validation_required 始终 True，不可由环境变量关闭
# - SAFETY_MARGIN_RATIO=0.8，超过时 warnings 中标注
# - stable == False 的特征禁止生成 G 代码，需工程师审核降低切深后重新生成
# - SUCCEEDED 状态禁止删除（阶段 7 CAM 校验可能已引用 G 代码产物）
```

## 理由

### 方案对比

#### 方案 A：重写 G 代码生成器（独立实现）

- 优点：完全可控，可针对阶段 6 需求定制
- 缺点：**违反 DRY 原则**；现有 `GCodeGenerator` 已有 212 个测试用例覆盖，
  重写将丢失全部测试资产；`app/postprocessor/` 包支持 10+ 控制器，重写工作量巨大；
  违反项目记忆「优先工程可用性」原则

#### 方案 B：复用现有 GCodeGenerator + 新增适配层（**已选**）

- 优点：复用 212 个测试用例 + 10+ 控制器支持；适配层专注 ChatterReport 安全裕度逻辑；
  模块边界清晰，阶段 6 逻辑独立于阶段 3 G 代码基础设施；符合「工程师助手」定位
  （适配层只做安全裕度标注，不改变 G 代码生成核心逻辑）
- 缺点：需理解现有 `GCodeGenerator` 接口；适配层与 `GCodeGenerator` 之间存在
  「组合优于继承」的设计约束（避免破坏现有测试）

#### 方案 C：直接修改 `GCodeGenerator` 加入 ChatterReport 参数

- 优点：最少代码量
- 缺点：**破坏 ADR-005 契约稳定性**；`GCodeGenerator` 是阶段 3 通用 G 代码生成器，
  加入阶段 5/6 特定参数会导致职责混淆；212 个测试用例需全部回归；
  违反「阶段 6 不修改现有基础设施」的硬约束

#### 方案 D：在 `app/chatter_prediction/` 模块内直接生成 G 代码

- 优点：无需新模块
- 缺点：违反单一职责原则；`chatter_prediction` 模块定位为「颤振预测」，
  生成 G 代码超出其职责范围；阶段 7 CAM 校验将难以引用清晰的模块边界

**选择方案 B** 的关键理由：

1. 复用现有 212 个测试用例 + 10+ 控制器支持，工程可用性最高
2. 适配层专注 ChatterReport 安全裕度逻辑，职责单一
3. 不修改 `GCodeGenerator` 与 `app/postprocessor/` 包，保持 ADR-005 契约稳定
4. 模块边界清晰，阶段 7 CAM 校验可直接引用 `gcode_generation` 模块

### 设计权衡

#### Q1：为什么 `GeneratorAdapter` 用组合而非继承？

`GCodeGenerator` 已有 212 个测试用例覆盖其 `generate()` 方法。若 `GeneratorAdapter` 继承
`GCodeGenerator` 并重写 `generate()`，将导致测试用例对子类行为产生假设，引入回归风险。
组合（has-a）使 `GeneratorAdapter` 仅作为外部调用者，不改变 `GCodeGenerator` 行为。

#### Q2：为什么 `stable == False` 的特征禁止生成 G 代码？

阶段 5 已计算 `stable` 字段（基于 `axial_depth < 0.8 × limit_depth`）。若特征不稳定，
直接生成 G 代码将导致机床颤振，可能损坏刀具或工件。阶段 6 的工程职责是
**拒绝生成不稳定特征的 G 代码**，强制工程师审核降低切深或主轴转速后重新生成。

但若工程师在审核阶段 `edited` 修改了 `axial_depth` 并重新计算稳定性为 `stable=True`，
则允许生成。这是「工程师助手」定位的体现：系统提供建议，工程师做最终决策。

#### Q3：为什么 SUCCEEDED 状态禁止删除？

阶段 7 CAM 校验可能已引用 SUCCEEDED 任务的 G 代码文件路径。删除将导致阶段 7
找不到文件，破坏全链路数据完整性。与阶段 5 的 `SUCCEEDED 禁删硬约束`一致，
`allow_delete_succeeded` 强制 False，不可由环境变量开启。

#### Q4：为什么 CAM 校验强制 True，不可关闭？

项目记忆硬约束：**生成的 G 代码必须经 CAM 软件二次校验后方可上机**。
`cam_validation_required` 始终 True，告知文本中明确标注「不可直接上机」。
即使阶段 6 G 代码语法校验通过，也必须在阶段 7 经 NX/PowerMill/PyCAM 校验
（含刀轨仿真、碰撞检测、机床运动学验证）后才能上机。

#### Q5：为什么不直接接口 CNC 控制器？

大一独立项目无法独立完成物理机床执行环节（需持证操作员 + 导师签字 + 保险）。
即使能接口，也违反项目记忆硬约束「系统绝不直接接口 CNC 控制器」。
阶段 6 产物终止于「CAM 软件可加载的 G 代码文件」，物理机床执行由人工 + CAM 软件完成。

#### Q6：为什么 `task_id` 前缀用 `"gc_"` 而非 `"gcode_"`？

与阶段 5 的 `"ch_"` 前缀对齐（短前缀 + 下划线）。`"gc_"` 简洁且不与现有前缀冲突
（`ch_` / `cp_` / `pg_` / `fe_` / `i3d_`）。

#### Q7：为什么环境变量前缀用 `LNN_GC_*`？

与阶段 5 的 `LNN_CH_*`、阶段 4 的 `LNN_CP_*` 对齐。`GC` = G-Code。

#### Q8：为什么复用阶段 5 的 `FeatureChatterResult` dataclass？

阶段 5 已定义 `FeatureChatterResult`（含 `limit_depth_mm` / `axial_depth_mm` / `stable` /
`confidence` / `prediction_method`）。阶段 6 的 `ChatterReportLoader` 直接返回
`list[FeatureChatterResult]`，避免重新定义相同结构。这是阶段 5 → 阶段 6 的契约继承。

#### Q9：为什么导出格式按控制器区分扩展名？

- fanuc → `.nc`（Fanuc 标准）
- siemens → `.mpf`（Siemens MPF 程序文件）
- heidenhain → `.h`（Heidenhain TNC 文件）
- xmachine → `.nc`（通用 NC 文件）

这是 CAM 软件加载 G 代码时的文件名约定，错误的扩展名可能导致 CAM 软件无法识别。

## 后果

### 积极影响

1. 复用现有 212 个测试用例 + 10+ 控制器支持，工程可用性最高
2. ChatterReport 安全裕度逻辑独立于 G 代码生成核心，可独立测试与演进
3. 不修改 `GCodeGenerator` 与 `app/postprocessor/` 包，保持 ADR-005 契约稳定
4. 模块边界清晰，阶段 7 CAM 校验可直接引用 `gcode_generation` 模块
5. 状态机与阶段 5 一致（PENDING → RUNNING → GENERATED → REVIEWED → SUCCEEDED），
   降低工程师学习成本
6. SUCCEEDED 禁删硬约束保护全链路数据完整性
7. CAM 校验强制告知文本明确标注「不可直接上机」，符合工程边界
8. `stable == False` 特征禁止生成 G 代码，从源头杜绝颤振风险
9. 安全裕度警告帮助工程师识别临界切深
10. 导出格式按控制器区分扩展名，与 CAM 软件约定对齐

### 负面影响

1. 新增 `app/gcode_generation/` 模块（6 个子模块 + API 路由），代码量增加
2. `GeneratorAdapter` 与 `GCodeGenerator` 之间存在组合关系，理解成本略增
   （需阅读两个模块才能理解完整流程）
3. `ChatterReportLoader` 依赖阶段 5 的 `ChatterReport` JSON 格式稳定，
   若阶段 5 修改导出格式需同步更新加载器
4. `stable == False` 特征禁止生成 G 代码可能导致部分任务无法完成
   （需工程师审核降低切深，符合「工程师助手」定位但增加人工环节）
5. SUCCEEDED 禁删可能导致任务存储无限增长，需配合 `task_retention_hours`
   定期清理过期任务（仅清理 FAILED / TIMEOUT，SUCCEEDED 永久保留）
6. 不直接接口 CNC 控制器意味着 G 代码需手动加载到 CAM 软件，
   无法实现全自动上机（符合工程边界但限制自动化程度）

## 实现产物

### 核心模块

| 文件 | 行数（预估） | 职责 |
|------|------|------|
| `app/gcode_generation/__init__.py` | ~100 | 模块公开符号导出 |
| `app/gcode_generation/gcode_store.py` | ~380 | 任务存储 + 状态机 + 审核枚举 + `FeatureGCodeResult` |
| `app/gcode_generation/gcode_disclaimer.py` | ~170 | 精度告知 + CAM 校验强制告知 |
| `app/gcode_generation/chatter_report_loader.py` | ~200 | 消费阶段 5 ChatterReport JSON |
| `app/gcode_generation/generator_adapter.py` | ~350 | 封装 GCodeGenerator + 安全裕度适配 |
| `app/gcode_generation/pipeline.py` | ~680 | 编排器 + 状态机 + 审核 + 导出 |
| `app/api/v1/gcode_generation/routes.py` | 1518 | 11 个 API 端点（含 `precision_info` + 2 个下载端点） |
| `app/config/__init__.py`（追加） | ~50 | `GCodeGenerationConfig` 11 字段 |

### API 路由

11 个端点，prefix `/api/v1/gcode-generation`（详见决策第 10 节）。

### 配置

`GCodeGenerationConfig` 11 字段，环境变量前缀 `LNN_GC_*`（详见决策第 2 节）。

### main.py 集成

第 212 行附近追加阶段 6 注释块 + 模块可用性检测（详见决策第 11 节）。

### 测试与验证

- `python/tests/test_gcode_generation.py`：pytest 用例（状态机 / 审核 / 导出 / 安全裕度 / CAM 校验告知 / SUCCEEDED 禁删）
- `python/standalone_verify_pipeline.py`：pipeline.py 端到端独立验证，**22/22 检查点全部通过**（s6-8v 阶段完成）
- `python/standalone_verify_routes.py`：routes.py 端到端独立验证，**69/69 检查点全部通过**（s6-11 阶段完成）
  - 覆盖 17 个测试组：路由注册 / precision_info / 任务创建 / 错误处理 / run / 状态查询 /
    列表 / 结果查询 / review / confirm / confirm 错误场景 / gcode 下载 / report 下载 /
    delete / disclaimer 默认 / disclaimer 带 task / 上游追溯
  - WinSock 损坏绕过补丁：`_overlapped` 空实现 + `socket.socketpair()` mock + `select.select()` patch

### 验收标准

- [x] `app/gcode_generation/` 6 个子模块全部实现
- [x] `GCodeGenerationConfig` 11 字段全部接入 `app/config/__init__.py`
- [x] 11 个 API 端点全部注册并可访问（`router.routes 数量 == 11` 断言通过）
- [x] main.py 集成注释块 + 模块可用性检测
- [x] 状态机 5 个状态全部覆盖（PENDING / RUNNING / GENERATED / REVIEWED / SUCCEEDED）
- [x] FAILED / TIMEOUT 异常状态覆盖
- [x] `stable == False` 特征拒绝生成 G 代码
- [x] 安全裕度警告（`axial > 0.8 × limit`）正确标注
- [x] SUCCEEDED 禁删硬约束（`allow_delete_succeeded=False`）
- [x] `cam_validation_required` 始终 True（断言 9/14/25/32/43 全部通过）
- [x] HRC52 `pending_calibration` 置信度告知文本正确
- [x] 导出格式按控制器区分扩展名（`.nc` / `.mpf` / `.h`）
- [x] 审核枚举 4 个状态（pending / confirmed / rejected / edited）
- [x] `edited` 修改 `axial_depth` 后重新计算 `safety_margin_ratio`
- [x] `ChatterReportLoader` 校验 `task_status == SUCCEEDED` 后才加载
- [x] 线程安全：`TaskStore` / 审核操作 / 导出操作均加锁
- [x] pipeline.py 独立验证 22/22 检查点全部通过（s6-8v）
- [x] routes.py 独立验证 69/69 检查点全部通过（s6-11）
- [x] NOT_FOUND 错误响应不回显 task_id（防枚举攻击，s6-11 修复）
- [x] 下载端点统一返回 JSONResponse + error()，不抛 HTTPException（s6-11 修复）
- [x] ADR-014 文档完整（本文件）

## 后续工作

1. **阶段 7 CAM 校验（ADR-015）**：消费阶段 6 导出的 G 代码文件 + 审核记录 JSON，
   调用 NX Open / PowerMill API / PyCAM 进行刀轨仿真 + 碰撞检测 + 机床运动学验证
2. **G 代码版本管理**：SUCCEEDED 任务永久保留，未来可加入版本对比功能
3. **控制器扩展**：现有 `app/postprocessor/` 已支持 10+ 控制器，阶段 6 自动继承
4. **五轴加工**：`xmachine_xm100` 控制器已支持五轴 G 代码生成
5. **工艺模板库**：未来可加入「钻孔模板」「铣平面模板」等快捷生成入口

## 参考

- ADR-005：核心架构契约设计
- ADR-008：参数化几何输出（OperationPlan 来源）
- ADR-009：切削参数推荐（ChatterParams 来源）
- ADR-013：颤振预测接入（ChatterReport 来源）
- 项目记忆硬约束：`cam_validation_required` 始终 True / SUCCEEDED 禁删 /
  HRC52 `pending_calibration` / `SAFETY_MARGIN_RATIO=0.8` / 系统不直接接口 CNC 控制器
- Tlusty 颤振稳定性理论：阶段 5 `predictor_adapter.py` 已实现
- 现有 G 代码基础设施：`app/postprocessor/` 包 + `app/process_planning/gcode_generator.py`
