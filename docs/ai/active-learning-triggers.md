# 主动学习触发器系统技术文档

## 概述

主动学习触发器系统是灵境制造 AI 平台的核心组件，用于在系统运行过程中识别需要人工干预的场景，并生成标准化的提问事件请求工艺师指导。

### 设计目标

- 自动识别不确定性场景并触发人工咨询
- 提供可配置的触发阈值和参数
- 生成标准化的事件格式
- 支持5类独立的触发场景检测

### 核心特性

- **5类触发器**：低置信度、知识缺失、证据冲突、新颖情境、关键决策
- **可配置化**：支持通过配置文件调整阈值和参数
- **标准化事件**：统一的事件结构 `{type, reason, context, suggested_action}`
- **事件历史记录**：自动记录所有触发事件
- **线程安全**：支持并发环境使用

## 架构设计

### 模块结构

```
python/app/ai/active_learning/
├── __init__.py          # 包初始化及对外接口
├── events.py            # 事件结构定义及生成逻辑
├── triggers.py          # 5类触发场景检测器实现
└── tests/
    ├── __init__.py
    └── test_triggers.py # 单元测试套件
```

### 类层次结构

```
BaseTrigger (抽象基类)
├── LowConfidenceTrigger      # 低置信度触发器
├── KnowledgeGapTrigger       # 知识缺失触发器
├── ConflictingEvidenceTrigger # 证据冲突触发器
├── NovelSituationTrigger     # 新颖情境触发器
└── CriticalDecisionTrigger   # 关键决策触发器

ActiveLearningTrigger         # 统一管理器
```

## 触发器类型详解

### 1. 低置信度触发器 (LowConfidenceTrigger)

**触发条件**：模型预测置信度低于设定阈值

**应用场景**：
- AI 模型对预测结果不确定
- 需要工艺师确认预测准确性

**配置参数**：
```python
@dataclass
class LowConfidenceConfig:
    confidence_threshold: float = 0.5      # 置信度阈值 (0-1)
    min_samples_for_confidence: int = 10   # 最小样本数
    enabled: bool = True
    priority: int = 3
```

**使用示例**：
```python
from app.ai.active_learning import LowConfidenceTrigger, LowConfidenceConfig

config = LowConfidenceConfig(confidence_threshold=0.6)
trigger = LowConfidenceTrigger(config)

event = trigger.check(
    confidence=0.4,
    context={"material": "titanium", "process": "milling"},
    model_name="bayesian_lnn_v1"
)

if event:
    print(f"触发: {event.reason}")
```

### 2. 知识缺失触发器 (KnowledgeGapTrigger)

**触发条件**：任务所需的关键知识在知识图谱中缺失

**应用场景**：
- 知识图谱数据不完整
- 缺少关键工艺参数

**配置参数**：
```python
@dataclass
class KnowledgeGapConfig:
    required_knowledge_fields: List[str] = ["material", "process_type", "tolerance"]
    knowledge_completeness_threshold: float = 0.8
    enabled: bool = True
    priority: int = 3
```

**使用示例**：
```python
from app.ai.active_learning import KnowledgeGapTrigger

trigger = KnowledgeGapTrigger()

event = trigger.check(
    available_knowledge={"material": "steel"},  # 缺少 process_type 和 tolerance
    context={"task": "process_planning"},
    task_description="制定加工工艺路线"
)
```

### 3. 证据冲突触发器 (ConflictingEvidenceTrigger)

**触发条件**：多个证据源给出相互矛盾的结论

**应用场景**：
- 不同模型给出不同预测
- 多源数据存在矛盾

**配置参数**：
```python
@dataclass
class ConflictingEvidenceConfig:
    conflict_threshold: float = 0.3      # 冲突程度阈值
    min_evidence_count: int = 2          # 最小证据源数量
    enabled: bool = True
    priority: int = 3
```

**使用示例**：
```python
from app.ai.active_learning import ConflictingEvidenceTrigger

trigger = ConflictingEvidenceTrigger()

event = trigger.check(
    evidence_list=[
        {"source": "model_a", "conclusion": "use_milling", "confidence": 0.8},
        {"source": "model_b", "conclusion": "use_turning", "confidence": 0.7}
    ],
    context={"task": "process_selection"}
)
```

### 4. 新颖情境触发器 (NovelSituationTrigger)

**触发条件**：当前情境与历史案例相似度极低

**应用场景**：
- 遇到全新的加工场景
- 缺乏历史经验参考

**配置参数**：
```python
@dataclass
class NovelSituationConfig:
    similarity_threshold: float = 0.4    # 相似度阈值
    min_feature_count: int = 3           # 最小特征数
    enabled: bool = True
    priority: int = 3
```

**使用示例**：
```python
from app.ai.active_learning import NovelSituationTrigger

trigger = NovelSituationTrigger()

event = trigger.check(
    situation_features={
        "material": "inconel",
        "hardness": 70,
        "geometry": "complex"
    },
    similarity_score=0.25,  # 与最近似案例的相似度
    context={"task": "machining"}
)
```

