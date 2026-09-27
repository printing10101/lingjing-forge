# 混合推理引擎系统 - 架构设计文档

> **实现状态说明（2026-07-13 修订）**
>
> 本文档描述的是目标架构（target design）。当前代码库中的实现状态如下：
>
> | 组件 | 文件 | 状态 |
> |------|------|------|
> | 核心数据模型（TaskInput/RoutingDecision/InferenceResult/FusionResult） | `core.py` | 完整实现 |
> | LNN 模型（CFC/LTC/Hybrid） | `models/` | 完整实现 |
> | 训练与推理 | `training/`、`inference/` | 完整实现 |
> | `TaskRouter` | `router/task_router.py` | 完整实现（混合规则 + 在线 ML 评分 + 贝叶斯收缩） |
> | `DempsterShaferFusion` | `fusion.py` | 完整实现（Dempster 组合规则 + 冲突阈值回退 + 70/30 DS 加权混合） |
> | `HybridInferenceEngine` | `engine.py` | 完整实现（真实多模型编排 + DS 融合 + 在线 outcome 反馈 + 流式扩展） |
> | 流式长时序扩展（借鉴 lingbot-map GCT） | `inference/streaming.py` | 完整实现（分页隐状态 + 关键帧策略 + 锚点上下文 + 轨迹记忆 + 窗口化推理） |
>
> 所有组件的公共 API 与本文档契约一致，可在下游代码中安全导入；决策结果不再带
> `"stub": True` 标记（`HybridInferenceEngine.get_engine_stats()` 显式输出
> `stub_implementation: False`）。`engine.py` 还通过
> `register_streaming_predictor` / `infer_stream` / `infer_windowed` 提供长时序
> 加工流推理能力。

## 1. 系统概述

混合推理引擎系统是一个高可用、可扩展、可维护的AI推理框架，整合了LNN（液态神经网络）、LLM（大语言模型）、Rule（规则引擎）和Hybrid（混合模式）四种推理引擎，通过智能任务路由和Dempster-Shafer证据理论结果融合，实现最优推理效果。

### 1.1 设计目标

- **高可用性**：完善的错误处理、重试和降级策略
- **可扩展性**：模型注册接口，支持新引擎无缝集成
- **可维护性**：模块化设计，低耦合高内聚，遵循PEP 8规范
- **高性能**：异步批量处理，模型缓存，推理时间 < 100ms (CFC)

### 1.2 核心特性

| 特性 | 描述 |
|------|------|
| 智能路由 | 基于规则与ML混合决策，自动选择最优引擎 |
| 多引擎支持 | LNN(CFC/LTC/Hybrid)、LLM、Rule并行/串行执行 |
| 结果融合 | Dempster-Shafer证据理论多引擎结果融合 |
| 多模态处理 | 数值、类别、文本、图像多模态输入支持 |
| 可解释性 | 完整推理路径、证据链、置信度评估 |

---

## 2. 系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户输入 (User Input)                      │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   任务解析与标准化 (TaskParser)                    │
│                    TaskInput标准化封装                             │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    任务路由器 (TaskRouter)                        │
│     ┌─────────────┐         ┌─────────────┐                      │
│     │  规则引擎    │  混合   │  ML评分模型  │                      │
│     │  (40%)      │ ──────▶ │  (60%)      │                      │
│     └─────────────┘         └─────────────┘                      │
│                      ▼                                            │
│              RoutingDecision                                     │
│          (引擎选择 + 置信度 + 依据)                                │
└──────────────────────────┬──────────────────────────────────────┘
                           │
               ┌───────────┼───────────┐
               ▼           ▼           ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  LNN Engine  │ │  LLM Engine  │ │ Rule Engine  │
