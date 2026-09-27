"""研究模块与产品轨之间的稳定 API 契约。

为什么需要这个目录：
- 研究模块改算法时不能破坏产品代码
- 产品代码调用研究模块时不能依赖研究模块的内部实现
- 这个目录定义了"稳定不变"的接口

规则：
- 本目录下的 dataclass/Protocol 是不可变的
- 研究模块可以增加字段，但不允许删除/重命名
- 产品轨通过 research_api_client 调用本目录中定义的能力
"""
from .feature_recognizer import (
    IFeatureRecognizer,
    RecognitionInput,
    RecognitionResult,
    RecognizedFeature,
    FeatureType,
    RecognizerCapabilities,
)

__all__ = [
    "IFeatureRecognizer",
    "RecognitionInput",
    "RecognitionResult",
    "RecognizedFeature",
    "FeatureType",
    "RecognizerCapabilities",
]
