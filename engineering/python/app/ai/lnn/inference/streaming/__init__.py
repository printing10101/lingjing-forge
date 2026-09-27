"""流式长时序推理引擎（包化拆分：原 streaming.py → streaming/ 子模块）。

对外 API 完全兼容：``from app.ai.lnn.inference.streaming import ...`` 继续可用。
"""

from .config import HiddenStatePage, KeyframeDecision, StreamingConfig
from .cache import PagedHiddenStateCache
from .selector import KeyframeSelector
from .context import AnchorContext
from .memory import TrajectoryMemory
from .predictor import StreamingPredictor

__all__ = [
    "StreamingConfig",
    "KeyframeDecision",
    "HiddenStatePage",
    "PagedHiddenStateCache",
    "KeyframeSelector",
    "AnchorContext",
    "TrajectoryMemory",
    "StreamingPredictor",
]