│ ┌──────────┐ │ │              │ │              │
│ │   CFC    │ │ │  LLM-GPT    │ │ RuleEngine   │
│ │  <100ms  │ │ │   (模拟)    │ │   -v1        │
│ ├──────────┤ │ │              │ │              │
│ │   LTC    │ │ │              │ │              │
│ │ 时序>1000│ │ │              │ │              │
│ ├──────────┤ │ │              │ │              │
│ │  Hybrid  │ │ │              │ │              │
│ │ CNN+LNN  │ │ │              │ │              │
│ └──────────┘ │ │              │ │              │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       └────────────────┼────────────────┘
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│                结果融合层 (Dempster-Shafer Fusion)                │
│     ┌──────────────┐     ┌──────────────┐                       │
│     │ Mass函数构建  │ ──▶ │ Dempster组合  │                       │
│     └──────────────┘     └──────┬───────┘                       │
│                                 ▼                                │
│                        冲突检测与解决                             │
│               (冲突阈值0.8 → 加权平均替代)                         │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    最终输出 (FusionResult)                        │
│   结论 | 支持证据 | 置信度 | 推理路径 | 可解释性报告              │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 目录结构

```
python/app/ai/lnn/
├── __init__.py                    # 模块初始化，导出核心类
├── core.py                        # 核心数据模型和类型定义
├── engine.py                      # 主混合推理引擎（整合器）
├── preprocessing.py               # 数据预处理模块
├── postprocessing.py              # 结果后处理模块
├── fusion.py                      # Dempster-Shafer结果融合层
├── models/                        # LNN模型定义
│   ├── __init__.py
│   ├── base_lnn.py                # LNN基类
│   ├── cfc_model.py               # CFC快速推理模型
│   ├── ltc_model.py               # LTC时序预测模型
│   └── hybrid_lnn.py              # CNN+LNN混合模型
├── training/                      # 训练模块
│   ├── __init__.py
│   ├── dataset.py                 # 数据集处理
│   ├── trainer.py                 # 训练器
│   └── evaluator.py               # 评估器
├── inference/                     # 推理模块
│   ├── __init__.py
│   ├── predictor.py               # 单样本预测器
│   ├── batch_inference.py         # 批量推理
│   └── registry.py                # 模型注册表
├── router/                        # 任务路由器
│   ├── __init__.py
│   └── task_router.py             # 路由决策算法
└── tests/                         # 单元测试
    ├── __init__.py
    └── test_lnn_system.py         # 完整测试套件
```

---

## 3. 模块接口定义

### 3.1 核心数据模型 (core.py)

#### 3.1.1 枚举类型

```python
class EngineType(str, Enum):
    """推理引擎类型"""
    LNN = "LNN"
    LLM = "LLM"
    HYBRID = "Hybrid"
    RULE = "Rule"

class ModelType(str, Enum):
    """LNN模型类型"""
    CFC = "CFC"
    LTC = "LTC"
    HYBRID_LNN = "HybridLNN"

class DataType(str, Enum):
    """数据类型"""
    STRUCTURED = "structured"
    UNSTRUCTURED = "unstructured"
    SEMI_STRUCTURED = "semi_structured"
    MULTIMODAL = "multimodal"

class TaskCategory(str, Enum):
    """任务类别"""
    CLASSIFICATION = "classification"
    REGRESSION = "regression"
    TIME_SERIES = "time_series"
    NLP = "nlp"
    VISION = "vision"
    LOGIC_REASONING = "logic_reasoning"
    RULE_BASED = "rule_based"
```

#### 3.1.2 数据类

