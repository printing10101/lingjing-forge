"""RAG 检索模型（V3.0 自 rag_retrieval.py 拆分）。"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import dataclass, field
from enum import Enum


class QueryIntent(Enum):
    MATERIAL_WEAR = "material_wear"
    CUTTING_PARAMS = "cutting_params"
    VIBRATION_WEAR = "vibration_wear"
    MATERIAL_COMPARE = "material_compare"
    CROSS_SOURCE = "cross_source"
    SIGNAL_FUSION = "signal_fusion"
    GENERAL = "general"


@dataclass
class RetrievalRule:
    intent: QueryIntent
    source_filters: list[str] = field(default_factory=list)
    metadata_filters: dict = field(default_factory=dict)
    keyword_boost: dict = field(default_factory=dict)
    n_results: int = 5
    priority: int = 1
    # v3: pipeline 分级 (fast=跳过增强, standard=混合检索+reranker, full=全部)
    pipeline_level: str = "standard"
    # v3: 聚类预分组标签，用于缩小检索范围（None 表示不限制）
    cluster_tag: str | None = None
    # v3: 是否启用 entity 倒排索引扩展检索
    use_entity_index: bool = True


RETRIEVAL_RULES: dict[QueryIntent, RetrievalRule] = {
    QueryIntent.MATERIAL_WEAR: RetrievalRule(
        intent=QueryIntent.MATERIAL_WEAR,
        source_filters=["uniwear-nuaa", "uniwear-phm2010", "uniwear"],
        metadata_filters={"category": "tool_wear"},
        keyword_boost={
            "TC4": 3.0,
            "钛合金": 3.0,
            "Ti-6Al-4V": 3.0,
            "HRC52": 3.0,
            "不锈钢": 2.5,
            "磨损": 2.0,
            "刀具": 1.5,
        },
        n_results=8,
        priority=1,
        pipeline_level="standard",
        cluster_tag="material_wear",
    ),
    QueryIntent.CUTTING_PARAMS: RetrievalRule(
        intent=QueryIntent.CUTTING_PARAMS,
        source_filters=["uniwear-phm2010", "bosch_cnc"],
        metadata_filters={"category": "tool_wear"},
        keyword_boost={
            "HRC52": 4.0,
            "不锈钢": 4.0,
            "切削参数": 3.0,
            "切削速度": 2.5,
            "进给量": 2.5,
            "切削深度": 2.5,
            "PHM2010": 3.0,
        },
        n_results=8,
        priority=1,
        pipeline_level="standard",
        cluster_tag="cutting_params",
    ),
    QueryIntent.VIBRATION_WEAR: RetrievalRule(
        intent=QueryIntent.VIBRATION_WEAR,
        source_filters=["uniwear-nuaa", "uniwear-phm2010", "bosch_cnc", "uniwear", "signal_fusion"],
        metadata_filters={"has_vibration": True},
        keyword_boost={
            "振动": 4.0,
            "RMS": 3.0,
            "频域": 2.5,
            "声发射": 2.5,
            "磨损关联": 3.0,
            "信号分析": 2.0,
            "监测": 1.5,
        },
        n_results=10,
        priority=1,
        pipeline_level="standard",
        cluster_tag="vibration_wear",
    ),
    QueryIntent.MATERIAL_COMPARE: RetrievalRule(
        intent=QueryIntent.MATERIAL_COMPARE,
        source_filters=["uniwear", "uniwear-nuaa", "uniwear-phm2010"],
        metadata_filters={},
        keyword_boost={
            "TC4": 3.0,
            "HRC52": 3.0,
            "钛合金": 3.0,
            "不锈钢": 3.0,
            "对比": 2.5,
            "材料差异": 2.5,
            "工艺对比": 2.5,
        },
        n_results=10,
        priority=1,
        pipeline_level="full",
        cluster_tag="material_compare",
    ),
    QueryIntent.CROSS_SOURCE: RetrievalRule(
        intent=QueryIntent.CROSS_SOURCE,
        source_filters=["bosch_cnc", "uniwear-nuaa", "uniwear-phm2010", "cross_source", "signal_fusion"],
        metadata_filters={},
        keyword_boost={
            "Bosch": 3.0,
            "Uniwear": 3.0,
            "多源": 3.0,
            "对比": 2.5,
            "交叉验证": 3.0,
            "联合分析": 2.5,
        },
        n_results=10,
        priority=1,
        pipeline_level="full",
        cluster_tag="cross_source",
    ),
    QueryIntent.SIGNAL_FUSION: RetrievalRule(
        intent=QueryIntent.SIGNAL_FUSION,
        source_filters=["signal_fusion"],
        metadata_filters={},
        keyword_boost={
            "信号样本": 3.0,
            "vibration": 3.0,
            "cutting_force": 3.0,
            "acoustic_emission": 3.0,
            "温度": 2.5,
            "电流": 2.5,
            "RMS": 2.5,
            "频谱": 2.0,
            "峭度": 2.0,
            "多模态": 2.5,
            "融合": 2.0,
        },
        n_results=10,
        priority=1,
        pipeline_level="standard",
        cluster_tag="signal_fusion",
        use_entity_index=False,
    ),
    QueryIntent.GENERAL: RetrievalRule(
        intent=QueryIntent.GENERAL,
        source_filters=[],
        metadata_filters={},
        keyword_boost={
            "刀具": 1.5,
            "磨损": 1.5,
            "加工": 1.2,
            "工艺": 1.2,
        },
        n_results=5,
        priority=3,
        pipeline_level="fast",
        cluster_tag=None,
        use_entity_index=False,
    ),
}

INTENT_KEYWORDS = {
    QueryIntent.MATERIAL_WEAR: [
        "TC4",
        "Ti-6Al-4V",
        "钛合金",
        "titanium",
        "HRC52",
        "不锈钢加工磨损",
        "磨损特征",
        "NUAA",
        "PHM2010",
    ],
    QueryIntent.CUTTING_PARAMS: [
        "HRC52",
        "不锈钢",
        "切削参数",
        "切削速度",
        "进给量",
        "背吃刀量",
        "转速",
        "PHM2010",
        "参数建议",
        "推荐参数",
    ],
    QueryIntent.VIBRATION_WEAR: [
        "振动",
        "vibration",
        "RMS",
        "声发射",
        "acoustic",
        "信号",
        "频域",
        "频谱",
        "振动与磨损",
        "磨损关联",
        "监测",
    ],
    QueryIntent.MATERIAL_COMPARE: [
        "多材料",
        "对比",
        "比较",
        "TC4",
        "HRC52",
        "钛合金",
        "不锈钢",
        "工艺对比",
        "材料差异",
        "不同材料",
    ],
    QueryIntent.CROSS_SOURCE: [
        "Bosch",
        "Uniwear",
        "多源",
        "对比",
        "交叉验证",
        "联合",
        "标定",
        "两个数据集",
        "不同数据源",
    ],
    QueryIntent.SIGNAL_FUSION: [
        "信号样本",
        "vibration",
        "cutting_force",
        "acoustic_emission",
        "信号融合",
        "多模态",
        "峭度",
        "频谱",
        "RMS",
        "声发射",
        "振动信号",
        "切削力信号",
    ],
}


# cluster_tag ChromaDB where 过滤映射
# cluster_tag 是抽象聚类标签，需映射到实际文档元数据字段。
# 这样无需重新导入数据即可实现预分组过滤，缩小检索范围。
_CLUSTER_TAG_FILTERS: dict[str, dict] = {
    "material_wear": {"category": "tool_wear"},
    "cutting_params": {"category": "tool_wear"},
    "vibration_wear": {"has_vibration": True},
    "material_compare": {},  # 依赖 source_filters 即可
    "cross_source": {},  # 跨源检索不额外限制
    "signal_fusion": {},  # signal_fusion source 已通过 source_filters 过滤
}


# 查询实体提取（用于 entity 倒排索引扩展检索）
# 复用 document_importer 的正则模式，从查询中提取制造领域实体

_QUERY_ENTITY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bTC[0-9]{1,2}\b"),
    re.compile(r"\bTi-?\dAl-?\dV?\b", re.IGNORECASE),
    re.compile(r"\bHRC\s*\d{1,3}\b"),
    re.compile(r"\b\d{2,4}[钢]\b"),
    re.compile(r"\b(?:6061|7075|2024|AISI\s*\d{3,4})\b", re.IGNORECASE),
    re.compile(r"\b(?:钛合金|不锈钢|铝合金|硬质合金|高温合金)\b"),
    re.compile(r"\bW[1-9]\b"),
    re.compile(r"\bc[1-9]\b"),
    re.compile(r"\b(?:振动|vibration|RMS|声发射|acoustic)\b", re.IGNORECASE),
    re.compile(r"\b(?:切削力|cutting\s*force|主轴功率|spindle\s*power)\b", re.IGNORECASE),
    re.compile(r"\b(?:频域|频谱|frequency\s*domain)\b", re.IGNORECASE),
    re.compile(r"\bNUAA\b"),
    re.compile(r"\bPHM\s*2010\b", re.IGNORECASE),
    re.compile(r"\bUniwear\b", re.IGNORECASE),
    re.compile(r"\bBosch\b", re.IGNORECASE),
    re.compile(r"\b(?:切削速度|进给量|背吃刀量|切削深度|转速)\b"),
    re.compile(r"\b(?:cutting\s*speed|feed\s*rate|depth\s*of\s*cut)\b", re.IGNORECASE),
]


def _extract_query_entities(query: str) -> list[str]:
    """从查询文本中提取制造领域实体（小写形式）。

    用于在向量检索之外，通过 entity 倒排索引补充精确匹配的 chunk。
    """
    if not query or not query.strip():
        return []
    found: set[str] = set()
    for pattern in _QUERY_ENTITY_PATTERNS:
        for match in pattern.finditer(query):
            entity = match.group(0).strip().lower()
            if len(entity) >= 2:
                found.add(entity)
    return sorted(found)


# 检索结果 LRU 缓存


# 独立于 rag_retrieval.py 的模块常量（避免循环依赖：本模块被 rag_retrieval 先 import）
RESULT_CACHE_ENABLED = os.getenv("RESULT_CACHE_ENABLED", "1") == "1"


class _ResultCache:
    """线程安全的 LRU 检索结果缓存。

    缓存键：query + intent + n_results + source 的哈希。
    缓存值：retrieve() 返回的 dict。
    """

    # 独立于 rag_retrieval.py 的模块常量（避免循环依赖：本模块被 rag_retrieval 先 import，
    # 若引用其模块级 RESULT_CACHE_SIZE 会在类定义时 NameError——2026-08-03 安装验证发现）
    DEFAULT_MAX_SIZE = int(os.getenv("RESULT_CACHE_SIZE", "200"))

    def __init__(self, max_size: int = DEFAULT_MAX_SIZE):
        self._max_size = max_size
        self._cache: dict[str, dict] = {}
        self._keys: list[str] = []
        self._lock = threading.Lock()
        # 性能指标
        self._hits = 0
        self._misses = 0

    @staticmethod
    def _make_key(
        query: str,
        intent: QueryIntent | None,
        n_results: int,
        override_source: str | None,
    ) -> str:
        parts = [
            query.strip().lower(),
            intent.value if intent else "auto",
            str(n_results),
            override_source or "",
        ]
        return hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()

    def get(
        self,
        query: str,
        intent: QueryIntent | None,
        n_results: int,
        override_source: str | None,
    ) -> dict | None:
        if not RESULT_CACHE_ENABLED:
            return None
        key = self._make_key(query, intent, n_results, override_source)
        with self._lock:
            if key in self._cache:
                self._hits += 1
                # 移动到末尾表示最近使用
                self._keys.remove(key)
                self._keys.append(key)
                return self._cache[key]
            self._misses += 1
            return None

    def put(
        self,
        query: str,
        intent: QueryIntent | None,
        n_results: int,
        override_source: str | None,
        value: dict,
    ) -> None:
        if not RESULT_CACHE_ENABLED:
            return
        key = self._make_key(query, intent, n_results, override_source)
        with self._lock:
            if key in self._cache:
                # 已存在则更新值并移动到末尾
                self._keys.remove(key)
                self._cache[key] = value
                self._keys.append(key)
                return
            if len(self._keys) >= self._max_size:
                # 淘汰最久未使用
                oldest = self._keys.pop(0)
                self._cache.pop(oldest, None)
            self._cache[key] = value
            self._keys.append(key)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._keys.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._cache),
                "capacity": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
            }


# 主引擎
