# 设计：科研与工程模块解耦（三层 monorepo + 模型文件契约）

**日期**：2026-07-15
**状态**：提议
**决策者**：项目负责人（独立开发）
**前置 ADR**：[ADR-005 核心架构契约设计](../../adr/ADR-005-核心架构契约设计.md)、[ADR-013 颤振预测接入](../../adr/ADR-013-颤振预测接入.md)

---

## 1. 背景与动机

### 1.1 当前耦合点（6 处）

| # | 耦合点 | 性质 |
|---|--------|------|
| 1 | `python/app/ai/lnn/` 双重身份 | 既是工程侧颤振预测引擎（ADR-013 路径 B），又是科研侧论文实验的训练目标 |
| 2 | `python/experiments/` 16 个脚本 `from app.ai.lnn.training.*` | 科研侧直接依赖工程侧训练基础设施 |
| 3 | `app/ai/lnn/training/` 共享基础设施 | 工程侧在线训练与科研侧论文实验共用同一套 trainer/dataset/evaluator，需求方向相反（工程要稳定向后兼容，科研要灵活可重写） |
| 4 | `app/ai/lnn/models/` 模型类耦合 | 同一组 LTC/CFC 模型类既被工程推理加载，又是科研训练目标，模型结构变更两端都要改 |
| 5 | `docs/` 文档混杂 | 科研论文产出与工程文档混在同一个 docs 树 |
| 6 | `research/` 与 `python/experiments/` 边界不清 | 两个科研目录各自演化，无明确职责分工 |

### 1.2 解耦动机（用户确认）

1. **仓库治理清晰**：目录混乱影响维护
2. **释放科研探索自由度**：科研侧能自由重写模型/试新损失/引入新依赖，不受工程契约束缚
3. **让工程侧独立演进**：工程应用代码可按 ADR-005 契约自由重构，不被科研实验脚本牵制

### 1.3 物理分离程度（用户确认）

采用 **monorepo 物理分层**：保留单一 git 仓库，但仓库内重构为三个平级顶层包：`engineering/`、`research/`、`shared/`。

### 1.4 核心矛盾

工程侧 `app/ai/lnn/training/reproducibility.py` 等一旦为工程需求重构（改 API、改默认值），科研侧论文实验复现性立刻被破坏——这违反 D-2 学术诚信硬约束。

---

## 2. 目录终态