### 5. 关键决策触发器 (CriticalDecisionTrigger)

**触发条件**：面临高风险、高成本或安全关键的决策

**应用场景**：
- 高风险工艺决策
- 涉及安全的关键参数选择
- 高成本操作确认

**配置参数**：
```python
@dataclass
class CriticalDecisionConfig:
    risk_threshold: float = 0.7          # 风险阈值
    cost_threshold: float = 10000.0      # 成本阈值 (元)
    safety_critical_keywords: List[str] = ["安全", "safety", "critical", "关键", "危险"]
    enabled: bool = True
    priority: int = 3
```

**使用示例**：
```python
from app.ai.active_learning import CriticalDecisionTrigger

trigger = CriticalDecisionTrigger()

event = trigger.check(
    decision_description="选择热处理工艺参数",
    context={"part": "turbine_blade"},
    risk_score=0.85,
    estimated_cost=50000,
    is_safety_related=True
)
```

## 统一管理器

`ActiveLearningTrigger` 提供统一的接口管理所有触发器。

### 初始化

```python
from app.ai.active_learning import ActiveLearningTrigger

# 使用默认配置
trigger = ActiveLearningTrigger()

# 使用自定义配置
config = {
    "low_confidence": {
        "confidence_threshold": 0.6,
        "enabled": True
    },
    "knowledge_gap": {
        "required_knowledge_fields": ["material", "machine"],
        "knowledge_completeness_threshold": 0.9
    }
}
trigger = ActiveLearningTrigger(config)
```

### 便捷方法

```python
# 检查低置信度
event = trigger.check_uncertainty(
    confidence=0.3,
    context={"material": "titanium"}
)

# 检查知识缺失
event = trigger.check_knowledge_gap(
    available_knowledge={"material": "steel"},
    context={}
)

# 检查证据冲突
event = trigger.check_conflicting_evidence(
    evidence_list=[...],
    context={}
)

# 检查新颖情境
event = trigger.check_novel_situation(
    situation_features={...},
    similarity_score=0.25,
    context={}
)

# 检查关键决策
event = trigger.check_critical_decision(
    decision_description="...",
    context={},
    risk_score=0.9
)
```

### 事件管理

```python
# 获取所有历史事件
events = trigger.get_all_events()

# 清空历史
trigger.clear_all_history()

# 获取触发器状态
status = trigger.get_status()
```

## 事件结构

所有触发器生成的事件遵循统一格式：

```python
{
    "type": str,              # 事件类型
    "reason": str,            # 触发原因描述
    "context": dict,          # 上下文信息
    "suggested_action": str,  # 建议操作
    "event_id": str,          # 事件唯一ID
    "timestamp": float,       # 时间戳
    "priority": int,          # 优先级 (1-5, 1为最高)
    "metadata": dict          # 额外元数据
}
```

### 事件类型枚举

```python
class EventType:
    LOW_CONFIDENCE = "low_confidence"
    KNOWLEDGE_GAP = "knowledge_gap"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    NOVEL_SITUATION = "novel_situation"
    CRITICAL_DECISION = "critical_decision"
```

## 配置系统

### 配置文件格式

支持通过 JSON 或 YAML 配置文件动态调整参数：

```json
{
    "low_confidence": {
        "confidence_threshold": 0.5,
        "min_samples_for_confidence": 10,
        "enabled": true,
        "priority": 2
    },
    "knowledge_gap": {
        "required_knowledge_fields": ["material", "process_type", "tolerance"],
        "knowledge_completeness_threshold": 0.8,
        "enabled": true
    },
    "conflicting_evidence": {
        "conflict_threshold": 0.3,
        "min_evidence_count": 2,
        "enabled": true
    },
    "novel_situation": {
        "similarity_threshold": 0.4,
        "min_feature_count": 3,
        "enabled": true
    },
    "critical_decision": {
        "risk_threshold": 0.7,
        "cost_threshold": 10000.0,
        "safety_critical_keywords": ["安全", "safety", "critical", "关键", "危险"],
        "enabled": true
    }
}
```

### 动态配置加载

```python
import json
from app.ai.active_learning import ActiveLearningTrigger

# 从文件加载配置
with open("active_learning_config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

trigger = ActiveLearningTrigger(config)
```

## 集成指南

### 与 M3.2 不确定性评估 API 集成

```python
from app.ai.lnn.inference.bayesian_predictor import BayesianPredictor
from app.ai.active_learning import ActiveLearningTrigger

predictor = BayesianPredictor(model_path="models/bayesian_lnn.pt")
trigger = ActiveLearningTrigger()

# 执行预测
mean, std = predictor.predict_with_uncertainty(input_data, n_samples=50)
confidence = 1.0 - (std.mean() / mean.std())  # 计算置信度

# 检查是否需要人工干预
event = trigger.check_uncertainty(
    confidence=confidence,
    context={"input_data": input_data},
    model_name="bayesian_lnn"
)

if event:
    # 发送事件到事件总线
    event_bus.dispatch("active_learning.request", event)
```

