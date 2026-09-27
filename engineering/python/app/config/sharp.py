"""SHARP 三元组验证智能体配置（Schema-Hybrid Agent for Reliable Prediction）。"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config._utils import _bool_env, _env, _float_env, _int_env


@dataclass
class SharpConfig:
    """SHARP 三元组验证智能体配置。

    对应论文 4 大组件的运行时开关与超参数，所有项均可通过环境变量覆盖：
    - ``LNN_SHARP_MAX_REACT_STEPS``：ReAct 循环最大步数（默认 8）
    - ``LNN_SHARP_CONFIDENCE_THRESHOLD``：终止置信度阈值（默认 0.85）
    - ``LNN_SHARP_EVIDENCE_CONVERGENCE_WINDOW``：证据收敛窗口（默认 2）
    - ``LNN_SHARP_MEMORY_TOP_K``：Memory-Augmented 检索 Top-K（默认 3）
    - ``LNN_SHARP_TOOL_EVIDENCE_TOP_K``：单工具证据返回 Top-K（默认 5）
    - ``LNN_SHARP_ENABLE_SCHEMA_PLANNER``：开关 Schema-Aware 规划器
    - ``LNN_SHARP_ENABLE_MEMORY_AUGMENT``：开关 Memory-Augmented 机制
    - ``LNN_SHARP_ENABLE_HYBRID_TOOLSET``：开关 Hybrid Knowledge Toolset
    - ``LNN_SHARP_ENABLE_REACT_LOOP``：开关 ReAct 循环（关则降级为单次 LLM 推理）
    - ``LNN_SHARP_ABLATION_MODE``：消融模式（none/no_schema/no_memory/no_react/no_toolset）

    消融模式优先级高于单独开关：设置 ``LNN_SHARP_ABLATION_MODE=no_memory``
    会强制 ``enable_memory_augment=False``，由 SharpService 在构建 pipeline 时处理。
    """

    max_react_steps: int = field(default_factory=lambda: _int_env("LNN_SHARP_MAX_REACT_STEPS", 8))
    confidence_threshold: float = field(default_factory=lambda: _float_env("LNN_SHARP_CONFIDENCE_THRESHOLD", 0.85))
    evidence_convergence_window: int = field(
        default_factory=lambda: _int_env("LNN_SHARP_EVIDENCE_CONVERGENCE_WINDOW", 2)
    )
    memory_top_k: int = field(default_factory=lambda: _int_env("LNN_SHARP_MEMORY_TOP_K", 3))
    tool_evidence_top_k: int = field(default_factory=lambda: _int_env("LNN_SHARP_TOOL_EVIDENCE_TOP_K", 5))
    enable_schema_planner: bool = field(default_factory=lambda: _bool_env("LNN_SHARP_ENABLE_SCHEMA_PLANNER", True))
    enable_memory_augment: bool = field(default_factory=lambda: _bool_env("LNN_SHARP_ENABLE_MEMORY_AUGMENT", True))
    enable_hybrid_toolset: bool = field(default_factory=lambda: _bool_env("LNN_SHARP_ENABLE_HYBRID_TOOLSET", True))
    enable_react_loop: bool = field(default_factory=lambda: _bool_env("LNN_SHARP_ENABLE_REACT_LOOP", True))
    ablation_mode: str = field(default_factory=lambda: _env("LNN_SHARP_ABLATION_MODE", ""))

    @property
    def resolved_ablation_mode(self) -> str | None:
        """规范化消融模式：空字符串或 'none' 视为 None（完整 SHARP）。"""
        v = (self.ablation_mode or "").strip().lower()
        if not v or v == "none":
            return None
        return v