```
灵境制造（上线版）/
├── engineering/              # 工程应用
│   ├── python/                # 原 python/ 整体迁入（去除 ai/lnn/training 和 ai/lnn/models）
│   │   ├── app/
│   │   ├── tests/
│   │   ├── alembic/
│   │   ├── examples/
│   │   ├── requirements.txt   # 纯 Python + ONNX Runtime，无 torch
│   │   ├── pyproject.toml
│   │   └── start_server.py
│   ├── src-tauri/             # 原 src-tauri/
│   ├── src/                   # 原前端 src/
│   ├── splashscreen.html
│   ├── index.html
│   ├── package.json
│   ├── pnpm-lock.yaml
│   ├── env.d.ts
│   └── requirements-dev.txt
├── research/                  # 科研探索
│   ├── experiments/           # 原 python/experiments/ 全部 45+ 脚本
│   │   ├── exp7_main_comparison.py ~ exp45_cnn_lstm_baseline.py
│   │   ├── trainer.py / models.py / losses.py / metrics.py / config.py
│   │   ├── run_experiment.py / run_all_experiments.py
│   │   ├── data_generator.py
│   │   ├── optuna_search.py
│   │   └── results/           # 实验结果输出目录
│   ├── training/              # 原 python/app/ai/lnn/training/ 整体迁入
│   │   ├── reproducibility.py
│   │   ├── experiment_tracker.py
│   │   ├── trainer.py
│   │   ├── evaluator.py
│   │   ├── dataset.py / dataset_cache.py / bosch_dataset.py
│   │   ├── device_manager.py
│   │   └── tracking/
│   │       └── mlflow_tracker.py
│   ├── models/                # 原 python/app/ai/lnn/models/ 整体迁入（LTC/CFC 完整实现）
│   │   ├── base_lnn.py / torch_base_lnn.py
│   │   ├── ltc_model.py / torch_ltc_model.py
│   │   ├── cfc_model.py / torch_cfc_model.py
│   │   ├── hybrid_lnn.py / torch_hybrid_lnn.py
│   │   └── parameter_models.py
│   ├── prototypes/            # 原 research/ 顶层原型
│   │   ├── lnn_research/      # Bayesian LNN / cross_layer_fusion
│   │   ├── multimodal_jepa/   # IJepa 3D / jepa_world_model / vjepa_machining
│   │   ├── agents_research/
│   │   └── shared/            # 原 research/shared/
│   ├── papers/                # 论文相关文档与产出
│   │   ├── 大创赛/            # 原 docs/大创赛/
│   │   ├── report_assets/     # 原 docs/report_assets/
│   │   ├── 01-综合技术文档.md
│   │   ├── 04-机械方向内容报告.md
│   │   ├── thesis_content.json
│   │   └── 版本号验证报告_V2.5.0.md
│   ├── datasets/              # 原 python/data/ + research 专用数据
│   │   ├── uniwear/
│   │   └── (科研自采数据)
│   ├── checkpoints/           # 训练产物（ONNX + model_card）
│   ├── requirements.txt       # torch + optuna + scipy + scikit-learn + mlflow
│   ├── requirements-dev.txt
│   └── pyproject.toml
├── shared/                    # 薄契约层（零重依赖）
│   ├── lnn/
│   │   ├── __init__.py
│   │   ├── protocols.py       # ChatterPredictorProtocol / ModelLoaderProtocol
│   │   ├── artifact.py        # ModelArtifactSpec
│   │   ├── model_card.py      # ModelCard
│   │   └── types.py           # FeatureChatterResult / PredictionMethod / ChatterReviewStatus
│   ├── data/
│   │   ├── __init__.py
│   │   ├── contracts.py       # ChatterParams / ChatterReport / MaterialParams / CuttingParams
│   │   └── dataset.py         # DatasetSpec
│   ├── constants/
│   │   ├── __init__.py
│   │   ├── materials.py       # DEFAULT_CONFIDENCE / PENDING_CALIBRATION_MATERIALS / K_s 表
│   │   ├── precision.py       # PrecisionTier enum / SAFETY_MARGIN_RATIO
│   │   └── gates.py           # INDUSTRIAL_HARD_GATES / REQUIRES_ENGINEER_REVIEW
│   └── pyproject.toml         # 零依赖（只允许 stdlib + typing_extensions）
├── docs/                      # 仅工程文档
│   ├── adr/                   # ADR-001 ~ ADR-020（保持原样）
│   ├── api/                   # API 文档
│   ├── development/
│   ├── user-guide/
│   ├── runbook/
│   ├── integrations/
│   ├── pipelines/
│   ├── prompts/
│   ├── rag/
│   ├── reports/
│   ├── simulation/
│   ├── ai/
│   ├── 变更摘要/
│   ├── 国内部署指南.md
│   ├── api-reference.md
│   └── README.md
├── deploy/                    # 工程部署（保持原样）
├── scripts/                   # 顶层脚本（保持原样）
├── config/                    # 顶层配置（保持原样）
├── data/                      # 顶层运行时数据（保持原样）
├── mcp_server/                # MCP 服务器（保持原样）
├── rust/                      # Rust 计算扩展（保持原样）
├── .github/                   # CI/CD（保持原样）
├── .lingjing/                 # 技能/工具数据目录
├── README.md
├── LICENSE
├── SECURITY.md
├── CONTRIBUTING.md
├── CITATION.cff
├── VERSION
└── pyproject.toml             # 顶层工作区配置
```

---

