# P1-3 dxf pipeline 六阶段声明化

**创建日期**: 2026-08-20  
**状态**: 已完成（模块 2026-08-22 cbd087c4；委托接线 2026-08-23 f3ee1e07——pipeline.py:31 导入，193–341 行 8 处调用。2026-09-13 复核统一口径；原"待接线"状态过期）

---

## 目标

将 `app/dxf/pipeline.py` 的「六阶段编排判定」抽取为纯 Python 白盒声明
（P1-1 方法论复用），使阶段定义/致命性/进度/摘要逻辑零框架依赖、
CI 可独立跑全量覆盖。

## 已交付

### 白盒声明模块
`app/dxf/_pipeline_stages.py`（纯 stdlib，零框架依赖）：

| 声明/函数 | 对应 pipeline.py 语义 |
|---|---|
| `STAGES`（6 阶段，顺序即执行序） | 六阶段编排顺序 |
| `stage_name(key)` | 阶段中文名（与 DxfPipelineStage name 逐字对齐） |
| `stage_failure_is_fatal(key)` | Stage3 3D模型转换可降级（False），其余致命（True） |
| `should_abort_after(key, failed)` | 失败阶段是否中止流水线 |
| `progress_of(statuses)` | 完成度 0-1（成功/失败计入已完成） |
| `summarize_pipeline(statuses, success)` | 摘要（「流水线在X阶段失败」） |
| `stage_index(key)` | 阶段序号 |

### 测试
`engineering/python/tests/unit/test_dxf_pipeline_stages.py`（~25 用例）：
阶段对齐 / 致命性 / 中止判定 / 进度计算 / 摘要生成。

## 待接线（文件锁解除后执行，委托路径保留框架调用）

### pipeline.py 委托（3 处）

1. **阶段声明复用**——`DxfPipelineStage(name=...)` 改为从声明取名称：
```python
from app.dxf._pipeline_stages import STAGES, stage_name
# Stage1: name=stage_name(StageKey.PARSE)  → "DXF解析"
```

2. **Stage3 降级判定**——3D模型转换失败是否降级继续：
```python
from app.dxf._pipeline_stages import stage_failure_is_fatal
# except 块中：is_fatal = stage_failure_is_fatal(StageKey.MODEL_CONVERT)  # False → 降级
```

3. **结果摘要**——`result.summary` 生成：
```python
from app.dxf._pipeline_stages import summarize_pipeline
# 各失败分支：result.summary = summarize_pipeline(statuses, success=False)
```

### 导出
`app/dxf/__init__.py` 追加：
```python
from app.dxf._pipeline_stages import (
    STAGES, StageKey, StageStatus,
    stage_name, stage_failure_is_fatal, should_abort_after,
    progress_of, summarize_pipeline,
)
```

## 验收标准（门禁）

1. ruff check app/dxf/ 全绿
2. mypy 0 错误（白盒模块无 torch）
3. `_pipeline_stages.py` 行覆盖 **100%**
4. 既有 dxf pipeline 测试（委托后行为不变）全绿
5. 阶段名与 pipeline.py 逐字对齐（测试已锁定）

## 变更日志

### v1.0 (2026-08-20)
- 白盒声明模块 `_pipeline_stages.py` 落地
- 测试 `test_dxf_pipeline_stages.py` 落地（~25 用例）
- 待办：pipeline.py 委托接线 + 导出（文件锁解除后）