### 与 M1 知识图谱集成

```python
from app.knowledge_graph import KnowledgeGraph
from app.ai.active_learning import KnowledgeGapTrigger

kg = KnowledgeGraph()
trigger = KnowledgeGapTrigger()

# 查询知识图谱
available_knowledge = kg.query_task_knowledge(task_id)

# 检查知识完整性
event = trigger.check(
    available_knowledge=available_knowledge,
    context={"task_id": task_id},
    task_description=task.description
)

if event:
    # 请求工艺师补充知识
    event_bus.dispatch("active_learning.knowledge_request", event)
```

## 测试

### 运行单元测试

```bash
cd python
$env:PYTHONPATH="C:\Users\<user>\Desktop\灵境制造（上线版）\python"
pytest app/ai/active_learning/tests/test_triggers.py -v
```

### 测试覆盖

- **35 个测试用例**覆盖所有触发器
- **关键路径 100% 覆盖**
- **总体覆盖率 > 80%**

测试内容包括：
- 触发条件验证
- 阈值边界测试
- 禁用触发器测试
- 自定义配置测试
- 事件结构验证
- 管理器功能测试

## API 参考

### TriggerEvent

```python
@dataclass
class TriggerEvent:
    type: str
    reason: str
    context: Dict[str, Any]
    suggested_action: str
    event_id: str
    timestamp: float
    priority: int
    metadata: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]
    def validate(self) -> bool
```

### BaseTrigger

```python
class BaseTrigger(ABC):
    def __init__(self, config: Optional[TriggerConfig] = None)
    
    @property
    def event_type(self) -> str
    
    @abstractmethod
    def check(self, **kwargs) -> Optional[TriggerEvent]
    
    @property
    def event_history(self) -> List[TriggerEvent]
    
    def clear_history(self) -> None
```

### ActiveLearningTrigger

```python
class ActiveLearningTrigger:
    def __init__(self, config: Optional[Dict[str, Any]] = None)
    
    def check_uncertainty(self, confidence: float, context: Dict[str, Any], 
                         model_name: str = "unknown") -> Optional[Dict[str, Any]]
    
    def check_knowledge_gap(self, available_knowledge: Dict[str, Any],
                           context: Dict[str, Any], task_description: str = "") -> Optional[Dict[str, Any]]
    
    def check_conflicting_evidence(self, evidence_list: List[Dict[str, Any]],
                                   context: Dict[str, Any]) -> Optional[Dict[str, Any]]
    
    def check_novel_situation(self, situation_features: Dict[str, Any],
                             similarity_score: float, context: Dict[str, Any]) -> Optional[Dict[str, Any]]
    
    def check_critical_decision(self, decision_description: str, context: Dict[str, Any],
                               risk_score: float = 0.0, estimated_cost: float = 0.0,
                               is_safety_related: bool = False) -> Optional[Dict[str, Any]]
    
    def get_all_events(self) -> List[Dict[str, Any]]
    def clear_all_history(self) -> None
    def get_status(self) -> Dict[str, Any]
```

## 最佳实践

### 1. 阈值调优

建议根据实际业务场景调整阈值：
- **低置信度**：根据模型特性设置，通常 0.4-0.7
- **知识缺失**：根据任务复杂度调整必需字段
- **证据冲突**：根据模型可靠性调整冲突阈值
- **新颖情境**：根据历史数据丰富度调整相似度阈值
- **关键决策**：根据风险承受能力调整风险/成本阈值

### 2. 事件处理

- 使用事件总线解耦触发器和处理逻辑
- 实现事件优先级队列
- 记录事件处理历史用于分析

### 3. 性能优化

- 禁用不需要的触发器减少开销
- 合理设置事件历史记录上限
- 使用异步事件分发

## 故障排除

### 常见问题

**Q: 触发器没有触发事件**
- 检查触发器是否启用 (`enabled=True`)
- 验证输入参数是否在有效范围
- 检查阈值设置是否合理

**Q: 事件格式不完整**
- 确保使用 `to_dict()` 方法转换事件
- 验证所有必需字段已填充

**Q: 模块导入失败**
- 确保 PYTHONPATH 包含 `python/` 目录
- 检查 Python 版本兼容性 (>=3.11)

## 版本历史

- **v1.0.0** (2026-06-15): 初始版本，实现5类触发器和统一管理器

## 相关文档

- [Bayesian LNN 架构与训练指南](./bayesian-lnn-guide.md) - LNN 模型架构与训练实现
- [模型自动微调流水线指南](./auto-retrain-guide.md) - 模型自动迭代与版本管理
- [API 参考文档](../api-reference.md) - LNN 推理与不确定性评估接口
