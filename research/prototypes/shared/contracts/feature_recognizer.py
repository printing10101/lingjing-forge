"""特征识别器契约：研究模块识别 DXF/CV 三视图中的几何特征的稳定接口。

这是产品轨调用研究模块识别能力的唯一入口。
新增识别类型时，应先在本文件中定义 FeatureType，再去研究模块中实现。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class FeatureType(str, Enum):
    """可识别的几何特征类型。"""

    BOX = "box"  # 长方体
    CYLINDER = "cylinder"  # 圆柱
    HOLE = "hole"  # 圆孔
    CHAMFER = "chamfer"  # 倒角
    FILLET = "fillet"  # 圆角
    STEP = "step"  # 台阶
    SLOT = "slot"  # 键槽
    POCKET = "pocket"  # 凹腔
    BOSS = "boss"  # 凸台
    THREAD = "thread"  # 螺纹


@dataclass
class RecognizedFeature:
    """一个被识别出来的特征。"""

    type: FeatureType
    # 位置（在 DXF 世界坐标）
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # 尺寸参数（按 type 不同而不同）
    params: dict = field(default_factory=dict)
    # 置信度 0.0 - 1.0
    confidence: float = 0.0
    # 来源层（DXF 图层名）
    source_layer: Optional[str] = None
    # 备注
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "position": list(self.position),
            "params": self.params,
            "confidence": self.confidence,
            "source_layer": self.source_layer,
            "note": self.note,
        }


@dataclass
class RecognitionInput:
    """识别输入。"""

    # 至少有一个：DXF 路径 / 三视图图片路径
    dxf_path: Optional[str] = None
    image_paths: Optional[list[str]] = None  # [front, top, side]
    # 元信息
    file_hash: Optional[str] = None  # 用于缓存
    extra: dict = field(default_factory=dict)


@dataclass
class RecognitionResult:
    """识别结果。"""

    status: str  # "ok" | "error" | "partial"
    features: list[RecognizedFeature] = field(default_factory=list)
    overall_confidence: float = 0.0
    latency_ms: int = 0
    error_message: Optional[str] = None
    # 元信息
    recognizer_name: str = ""
    recognizer_version: str = "1.0.0"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "features": [f.to_dict() for f in self.features],
            "overall_confidence": self.overall_confidence,
            "latency_ms": self.latency_ms,
            "error_message": self.error_message,
            "recognizer_name": self.recognizer_name,
            "recognizer_version": self.recognizer_version,
        }


@dataclass
class RecognizerCapabilities:
    """识别器能力声明。"""

    name: str
    version: str
    supported_features: list[FeatureType]
    requires_image: bool = False
    requires_dxf: bool = True
    min_confidence_threshold: float = 0.5


class IFeatureRecognizer(ABC):
    """特征识别器抽象接口。"""

    @abstractmethod
    def recognize(self, input: RecognitionInput) -> RecognitionResult:
        """执行识别。"""

    @abstractmethod
    def get_capabilities(self) -> RecognizerCapabilities:
        """返回能力声明。"""

    @abstractmethod
    def warmup(self) -> None:
        """预热模型（首次加载时跑一次）。"""


# 默认实现：基于规则的特征识别器（baseline）
class RuleBasedFeatureRecognizer(IFeatureRecognizer):
    """基于规则的特征识别器：作为 A/B 测试的 baseline。"""

    def recognize(self, input: RecognitionInput) -> RecognitionResult:
        # baseline 实际逻辑在 python/app/dxf/feature_extractor.py
        # 这里仅返回空 result 以保持契约完整
        return RecognitionResult(
            status="ok",
            features=[],
            overall_confidence=1.0,
            recognizer_name="rule_based_recognizer",
            recognizer_version="1.0.0",
        )

    def get_capabilities(self) -> RecognizerCapabilities:
        return RecognizerCapabilities(
            name="rule_based_recognizer",
            version="1.0.0",
            supported_features=[
                FeatureType.BOX,
                FeatureType.CYLINDER,
                FeatureType.HOLE,
            ],
            requires_dxf=True,
        )

    def warmup(self) -> None:
        pass