## 3. `shared/` 契约清单（核心设计）

### 3.1 依赖约束

- `shared/` **只允许** `stdlib` + `typing_extensions`
- **严禁** torch / numpy / pydantic / scikit-learn 等重依赖
- 可被 `engineering/` 和 `research/` 双向 `pip install -e .`

### 3.2 契约模块

| 模块 | 内容 | 形式 |
|------|------|------|
| `shared/lnn/protocols.py` | `ChatterPredictorProtocol`（`predict_feature(...) → FeatureChatterResult`）、`ModelLoaderProtocol` | `typing.Protocol` 运行时检查 |
| `shared/lnn/artifact.py` | `ModelArtifactSpec`（onnx 路径 / 输入 schema / 输出 schema / 版本 / model_card 路径） | `@dataclass` |
| `shared/lnn/model_card.py` | `ModelCard`（git SHA / 数据 hash / 训练超参 / 评估指标 / 训练时间 / 训练设备） | `@dataclass` |
| `shared/lnn/types.py` | `FeatureChatterResult` / `PredictionMethod` enum / `ChatterReviewStatus` enum / `ChatterPredictionTaskStatus` enum | `@dataclass` + `Enum` |
| `shared/data/contracts.py` | `ChatterParams` / `ChatterReport` / `MaterialParams` / `CuttingParams` / `ToolParams` / `MachineParams` | `@dataclass` |
| `shared/data/dataset.py` | `DatasetSpec`（schema / version / hash / 路径 / 创建时间 / 描述） | `@dataclass` |
| `shared/constants/materials.py` | `DEFAULT_CONFIDENCE=0.8` / `PENDING_CALIBRATION_CONFIDENCE=0.5` / `FALLBACK_CONFIDENCE=0.3` / `PENDING_CALIBRATION_MATERIALS: frozenset` / K_s 表（6061-T6 ≈ 800 / TC4 ≈ 1600 / HRC52 ≈ 2800 N/mm²） | 常量 |
| `shared/constants/precision.py` | `PrecisionTier` enum（coarse/standard/high）/ `SAFETY_MARGIN_RATIO=0.8` | 常量 |
| `shared/constants/gates.py` | `INDUSTRIAL_HARD_GATES` 8 条 / `REQUIRES_ENGINEER_REVIEW=True` / `REQUIRES_CAM_VALIDATION=True` | 常量 |

### 3.3 关键约束

- `shared/` 是 **唯一**的科研侧与工程侧共享代码入口
- 工程侧 `from shared.lnn.protocols import ChatterPredictorProtocol` 做类型注解
- 科研侧 `from shared.lnn.types import FeatureChatterResult` 做训练目标对齐
- 数据契约两端共享，避免 schema 漂移
- 契约变更需走 ADR 评审（与 ADR-005 一致）

---

## 4. 模型文件即契约（核心解耦机制）

### 4.1 训练产物结构

科研侧训练完成后必须导出：

```
research/checkpoints/chatter_model_<timestamp>/
├── model.onnx                # ONNX 推理图
├── model_card.json           # git SHA + 数据 hash + 超参 + 评估指标
├── preprocessor.pkl          # sklearn Pipeline（transform only，禁 fit_transform）
├── input_schema.json         # 输入字段名/类型/范围
└── output_schema.json        # 输出字段名/类型/范围
```

### 4.2 工程侧 ONNX 加载器

`engineering/python/app/ai/lnn/inference/onnx_predictor.py`：

- 通过 `shared.lnn.ModelArtifactSpec` 加载
- 用 `onnxruntime.InferenceSession` 推理
- **推理路径完全不依赖 torch**（torch 仅在科研侧训练时使用）
- **preprocessor 加载保留 sklearn 依赖**：preprocessor.pkl 用 sklearn Pipeline 序列化，工程侧需 `scikit-learn` 才能 `pickle.loads` 反序列化（仅调 `transform`，禁 `fit_transform`）。长期可换 `skops` 安全格式消除 sklearn 依赖
- 提供 `predict_feature(feature_id, feature_type, material_id, chatter_params_dict, source_cutting_params_task_id="")` 签名，符合 `ChatterPredictorProtocol`

