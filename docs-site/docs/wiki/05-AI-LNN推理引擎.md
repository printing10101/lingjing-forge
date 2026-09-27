# 05. AI / LNN 推理引擎

> 这是本系统最核心的 AI 引擎，根目录：`python/app/ai/lnn/`，架构详见同目录的 `ARCHITECTURE.md`。

## 5.1 顶层概念

LNN（Liquid / Logic Neural Network）混合推理引擎，将 **LNN（CFC/LTC/Hybrid）+ LLM + 规则引擎** 通过智能任务路由 + Dempster-Shafer 证据理论融合，实现"快+准+可解释"。

### 5.1.1 引擎枚举
```python
class EngineType(str, Enum):
    LNN = "LNN"          # 液态神经网络
    LLM = "LLM"          # 大语言模型
    HYBRID = "Hybrid"    # 混合引擎
    RULE = "Rule"        # 规则引擎
```

### 5.1.2 模型类型
```python
class ModelType(str, Enum):
    CFC = "CFC"          # 闭式连续时间（<100ms）
    LTC = "LTC"          # 液态时间常数（时序 >1000 步）
    HYBRID_LNN = "HybridLNN"  # CNN + LNN
```

## 5.2 目录结构

```
python/app/ai/lnn/
├── __init__.py                # 暴露对外 API
├── core.py                    # 核心数据模型
├── engine.py                  # 混合推理整合器
├── preprocessing.py           # 数据预处理
├── postprocessing.py          # 结果后处理
├── fusion.py                  # Dempster-Shafer 融合
│
├── config/                    # 配置管理
├── inference/                 # 推理：predictor / registry / cache / batch
├── models/                    # 模型定义（PyTorch + 旧版）
├── training/                  # 训练：trainer / dataset / device / evaluator
├── quantization/              # 量化
├── router/                    # 任务路由
├── workflow/                  # 工作流编排
├── utils/                     # 内存优化
└── tests/                     # 单测 + 基准
```

## 5.3 核心数据模型 `core.py`

### 5.3.1 `TaskInput`
标准化任务输入，含：
- `task_description` — 任务描述
- `input_data` — 输入数据
- `task_category` — 任务类别（REGRESSION / CLASSIFICATION / ...）
- `requirements` — 性能/精度/可解释性要求

### 5.3.2 `RoutingDecision`
路由输出：
- `engine_type` — 选中的引擎
- `confidence` — 路由置信度
- `reasoning` — 选择依据

### 5.3.3 `InferenceResult`
单引擎输出：结论 + 置信度 + 推理路径 + 元数据。

### 5.3.4 `FusionResult`
多引擎融合输出：
- `conclusion`
- `supporting_evidence`
- `confidence`
- `reasoning_path`
- `explainability_report`

## 5.4 任务路由 `router/task_router.py`

```
权重：规则引擎 40% + ML 评分模型 60%
       │                │
       └──── 混合评分 ───┘
              │
              ▼
       RoutingDecision
       (engine + confidence + reasoning)
```

- **规则引擎**：基于特征匹配的确定性策略
- **ML 评分**：学习历史任务的最优引擎选择
- 决策被记录在 `explainability_report` 中，可追溯

## 5.5 模型定义 `models/`

### 5.5.1 PyTorch 实现（推荐）
| 文件 | 说明 |
|------|------|
| `torch_base_lnn.py` | LNN 基类 + `LNNConfig` |
| `torch_cfc_model.py` | CFC 闭式连续时间网络 |
| `torch_ltc_model.py` | LTC 液态时间常数网络 |
| `torch_hybrid_lnn.py` | CNN + LNN 混合 |

### 5.5.2 旧版（保留兼容）
| 文件 | 说明 |
|------|------|
| `base_lnn.py` / `cfc_model.py` / `ltc_model.py` / `hybrid_lnn.py` | 早期实现 |
| `parameter_models.py` | 参数化子模型 |

### 5.5.3 `torch_base_lnn.py:LNNConfig`
模型配置 dataclass：单元数、时间步、ODE 求解器、激活函数、混合架构开关等。

## 5.6 训练 `training/`

