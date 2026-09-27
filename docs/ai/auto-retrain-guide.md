# 模型自动微调流水线使用指南

## 概述

模型自动微调流水线（Auto Retrain Pipeline）是一个自动化模型优化系统，能够基于新数据自动触发模型训练流程，确保模型持续适应最新数据分布。

### 核心特性

- **双重触发机制**：支持定时调度 + 数据量阈值触发
- **自动数据准备**：从数据湖提取、清洗、预处理训练数据
- **模型评估体系**：验证集评估达标后才能注册新模型
- **版本管理**：保留N个历史版本，不删除老模型
- **任务集成**：复用现有AsyncTaskManager进行异步任务管理

### 设计原则

1. **新模型必须评估**：只有在验证集上达到预设指标的模型才能注册
2. **老模型不删除**：系统保留N个历史版本（可配置）
3. **不要每次都微调**：当新数据量未达到设定阈值时，即使到了定时触发时间也不启动微调
4. **不要改训练算法**：复用现有LNNTrainer实现，不修改核心训练算法

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                  Auto Retrain Scheduler                  │
│  ┌──────────────┐         ┌──────────────────────┐     │
│  │ 定时触发器   │         │  阈值触发器          │     │
│  │ (Cron/间隔)  │         │  (数据量监控)        │     │
│  └──────────────┘         └──────────────────────┘     │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                   Data Preparator                        │
│  • 从数据湖提取数据                                      │
│  • 数据清洗与验证                                        │
│  • 训练集/验证集划分                                     │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                   LNN Trainer (复用)                     │
│  • 模型训练                                              │
│  • 检查点保存                                            │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                   Model Evaluator                        │
│  • 验证集评估                                            │
│  • 指标计算（val_loss, val_accuracy, val_r2）           │
│  • 达标判定                                              │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│                Model Registry Service                    │
│  • 模型版本注册                                          │
│  • 历史版本管理                                          │
└─────────────────────────────────────────────────────────┘
```

## 模块说明

### 1. scheduler.py - 调度器

负责触发机制的实现，包括：

- **定时触发**：支持cron表达式或时间间隔配置
- **阈值触发**：监控新数据量，达到阈值时触发
- **触发抑制**：数据量不足时不触发微调

#### 配置参数

```python
AutoRetrainConfig(
    schedule_enabled=True,           # 启用定时触发
    schedule_cron="0 2 * * 0",       # 每周日凌晨2点
    schedule_interval_hours=168,     # 7天间隔
    
    threshold_enabled=True,          # 启用阈值触发
    min_samples_threshold=100,       # 最小样本数阈值
    
    max_concurrent_training=1,       # 最大并发训练数
    training_timeout_hours=24,       # 训练超时时间
    
    data_lookback_days=7,            # 数据回溯天数
    max_model_versions=5,            # 保留的最大模型版本数
)
```

### 2. data_prep.py - 数据准备

负责训练数据的提取、清洗和预处理：

- **数据提取**：从数据湖按时间范围提取新数据
- **数据清洗**：验证样本有效性，过滤无效数据
- **数据集划分**：自动划分训练集和验证集（默认80/20）
- **DataLoader创建**：生成PyTorch兼容的DataLoader

#### 数据格式要求

训练样本必须包含以下字段：

```python
{
    "record_id": "unique_id",
    "features": {
        "spindle_speed": 1000,
        "feed_rate": 100,
        "depth_of_cut": 5,
        "machine_id": "M001",
        "tool_id": "T001",
        "workpiece_material": "steel"
    },
    "labels": {
        "first_pass_acceptance": True,
        "actual_dimensions": [10.5, 20.3, 15.2],
        "surface_roughness": 1.2
    },
    "timestamp": "2024-01-01T10:00:00"
}
```

### 3. evaluator.py - 模型评估

负责训练后模型的评估与验证：

- **指标计算**：val_loss, val_accuracy, val_r2
- **绝对指标检查**：验证指标是否达到预设阈值
- **相对改进检查**：与基线模型比较改进幅度
- **模型注册**：评估通过后自动注册到模型注册服务

#### 评估配置

```python
EvaluationConfig(
    min_val_accuracy=0.85,           # 最低验证准确率
    max_val_loss=0.5,                # 最大验证损失
    min_val_r2=0.7,                  # 最低R²分数
    
    require_improvement=True,        # 是否要求相对改进
    min_improvement_percent=1.0,     # 最小改进百分比
)
```

## 使用指南

### 1. 手动触发微调

```bash
cd python
python -m app.ai.auto_retrain.scheduler --trigger-now
```

期望输出：
```
Trigger result: {
    "success": True,
    "task_id": "training-abc123",
    "trigger_reason": "manual_cli",
    "new_samples": 150
}
```

### 2. 查询任务状态

```bash
curl http://localhost:8765/api/v1/jobs | grep "auto_retrain"
```

期望结果：能够在任务列表中找到状态为"已提交"或"运行中"的auto_retrain任务。

### 3. 运行单元测试

```bash
cd python
pytest app/ai/auto_retrain/tests/ -v
```

期望结果：所有单元测试通过，无失败用例。

## 配置指南

### 环境变量配置

可以在启动时通过环境变量覆盖默认配置：

```bash
export AUTO_RETRAIN_THRESHOLD=100
export AUTO_RETRAIN_SCHEDULE_HOURS=168
export AUTO_RETRAIN_MAX_VERSIONS=5
```

### 代码配置

```python
from app.ai.auto_retrain import AutoRetrainScheduler, AutoRetrainConfig