```python
@dataclass
class TaskInput:
    """标准化任务输入"""
    task_description: str
    input_data: Any
    context: Optional[Dict[str, Any]] = None
    task_category: Optional[TaskCategory] = None
    data_type: Optional[DataType] = None
    precision_requirement: float = 0.9
    time_sensitivity: float = 0.5
    max_latency_ms: int = 1000
    metadata: Optional[Dict[str, Any]] = None

@dataclass
class RoutingDecision:
    """路由决策结果"""
    selected_engine: EngineType
    selected_model: Optional[str] = None
    confidence: float = 0.0
    reasoning: str = ""
    decision_factors: Optional[Dict[str, float]] = None
    alternatives: Optional[List[Dict[str, Any]]] = None
    timestamp: Optional[float] = None

@dataclass
class InferenceResult:
    """推理结果"""
    prediction: Any
    confidence: float = 0.0
    engine_used: Optional[EngineType] = None
    model_used: Optional[str] = None
    processing_time_ms: float = 0.0
    metadata: Optional[Dict[str, Any]] = None
    evidence: Optional[List[Dict[str, Any]]] = None
    uncertainty: Optional[Dict[str, float]] = None

@dataclass
class FusionResult:
    """融合后的最终结果"""
    final_prediction: Any
    confidence: float = 0.0
    contributing_engines: List[Dict[str, Any]]
    fusion_method: str = "dempster_shafer"
    reasoning_path: Optional[List[str]] = None
    explainability_report: Optional[str] = None
    quality_metrics: Optional[Dict[str, float]] = None
```

### 3.2 LNN基类接口 (models/base_lnn.py)

```python
class BaseLNNModel(ABC):
    """LNN模型基类，定义统一接口与基础功能"""

    def __init__(self, model_name, input_dim, output_dim, device="cpu", **kwargs)

    @abstractmethod
    def build(self) -> None:
        """构建模型结构"""

    @abstractmethod
    def forward(self, x: np.ndarray) -> np.ndarray:
        """前向传播"""

    @abstractmethod
    def predict(self, x: np.ndarray) -> np.ndarray:
        """预测接口"""

    def predict_with_confidence(self, x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]
    def calculate_uncertainty(self, predictions: np.ndarray) -> Dict[str, float]
    def train(self, train_data, train_labels, val_data=None, val_labels=None, epochs=100, batch_size=32, lr=0.001)
    def evaluate(self, test_data, test_labels, metrics=None) -> Dict[str, float]
    def save(self, path: str) -> None
    def load(self, path: str) -> None
    def get_model_info(self) -> Dict[str, Any]
    def measure_inference_time(self, x: np.ndarray, n_runs=100) -> Dict[str, float]
```

### 3.3 任务路由器接口 (router/task_router.py)

```python
class TaskRouter:
    """任务路由器 - 混合规则与ML决策"""

    def __init__(self, rule_weight=0.4, ml_weight=0.6, confidence_threshold=0.7, enable_fallback=True)

    def route(self, task: TaskInput) -> RoutingDecision:
        """路由决策主入口"""

    def get_decision_stats(self) -> Dict[str, Any]:
        """获取决策统计"""

    def reset_history(self) -> None:
        """重置决策历史"""
```

#### 3.3.1 决策算法说明

1. **特征提取**: 从任务描述中提取9维特征向量
   - 复杂度评分、计算密集度、逻辑推理深度
   - 时间敏感性、数据结构化比例、精度要求
   - 输入规模、时序成分、多模态输入、可解释性需求

2. **规则评分**: 基于关键词匹配和阈值规则
   - Rule引擎：条件规则关键词、低延迟要求、高逻辑深度
   - LLM引擎：自然语言处理关键词、高复杂度、可解释性需求
   - LNN引擎：时序关键词、快速推理要求、结构化数据
   - Hybrid引擎：多模态关键词、高精度要求

3. **ML评分**: 基于加权评分模型
   - 9个特征 × 4个引擎的权重矩阵
   - 特征重要性加权聚合

4. **混合决策**: `final_score = 0.4 × rule_score + 0.6 × ml_score`

5. **降级策略**: 置信度低于阈值时选择备选引擎

### 3.4 结果融合层接口 (fusion.py)

```python
class DempsterShaferFusion:
    """Dempster-Shafer证据理论融合器"""

    def __init__(self, conflict_threshold=0.8, min_confidence=0.3, enable_conflict_resolution=True)

    def fuse(self, results: List[InferenceResult], weights=None) -> FusionResult:
        """融合多引擎结果"""

    def get_fusion_stats(self) -> Dict[str, Any]:
        """获取融合统计"""
```