| 文件 | 职责 |
|------|------|
| `trainer.py` | `LNNTrainer` — 训练主循环 + 早停 + 检查点 |
| `dataset.py` | 通用 Dataset |
| `bosch_dataset.py` | Bosch CNC 数据加载器 |
| `dataset_cache.py` | 数据集缓存 |
| `device_manager.py` | GPU/CPU 检测、显存管理、`clear_gpu_memory()` |
| `evaluator.py` | 训练评估器 |
| `example_usage.py` | 使用样例 |

### 5.6.1 设备管理
```python
from app.ai.lnn.training.device_manager import (
    detect_device, get_available_devices, get_device_status,
    get_optimal_batch_size, get_optimal_num_workers, clear_gpu_memory,
)
```
自动选择 GPU（CUDA / MPS） / CPU，给出最优 batch_size 和 num_workers。

### 5.6.2 训练入口（API）
`POST /api/v1/lnn/train` —— 提交训练任务，返回 `task_id`，SSE 推流进度。

## 5.7 推理 `inference/`

| 文件 | 职责 |
|------|------|
| `predictor.py` | `LNNPredictor` / `PredictionResult` |
| `registry.py` | 模型注册表（含量化） |
| `model_cache.py` | 模型缓存（LRU + 显存感知） |
| `batch_inference.py` | 批量推理 |

### 5.7.1 推理入口（API）
`POST /api/v1/lnn/predict` —— 单次预测  
`POST /api/v1/lnn/batch-predict` —— 批量预测  
`GET  /api/v1/lnn/models` —— 已注册模型列表  
`POST /api/v1/lnn/quantize` —— 模型量化

### 5.7.2 模型注册服务
- `app/services/model_registry_service.py:get_model_registry_service()`  
  单例入口，避免在路由中直接实例化。

## 5.8 量化 `quantization/quantizer.py`

- 支持动态量化（Dynamic Quantization）
- 支持静态量化（Static Quantization with Calibration）
- 量化后模型在 `is_quantized_model()` 中标识
- 推理侧自动选择量化模型路径

## 5.9 融合 `fusion.py`

### 5.9.1 Dempster-Shafer 证据理论
- 每个引擎输出 → Mass 函数
- 多个 Mass 函数通过 Dempster 组合规则合成
- 冲突阈值（默认 0.8）：超过则降级为加权平均

### 5.9.2 输出
```python
@dataclass
class FusionResult:
    conclusion: Any
    supporting_evidence: List[Evidence]
    confidence: float
    reasoning_path: List[Step]
    explainability_report: Dict
```

## 5.10 工作流编排 `workflow/workflow_orchestrator.py`

将 LNN 训练 / 推理 / 量化 / 融合 / 持久化等步骤编排为可复用的 workflow（参考 `python/config/lnn_workflow.yaml`）。

## 5.11 LLM 引擎 `app/ai/`

| 文件 | 职责 |
|------|------|
| `llm_client.py` | 通用 LLM 客户端抽象 |
| `ollama_routes.py` | Ollama 本地 LLM 集成（FastAPI 路由） |
| `agents.py` | 智能体编排（与 `app/api/v1/agent_gateway.py` 配合） |

支持的本地模型示例：`qwen2.5-coder:7b`（见 README）。

## 5.12 智能体 `app/ai/agents.py`

- 多 Agent 协作（`AgentGateway`）
- 目标对齐（`app/goals/goal_alignment.py`）
- 状态机（`app/models/agent_state.py`）
- 网关 API：`/api/v1/agent_gateway`

## 5.13 工艺理解 `app/ai/process_understanding/`

> 工艺图理解模块（识别工艺图中的符号、尺寸、注释）
- 路由：`app/ai/process_understanding/routes.py`
- 主入口：`/api/v1/process_understanding/*`

## 5.14 训练与推理的关键工程点

| 关键点 | 说明 |
|--------|------|
| **模型缓存** | `model_cache` LRU 减少冷启动延迟 |
| **量化** | `quantizer` 减少模型体积与推理时延 |
| **熔断** | Dempster-Shafer 冲突 > 0.8 自动降级 |
| **可解释** | 完整推理路径 + 证据链 + 置信度 |
| **批量推理** | `batch_inference` 提升吞吐 |
| **GPU 自适应** | `device_manager` 动态选择 batch_size |
| **训练持久化** | `AsyncTaskManager` 持久化训练任务，崩溃可恢复 |
| **SSE 推流** | 训练/推理进度实时推送到前端 |