### 4.3 收益

- 工程侧 `requirements.txt` 从含 torch（≈2GB）→ 仅 onnxruntime（≈50MB）
- Tauri 包体大幅缩小（约 1.5GB 减重）
- sidecar 启动时间显著缩短（无 torch 初始化开销）
- CI 流水线不再跑 torch 测试，反馈更快

---

## 5. 工程侧重构要点

### 5.1 LNN 模块处置

| 模块 | 处置 | 备注 |
|------|------|------|
| `app/ai/lnn/inference/` | **保留并改造** | 改为加载 ONNX（删 torch 模型加载代码） |
| `app/ai/lnn/models/` | **删除** | 迁到 `research/models/` |
| `app/ai/lnn/training/` | **删除** | 迁到 `research/training/` |
| `app/ai/lnn/tracking/mlflow_tracker.py` | **删除** | 仅科研侧需要 |
| `app/ai/lnn/quantization/` | **删除** | 仅科研侧需要 |
| `app/ai/lnn/router/task_router.py` | **保留** | 工程侧任务路由 |
| `app/ai/lnn/config/` | **保留** | 工程侧 LNN 配置 |
| `app/ai/lnn/core.py` | **保留工程必要部分** | 去除训练相关代码 |
| `app/ai/lnn/engine.py` | **保留工程必要部分** | 去除训练相关代码 |
| `app/ai/lnn/fusion.py` | **保留** | 推理时融合 |
| `app/ai/lnn/preprocessing.py` | **保留** | 推理时预处理 |
| `app/ai/lnn/postprocessing.py` | **保留** | 推理时后处理 |
| `app/ai/lnn/visualization.py` | **保留** | 可解释性可视化 |

### 5.2 颤振预测改造

`app/chatter_prediction/predictor_adapter.py`：

- 路径 A（Tlusty 解析法）：**保持原样**
- 路径 B（LTC 神经网络）：改为 ONNX 加载，仍保留 `check_ltc_model_available()` 探测（改为探测 `model.onnx`）
- 路径 C（兜底默认值）：**保持原样**
- 行为不变：找不到模型仍回退解析法

### 5.3 API 路由处置

| 端点 | 处置 |
|------|------|
| `/api/v1/lnn/` 训练相关端点 | **删除** 或改为触发科研侧异步任务（推荐删除，工程侧不暴露训练） |
| `/api/v1/lnn/` 推理相关端点 | **保留**，改用 ONNX 加载 |
| `/api/v1/chatter_prediction/` | **保留**，路径 B 改 ONNX |
| `/api/v1/world_model/` | **保留**，import 路径改 `from shared.lnn...` |

### 5.4 依赖瘦身

`engineering/python/requirements.txt` 移除：

- `torch`
- `torchvision`
- `torchaudio`
- `optuna`
- `mlflow`
- `tensorboard`

**保留**：

- `scikit-learn`：仅用于 `pickle.loads` 反序列化 preprocessor.pkl + 调 `transform`（推理路径禁 `fit_transform`）。长期可换 `skops` 安全格式彻底消除该依赖
- `numpy`：ONNX Runtime 推理输入输出仍需 numpy 数组

新增：

- `onnxruntime`（≈50MB）

### 5.5 测试改造

- `tests/test_lnn*.py` 推理相关：保留，改 mock ONNX session
- `tests/test_lnn*training*.py`：迁移到 `research/`
- `tests/test_chatter_prediction.py`：保留，路径 B mock ONNX

---

## 6. 科研侧重构要点

### 6.1 模块迁移