#### 3.4.1 Dempster-Shafer算法说明

1. **Mass函数构建**: `m(hypothesis_A) = confidence × weight`, `m(uncertainty) = (1-confidence) × weight`

2. **Dempster组合规则**:
   ```
   m_combined(A) = Σ[m1(B) × m2(C)] / (1 - K)  where B ∩ C = A
   K = Σ[m1(B) × m2(C)]  where B ∩ C = ∅ (冲突系数)
   ```

3. **冲突处理**: 当K > 0.8时，使用加权平均替代Dempster组合

4. **动态权重**: `weight = 0.4×confidence + 0.3×efficiency + 0.3×historical_score`

### 3.5 混合推理引擎接口 (engine.py)

```python
class HybridInferenceEngine:
    """混合推理引擎 - 主 orchestrator"""

    def __init__(self, rule_weight=0.4, ml_weight=0.6, enable_fusion=True,
                 enable_parallel_execution=False, cache_size=10, device="cpu")

    def initialize_models(self) -> None:
        """初始化所有LNN模型并注册"""

    def infer(self, task_description, input_data, context=None,
              precision_requirement=0.9, time_sensitivity=0.5,
              max_latency_ms=1000) -> Union[FusionResult, InferenceResult]:
        """主推理接口"""

    def infer_batch(self, tasks, batch_size=32) -> List[Union[FusionResult, InferenceResult]]:
        """批量推理"""

    def register_custom_model(self, model_name, model_instance, model_type) -> None:
        """注册自定义模型"""

    def get_engine_stats(self) -> Dict[str, Any]:
        """获取引擎统计"""
```

---

## 4. 数据流图 (DFD)

### 4.1 Level 0 - 上下文图

```
                 ┌──────────┐
                 │  User    │
                 └────┬─────┘
                      │ 请求
                      ▼
              ┌───────────────┐
              │  Hybrid       │
              │  Inference    │
              │  Engine       │
              └───────┬───────┘
                      │ 结果
                      ▼
                 ┌──────────┐
                 │  User    │
                 └──────────┘
```

### 4.2 Level 1 - 系统数据流

```
User Input
    │
    ├──(1)──▶ [TaskParser] ──▶ TaskInput
    │                              │
    │                              ├──(2)──▶ [TaskRouter] ──▶ RoutingDecision
    │                                                               │
    │                              ┌────────────────────────────────┤
    │                              ▼                                ▼
    │                         (3) Engine Selection                 (3)
    │                              │                                │
    │                    ┌─────────┴─────────┐                     │
    │                    ▼                   ▼                     ▼
    │              [LNN Engine]        [LLM Engine]          [Rule Engine]
    │                    │                   │                     │
    │                    └─────────┬─────────┘                     │
    │                              ▼                               │
    │                    [Intermediate Results] ◀─────────────────┘
    │                              │
    │                              ├──(4)──▶ [Fusion Layer] ──▶ FusionResult
    │                                                                 │
    │                                                                 ├──(5)──▶ [Postprocessor]
    │                                                                         │
    │                                                                         ▼
    │                                                                    Final Output
    └────────────────────────────────────────────────────────────────────────────▶ User
```

### 4.3 Level 2 - LNN引擎内部数据流

```
Input Data
    │
    ├──(1)──▶ [Preprocessor] ──▶ Normalized Features
    │                                  │
    │                                  ├──(2)──▶ [CFC Model] ──▶ Prediction
    │                                  ├──(2)──▶ [LTC Model] ──▶ Prediction
    │                                  └──(2)──▶ [Hybrid Model]─▶ Prediction
    │                                                                   │
    │                                                                   ├──(3)──▶ [Postprocessor]
    │                                                                           │
    │                                                                           ▼
    │                                                                      InferenceResult
    └────────────────────────────────────────────────────────────────────────────▶ Fusion
```

---

