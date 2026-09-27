"""Task router subpackage for the LNN hybrid inference engine.

Status: Fully implemented (2026-07-13 修订).

The router module provides hybrid (rule + ML) decision logic for selecting
the optimal inference engine per task. The full algorithm described in
``ARCHITECTURE.md`` §3.3 is implemented in ``task_router.py``:

- **规则评分**：基于 ``TaskCategory`` × ``EngineType`` 亲和度矩阵 + 延迟/
  精度/数据类型调整，归一化后输出每引擎得分。
- **在线 ML 评分**：基于各引擎 ``update_outcome`` 反馈的滚动成功率，
  采用贝叶斯收缩（pseudo-count=5）正则化，避免冷启动零分。
- **混合决策**：``score = 0.4 * rule_score + 0.6 * ml_score``，取最高分
  引擎，并填充 ``alternatives`` 备选列表供调用方回退。

下游消费者（如 ``research/agents_research/agents.py``）可安全导入公共
API。``HybridInferenceEngine.get_engine_stats()`` 显式输出
``stub_implementation: False`` 以确认非 stub 实现。

See ARCHITECTURE.md for the target design contract.
"""

from app.ai.lnn.router.task_router import TaskRouter

__all__ = ["TaskRouter"]