| 原位置 | 新位置 | 备注 |
|--------|--------|------|
| `python/experiments/` | `research/experiments/` | 整体迁移，45+ 脚本 |
| `python/app/ai/lnn/training/` | `research/training/` | 整体迁移 |
| `python/app/ai/lnn/models/` | `research/models/` | 整体迁移 |
| `python/app/ai/lnn/tracking/` | `research/training/tracking/` | 合并到 training 下 |
| `python/app/ai/lnn/quantization/` | `research/quantization/` | 整体迁移 |
| `python/app/ai/lnn/tests/` | `research/tests/` | 迁移 torch 相关测试 |
| `research/` 顶层原型 | `research/prototypes/` | 整理 |
| `python/data/uniwear/` | `research/datasets/uniwear/` | 数据迁移 |
| `docs/大创赛/` | `research/papers/大创赛/` | 论文文档迁移 |
| `docs/report_assets/` | `research/papers/report_assets/` | 论文资产迁移 |
| `docs/01-综合技术文档.md` | `research/papers/01-综合技术文档.md` | 综合文档迁移 |
| `docs/04-机械方向内容报告.md` | `research/papers/04-机械方向内容报告.md` | 机械报告迁移 |
| `docs/thesis_content.json` | `research/papers/thesis_content.json` | 论文内容迁移 |

### 6.2 import 路径改造

科研侧脚本（16 个文件）：

```python
# 原
from app.ai.lnn.training.reproducibility import set_global_seed
from app.ai.lnn.training.experiment_tracker import start_run, log_params
from app.ai.lnn.models import create_model

# 新
from research.training.reproducibility import set_global_seed
from research.training.experiment_tracker import start_run, log_params
from research.models import create_model
```

共享契约引用：

```python
from shared.lnn.types import FeatureChatterResult
from shared.data.contracts import ChatterParams
from shared.constants.materials import DEFAULT_CONFIDENCE
```

### 6.3 训练产物导出契约

科研侧训练完成后必须：

1. 导出 ONNX 到 `research/checkpoints/chatter_model_<timestamp>/model.onnx`
2. 生成 `model_card.json`（git SHA / 数据 hash / 超参 / 评估指标）
3. 序列化 preprocessor 到 `preprocessor.pkl`
4. 生成 input/output schema JSON
5. 工程侧通过 `ModelArtifactSpec` 加载

### 6.4 论文复现性保护

- 实验跑完后在 `main` 分支打 tag `paper-v1.0-frozen`
- `research/` 目录可独立 git tag 冻结
- MLflow tracking 仍记录所有实验元数据
- `research/training/reproducibility.py` 保持原样（已覆盖 random/np/torch/cudnn/DataLoader）

---

## 7. 迁移顺序（关键）

### 7.1 阶段 1（立即执行，零干扰实验）

**前提**：Python 进程已加载代码到内存，磁盘文件移动/重命名不影响运行中进程。

1. 建 `shared/` 目录骨架
2. 写契约代码：
   - `shared/lnn/protocols.py`
   - `shared/lnn/artifact.py`
   - `shared/lnn/model_card.py`
   - `shared/lnn/types.py`
   - `shared/data/contracts.py`
   - `shared/data/dataset.py`
   - `shared/constants/materials.py`
   - `shared/constants/precision.py`
   - `shared/constants/gates.py`
3. 写 `shared/pyproject.toml`（零依赖）
4. 写 `shared/README.md`
5. `pip install -e shared/` 在工程虚拟环境装上
6. 写本 spec 文档（已完成）

**产出**：`shared/` 包可被工程侧和科研侧双向引用，但现有代码尚未实际引用。

### 7.2 阶段 2（实验跑完后，用户通知）

**前置条件**：

- 用户确认实验已完成
- 在 `main` 分支打 tag `paper-v1.0-frozen` 作为回滚点
- 创建新分支 `refactor/decouple-research-engineering`

**步骤**：

1. **创建新目录骨架**：
   - `mkdir engineering/ research/`
   - 在 `engineering/` 下创建 `python/` 子目录