## 5. 类关系图

### 5.1 模型继承关系

```
                    ┌──────────────────┐
                    │   BaseLNNModel   │
                    │   (Abstract)     │
                    └────────┬─────────┘
                             │
            ┌────────────────┼────────────────┐
            │                │                │
            ▼                ▼                ▼
    ┌───────────────┐ ┌───────────────┐ ┌───────────────┐
    │   CFCModel    │ │   LTCModel    │ │HybridLNNModel │
    │ (快速推理)     │ │ (时序预测)    │ │ (多模态)      │
    └───────────────┘ └───────────────┘ └───────────────┘
```

### 5.2 核心组件关系

```
┌────────────────────────────────────────────────────────────────┐
│                   HybridInferenceEngine                         │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  TaskRouter  │  │    Fusion    │  │   ModelRegistry      │  │
│  │              │  │              │  │                      │  │
│  │ - Scoring    │  │ - D-S Theory │  │ - Cache Management   │  │
│  │   Model      │  │ - Conflict   │  │ - Version Control    │  │
│  │ - Rule Based │  │   Resolution │  │ - Model Loading      │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
│         │                 │                      │              │
│         │                 │         ┌────────────┴────────────┐ │
│         │                 │         │    LNN Models           │ │
│         │                 │    ┌────┴────┐ ┌─────┐ ┌────────┐ │ │
│         │                 │    │   CFC   │ │ LTC │ │Hybrid  │ │ │
│         │                 │    └─────────┘ └─────┘ └────────┘ │ │
│         ▼                 ▼                                   │
│    RoutingDecision  FusionResult                              │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│                    训练与推理                                    │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ LNNDataset   │  │  LNNTrainer  │  │    LNNEvaluator      │  │
│  │              │  │              │  │                      │  │
│  │ - Data Load  │  │ - Training   │  │ - Multi-metric Eval  │  │
│  │ - Split      │  │ - Checkpoint │  │ - Performance Test   │  │
│  │ - Transform  │  │ - Early Stop │  │ - Report Generation  │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  Predictor   │  │BatchPredictor│  │   DataPreprocessor   │  │
│  │              │  │              │  │                      │  │
│  │ - Single     │  │ - Async      │  │ - Normalization      │  │
│  │   Inference  │  │ - Retry      │  │ - Outlier Detection  │  │
│  │ - Confidence │  │ - Progress   │  │ - Missing Values     │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

---

## 6. 关键算法说明

### 6.1 任务路由混合决策算法

```
Algorithm: HybridRoutingDecision
Input: TaskInput(task_description, context, precision_requirement, time_sensitivity, max_latency_ms)
Output: RoutingDecision(selected_engine, confidence, reasoning)

1. ExtractFeatures(task) → TaskFeatures
   - complexity_score = min(1.0, word_count / 100)
   - logic_depth = min(1.0, logic_keyword_count / 10)
   - data_structure_ratio = structure_indicator_count / 5
   - temporal_component = any(temporal_keywords in description)
   - multimodal_input = any(multimodal_keywords in description)

2. RuleBasedScoring(features) → rule_scores[EngineType]
   - For each engine, compute score based on rule matching
   - RULE: +0.5 if rule_keywords, +0.2 if logic_depth > 0.5, +0.3 if latency < 50ms
   - LLM: +0.5 if llm_keywords, +0.2 if complexity > 0.6
   - LNN: +0.4 if temporal, +0.3 if latency < 100ms
   - HYBRID: +0.5 if multimodal, +0.2 if precision > 0.9

3. MLScoring(features) → ml_scores[EngineType]
   - For each engine:
     score = Σ(feature_value × weight[feature][engine] × importance[feature])
   - Normalize scores to sum to 1.0

4. CombineScores(rule_scores, ml_scores) → combined_scores
   - combined[engine] = 0.4 × rule_scores[engine] + 0.6 × ml_scores[engine]
   - Normalize