config = AutoRetrainConfig(
    min_samples_threshold=200,
    schedule_interval_hours=24,
    max_model_versions=10,
)

scheduler = AutoRetrainScheduler(config=config)
await scheduler.start()
```

## 维护与故障排除

### 常见问题

#### 1. 微调未触发

**原因**：
- 新数据量未达到阈值
- 定时触发间隔未到

**解决方案**：
- 检查数据湖中的新数据量：`data_lake.get_statistics()`
- 调整阈值配置：`min_samples_threshold`
- 手动触发：`python -m app.ai.auto_retrain.scheduler --trigger-now`

#### 2. 训练任务失败

**原因**：
- 数据质量问题
- GPU内存不足
- 训练超时

**解决方案**：
- 检查训练日志：`logs/training_*.log`
- 减小batch_size或切换到CPU模式
- 增加超时时间：`training_timeout_hours`

#### 3. 模型评估未通过

**原因**：
- 验证损失过高
- R²分数过低
- 相对改进不足

**解决方案**：
- 检查数据质量
- 调整评估阈值：`EvaluationConfig`
- 增加训练轮数

### 日志位置

- 调度器日志：`logs/auto_retrain_scheduler.log`
- 训练日志：`logs/training_*.log`
- 评估日志：`logs/model_evaluation.log`

### 监控指标

关键监控指标：

- `auto_retrain_last_trigger_time`：上次触发时间
- `auto_retrain_new_samples_count`：新数据量
- `auto_retrain_task_status`：任务状态
- `model_val_loss`：验证损失
- `model_val_r2`：R²分数

## 版本管理策略

### 版本命名

自动生成的版本标签格式：`v{timestamp}`

示例：`v1704067200`

### 版本保留

- 默认保留最近5个版本（可配置）
- 老版本不删除，仅标记为`archived`
- 支持版本回滚

### 版本信息

每个版本记录以下信息：

```python
{
    "model_name": "lnn_model",
    "version": "v1704067200",
    "metrics": {
        "val_loss": 0.35,
        "val_accuracy": 0.88,
        "val_r2": 0.82
    },
    "training_params": {
        "epochs": 100,
        "learning_rate": 0.001,
        "batch_size": 64
    },
    "data_stats": {
        "total_samples": 1500,
        "train_samples": 1200,
        "val_samples": 300
    },
    "registered_at": "2024-01-01T10:00:00"
}
```

## 性能优化建议

### 1. 数据准备优化

- 使用增量数据提取，避免重复处理
- 合理设置`data_lookback_days`，平衡数据量和时效性

### 2. 训练优化

- 根据GPU内存调整`batch_size`
- 使用混合精度训练（AMP）加速
- 合理设置`early_stopping_patience`避免过拟合

### 3. 评估优化

- 根据业务需求调整评估阈值
- 对于回归任务，可以放宽`min_val_accuracy`要求
- 使用`require_improvement=False`跳过相对改进检查

## 扩展开发

### 自定义触发器

```python
from app.ai.auto_retrain.scheduler import AutoRetrainScheduler

class CustomScheduler(AutoRetrainScheduler):
    async def check_and_trigger(self):
        # 自定义触发逻辑
        if self._custom_condition():
            return await self.trigger_retrain("custom_trigger")
```

### 自定义评估指标

```python
from app.ai.auto_retrain.evaluator import ModelEvaluator

class CustomEvaluator(ModelEvaluator):
    def _compute_metrics(self, model, val_loader, device):
        metrics = super()._compute_metrics(model, val_loader, device)
        # 添加自定义指标
        metrics["custom_metric"] = self._compute_custom_metric(model, val_loader)
        return metrics
```

## 附录

### A. 依赖模块

- `app.ai.lnn.training.trainer.LNNTrainer`：训练器
- `app.tasks.task_system.AsyncTaskManager`：任务管理器
- `app.services.model_registry_service.ModelRegistryService`：模型注册服务
- `app.training.data_lake.TrainingDataLake`：训练数据湖

### B. 相关API

- `POST /api/v1/lnn/models/train`：手动触发训练
- `GET /api/v1/jobs`：查询任务列表
- `GET /api/v1/lnn/models`：查询模型列表

### C. 参考资料

- [Bayesian LNN 架构与训练指南](./bayesian-lnn-guide.md) - LNN 训练器实现细节
- [主动学习触发器系统](./active-learning-triggers.md) - 不确定性场景识别
- LNN 训练器源码：[`python/app/ai/lnn/training/trainer.py`](../../python/app/ai/lnn/training/trainer.py)

---

**文档版本**：1.0.0  
**最后更新**：2024-01-01  
**维护者**：灵境制造AI团队