2. **批量迁移工程代码**：
   - `mv python/app/ engineering/python/app/`
   - `mv python/tests/ engineering/python/tests/`
   - `mv python/alembic/ engineering/python/alembic/`
   - `mv python/examples/ engineering/python/examples/`
   - `mv python/scripts/ engineering/python/scripts/`
   - `mv python/start_server.py engineering/python/`
   - `mv python/sidecar_main.py engineering/python/`
   - `mv python/conftest.py engineering/python/`
   - `mv python/alembic.ini engineering/python/`
   - `mv python/requirements*.txt engineering/python/`
   - `mv python/run_pytest.py engineering/python/`
   - `mv src-tauri/ engineering/`
   - `mv src/ engineering/`
   - `mv splashscreen.html engineering/`
   - `mv index.html engineering/`
   - `mv package.json engineering/`
   - `mv pnpm-lock.yaml engineering/`
   - `mv env.d.ts engineering/`

3. **批量迁移科研代码**：
   - `mv python/experiments/ research/experiments/`
   - `mv python/app/ai/lnn/training/ research/training/`
   - `mv python/app/ai/lnn/models/ research/models/`
   - `mv python/app/ai/lnn/tracking/ research/training/tracking/`
   - `mv python/app/ai/lnn/quantization/ research/quantization/`
   - `mv python/app/ai/lnn/tests/ research/tests/`
   - `mv python/data/uniwear/ research/datasets/uniwear/`（仅迁移 uniwear 科研数据，python/data/ 下其他运行时数据保留）
   - 整理 `research/` 顶层原型到 `research/prototypes/`：
     - `mv research/lnn_research/ research/prototypes/lnn_research/`
     - `mv research/multimodal_jepa/ research/prototypes/multimodal_jepa/`
     - `mv research/agents_research/ research/prototypes/agents_research/`
     - `mv research/shared/ research/prototypes/shared/`

4. **批量迁移论文文档**：
   - `mv docs/大创赛/ research/papers/大创赛/`
   - `mv docs/report_assets/ research/papers/report_assets/`
   - `mv docs/01-综合技术文档.md research/papers/`
   - `mv docs/04-机械方向内容报告.md research/papers/`
   - `mv docs/thesis_content.json research/papers/`
   - `mv docs/版本号验证报告_V2.5.0.md research/papers/`

5. **改 import 路径**（一次性 sed 脚本）：
   - 全量扫描 `from app.ai.lnn` 出现位置
   - 工程侧（`engineering/`）改为 `from app.ai.lnn.inference` + `from shared.lnn`
   - 科研侧（`research/`）改为 `from research.training` + `from research.models` + `from shared.lnn`

6. **改造工程侧 LNN 模块**：
   - 删除 `engineering/python/app/ai/lnn/training/`（已迁走，确认无残留引用）
   - 删除 `engineering/python/app/ai/lnn/models/`（已迁走）
   - 删除 `engineering/python/app/ai/lnn/tracking/`（已迁走）
   - 删除 `engineering/python/app/ai/lnn/quantization/`（已迁走）
   - 改造 `engineering/python/app/ai/lnn/inference/predictor.py` 为 ONNX 加载
   - 改造 `engineering/python/app/chatter_prediction/predictor_adapter.py` 路径 B
   - 改造 `engineering/python/app/plugins/world_model/plugin.py` import 路径

7. **依赖瘦身**：
   - 改 `engineering/python/requirements.txt` 删 torch + sklearn + optuna + mlflow
   - 新增 `onnxruntime`
   - 新建 `research/requirements.txt` 含 torch + optuna + scipy + scikit-learn + mlflow

8. **配置文件更新**：
   - `pyproject.toml`（顶层）改为 workspace 配置
   - `pytest.ini` 调整 testpaths
   - `.coveragerc` 调整 source 路径
   - `.github/workflows/ci.yml` 拆分工程/科研 CI job