5. SelectBestEngine(combined_scores) → (selected_engine, confidence)
   - selected = argmax(combined_scores)
   - confidence = combined_scores[selected]
   - If confidence < threshold and enable_fallback:
     select second best engine

6. SelectModel(selected_engine, features) → model_name
   - LNN + temporal → LTC
   - LNN + fast → CFC
   - HYBRID → HybridLNN

7. GenerateReasoning(selected, features, rule_scores, ml_scores) → reasoning_text

8. Return RoutingDecision(selected_engine, model_name, confidence, reasoning)
```

### 6.2 Dempster-Shafer融合算法

```
Algorithm: DempsterShaferFusion
Input: List[InferenceResult], Optional[weights]
Output: FusionResult

1. BuildEvidences(results) → List[EngineEvidence]

2. ComputeDynamicWeights(evidences) → weights[EngineType]
   - confidence_score = evidence.confidence (40%)
   - efficiency_score = 1 / (1 + processing_time / 1000) (30%)
   - historical_score = confidence (30%)
   - weight = 0.4 × confidence + 0.3 × efficiency + 0.3 × historical
   - Normalize

3. BuildMassFunctions(evidences, weights) → List[Mass]
   - m(hypothesis_A) = confidence × weight
   - m(uncertainty) = (1 - confidence) × weight

4. DempsterCombine(mass_functions) → (combined_mass, conflict)
   - combined = mass_functions[0]
   - For each subsequent mass:
     - Compute pairwise combination
     - conflict = Σ[m1(B) × m2(C)] where B ∩ C = ∅
     - normalization = 1 - conflict
     - combined = temp_combined / normalization

5. If conflict > threshold:
   - ResolveConflict(mass_functions) → combined_mass
   - Use weighted average instead

6. ExtractPrediction(combined_mass, results) → final_prediction
   - Weighted average of all predictions

7. ComputeFusionConfidence(combined_mass, evidences) → fusion_confidence
   - fusion_confidence = 0.6 × mass(hypothesis_A) + 0.4 × consistency

8. ComputeQualityMetrics(results, fusion_confidence) → metrics

9. GenerateExplainability(results, weights, combined_mass, conflict) → report

10. BuildReasoningPath(results, combined_mass) → path

11. Return FusionResult(final_prediction, fusion_confidence, engines, method, path, report, metrics)
```

### 6.3 数据预处理算法

```
Algorithm: DataPreprocessing
Input: Raw data (numeric, categorical, text, image)
Output: Normalized features with metadata

1. HandleMissingValues(data)
   - Strategy: mean/median/zero/forward
   - Count missing values filled

2. DetectOutliers(data)
   - Method: z_score or IQR
   - z_score: |x - mean| / std > threshold
   - IQR: x < Q1 - k*IQR or x > Q3 + k*IQR
   - Apply Winsorization (clip to bounds)

3. Normalize(data)
   - Z-score: (x - mean) / std
   - Min-Max: (x - min) / (max - min)

4. Return PreprocessingResult(features, method, outlier_count, missing_count)
```

---

## 7. 错误处理与降级策略

### 7.1 异常处理层次

| 层级 | 处理策略 | 示例 |
|------|---------|------|
| L1 - 模块级 | 捕获异常，返回默认值 | 模型推理失败 → 返回空结果 |
| L2 - 引擎级 | 重试逻辑，指数退避 | 预测失败 → 最多重试3次 |
| L3 - 系统级 | 降级策略，切换引擎 | 路由决策失败 → 降级到Rule引擎 |
| L4 - 全局级 | 错误恢复，状态回滚 | 融合失败 → 返回单引擎结果 |

### 7.2 降级策略流程

```
Primary Engine Failed
    │
    ├──(1) Retry (up to 3 times with exponential backoff)
    │         │
    │         └── If still fails ──▶ (2)
    │
    ├──(2) Try secondary engine from routing alternatives
    │         │
    │         └── If unavailable ──▶ (3)
    │
    ├──(3) Fallback to Rule engine (most stable)
    │         │
    │         └── If fails ──▶ (4)
    │
    └──(4) Return error result with explanation
```

---

## 8. 扩展性设计

### 8.1 新引擎集成

```python
# 1. 定义新模型类
class CustomModel(BaseLNNModel):
    def build(self): ...
    def forward(self, x): ...
    def predict(self, x): ...

# 2. 注册到引擎
engine = HybridInferenceEngine()
engine.initialize_models()
engine.register_custom_model("CustomModel", custom_model, ModelType.CFC)

# 3. 更新路由器（可选）
router = TaskRouter()
# 在ScoringModel中添加新引擎的权重配置
```

### 8.2 模型注册表扩展

```python
registry = ModelRegistry()

# 注册自定义模型类型
registry.register_custom_model(
    model_name="MyCustomModel",
    model_class=MyCustomModelClass,
    input_dim=128,
    output_dim=10
)

# 导出/导入注册表配置
registry.export_registry("registry_config.json")
new_registry = ModelRegistry()
new_registry.import_registry("registry_config.json")
```

---

## 9. 测试策略

### 9.1 测试覆盖范围

| 模块 | 测试类型 | 覆盖内容 |
|------|---------|---------|
| 核心模型 | 单元测试 | 序列化、数据类定义 |
| CFC模型 | 单元测试 | 构建、前向传播、预测、置信度、不确定性、性能 |
| LTC模型 | 单元测试 | 序列处理、记忆重置、多步预测 |
| Hybrid模型 | 单元测试 | 多模态输入、融合方法 |
| 任务路由器 | 单元测试 | 各引擎路由、决策格式、统计 |
| 融合层 | 单元测试 | 单/多引擎融合、权重计算、质量指标 |
| 预处理 | 单元测试 | 标准化、异常值、缺失值、逆变换 |
| 后处理 | 单元测试 | JSON/XML输出、可视化数据 |
| 注册表 | 单元测试 | 注册、加载、缓存、导入导出 |
| 预测器 | 单元测试 | 单样本、批量、置信度、统计 |
| 混合引擎 | 集成测试 | 完整推理流程、批处理、自定义模型 |

### 9.2 运行测试

```bash
cd python
python -m pytest app/ai/lnn/tests/test_lnn_system.py -v
```

---

## 10. 性能指标目标

| 指标 | 目标值 | 说明 |
|------|-------|------|
| CFC推理延迟 | < 100ms | 快速推理场景 |
| LTC序列长度 | > 1000 | 时序预测能力 |
| 路由决策时间 | < 10ms | 决策效率 |
| 融合处理时间 | < 50ms | 融合效率 |
| 模型缓存命中率 | > 80% | 缓存效率 |
| 测试覆盖率 | ≥ 80% | 代码质量 |
| 系统可用性 | > 99.9% | 通过降级策略保证 |

---

## 11. 使用示例

```python
from app.ai.lnn.engine import HybridInferenceEngine
import numpy as np

# 初始化引擎
engine = HybridInferenceEngine(
    rule_weight=0.4,
    ml_weight=0.6,
    enable_fusion=True,
    enable_parallel_execution=True
)
engine.initialize_models()

# 单次推理
result = engine.infer(
    task_description="预测接下来24小时的销售数据，需要高精度",
    input_data=np.random.randn(100, 10),
    precision_requirement=0.95,
    time_sensitivity=0.7
)

print(f"预测结果: {result.final_prediction}")
print(f"置信度: {result.confidence}")
print(f"融合方法: {result.fusion_method}")
print(f"可解释性报告:\n{result.explainability_report}")

# 批量推理
tasks = [
    {"task_description": "分类任务", "input_data": np.random.randn(50, 10)},
    {"task_description": "时序预测", "input_data": np.random.randn(200, 5)},
]
results = engine.infer_batch(tasks)

# 获取引擎统计
stats = engine.get_engine_stats()
print(stats)
```