9. **验证**：
   - 工程侧全量测试通过
   - 科研侧 exp7 smoke test 通过
   - sidecar 启动验证
   - 前端调用验证

---

## 8. 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| 工程侧 LNN 在线训练 API 被删后，前端有调用 | 阶段 2 前检查 `src/views/` 调用，前端对应禁用或改为触发科研侧异步任务 |
| `research/training/experiment_tracker.py` 依赖 MLflow | 保留 MLflow 依赖在 `research/requirements.txt` |
| `app/plugins/world_model/plugin.py` 是工程+科研混合 | 拆分：工程侧只保留推理调用，训练相关迁科研 |
| ADR-013 颤振预测路径 B 当前无模型 | 阶段 2 后路径 B 改为 ONNX 加载，行为不变（找不到模型仍回退解析法） |
| 62 处 import 改写遗漏 | 用 `grep -r "from app.ai.lnn" python/` 全量扫描后逐个改，加 CI 检查 |
| 科研论文 git tag 冻结点 | 实验跑完后在 `main` 上打 tag `paper-v1.0-frozen` 再开始迁移 |
| `shared/` 契约设计若有偏差需修改 | 契约变更需走 ADR 评审，与 ADR-005 一致 |
| 工程侧 `sklearn` 移除后 preprocessor.pkl 无法反序列化 | 保留 `scikit-learn` 在工程侧（仅 `pickle.loads` + `transform`），或换 `skops` 安全格式 |
| 双重 `pyproject.toml` 导致 IDE 跳转混乱 | 在顶层 `pyproject.toml` 配置 workspace members |

---

## 9. 不在本次范围

- ADR-005 五大契约完整实现（任务/数据/插件/配置/可观测）——本次仅做 `shared/` 雏形
- 完整插件化（方案 C）——可在方案 A 落地后渐进推进
- 双 git 仓库拆分——不考虑，独立开发者双仓同步成本过高
- 工程侧 5 大契约的全面重构——按 ADR-005 阶段计划独立推进
- 世界模型 / RL 模块的完整接入——按 ADR-017 / ADR-020 独立推进

---

## 10. 验收标准

### 阶段 1 验收

- [ ] `shared/` 目录骨架建立
- [ ] 9 个契约模块代码完成（protocols / artifact / model_card / types / contracts / dataset / materials / precision / gates）
- [ ] `shared/pyproject.toml` 零依赖声明
- [ ] `pip install -e shared/` 在工程虚拟环境成功
- [ ] `from shared.lnn.protocols import ChatterPredictorProtocol` 可导入
- [ ] `from shared.constants.materials import DEFAULT_CONFIDENCE` 可导入
- [ ] spec 文档落盘并经用户 review

### 阶段 2 验收

- [ ] `engineering/` 与 `research/` 物理分层完成
- [ ] 62 处 `from app.ai.lnn` import 全部改写
- [ ] 工程侧 `requirements.txt` 无 torch 依赖
- [ ] 工程侧 sidecar 启动验证通过
- [ ] 工程侧全量测试通过（含 chatter_prediction / lnn inference / world_model）
- [ ] 科研侧 exp7 smoke test 通过
- [ ] 前端调用验证（LNN 推理 / 颤振预测 / 世界模型）
- [ ] CI 流水线拆分（工程 job 不跑 torch 测试）
- [ ] `docs/` 仅含工程文档
- [ ] `research/papers/` 含全部论文产出

---

## 11. 后续工作

- 工程侧按 ADR-005 推进五大契约实现
- 科研侧按 D-1/D-2 推进论文实验
- `shared/` 契约按需扩展（如未来世界模型 / RL 接入）
- 工程侧 ONNX 推理性能基准测试
- 工程侧 Tauri 包体瘦身验证

---

## 变更记录

| 日期 | 变更内容 | 变更人 |
|------|----------|--------|
| 2026-07-15 | 初始版本，提议三层 monorepo + 模型文件契约解耦 | 项目负责人 |
