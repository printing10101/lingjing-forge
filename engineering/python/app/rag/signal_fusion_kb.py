"""多源信号融合知识库。

落地竞品分析中识别的 MachineMetrics / 工业物联网平台补强点：
将振动、切削力、温度、声发射、电流等多源信号样本统一入库，
支持跨源检索、多模态融合、与刀具磨损 / 颤振预测模型的关联。

设计要点：
- 沿用项目既有 ChromaDB 单一集合 ``knowledge_base`` + ``source`` metadata 约定，
  本模块所有样本 ``source="signal_fusion"``，避免破坏现有 RAG 检索路径。
- ``sensor_features`` schema 与 ``ToolWearPredictor.calibrate_with_real_time_data``
  完全对齐：vibration_rms / cutting_force / temperature / acoustic_emission。
- 9 维特征规范复用 ``app.ai.lnn.training.dataset.FeatureExtractor``，
  保证与 LNN/LTC 训练输入空间一致。
- 融合算法复用 ``app.data.pipeline.fusion`` 中的 MultiModalFusion /
  CrossModalAttentionFusion，避免重复实现。
- 不依赖 torch；ChromaDB / sentence-transformers / numpy 为现有依赖。

典型用法（API 层调用）::

    from app.rag.signal_fusion_kb import get_signal_fusion_kb

    kb = get_signal_fusion_kb()
    sample = SignalSample(
        signal_type="vibration",
        source="bosch_cnc",
        features=[0.12, 0.45, 0.88, 1.15, 1.42, 3.01, 250.0, 248.5, 0.92],
        sensor_features={"vibration_rms": 1.2},
        process_context={"spindle_rpm": 8000, "feed_rate": 1200},
        machine_id="vmc_850",
        tool_id=3,
        material="aluminum_6061",
    )
    sample_id = kb.register_sample(sample)
    hits = kb.retrieve_similar(sample.features, signal_type="vibration", top_k=5)
    fused = kb.fuse_signals({s.signal_type: s.features for s in hits})
    wear_input = kb.correlate_with_wear(hits)
    # wear_input 可直接传给 ToolWearPredictor.calibrate_with_real_time_data
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# 常量

SIGNAL_FUSION_SOURCE = "signal_fusion"

# 支持的信号类型（与 ToolWearPredictor sensor_features 对齐 + 扩展）
SUPPORTED_SIGNAL_TYPES = (
    "vibration",  # 振动 → vibration_rms
    "cutting_force",  # 切削力 → cutting_force
    "temperature",  # 温度 → temperature
    "acoustic_emission",  # 声发射 → acoustic_emission
    "current",  # 电流（扩展，无对应 sensor_feature 字段）
)

# 信号类型 sensor_features 字段映射
SIGNAL_TYPE_TO_SENSOR_FIELD: dict[str, str | None] = {
    "vibration": "vibration_rms",
    "cutting_force": "cutting_force",
    "temperature": "temperature",
    "acoustic_emission": "acoustic_emission",
    "current": None,  # 电流暂无对应字段，仅入库特征
}

# 9 维特征名称（与 FeatureExtractor 对齐）
FEATURE_NAMES: tuple[str, ...] = (
    "rms",
    "peak",
    "peak_to_peak",
    "shape_factor",
    "impulse_factor",
    "kurtosis",
    "dominant_freq",
    "spectral_centroid",
    "spectral_energy",
)


# 数据类


@dataclass
class SignalSample:
    """多源信号样本。

    Attributes:
        signal_type: 信号类型（见 SUPPORTED_SIGNAL_TYPES）
        source: 数据源标识（bosch_cnc / uniwear / custom 等）
        features: 9 维特征向量（与 FeatureExtractor 对齐）
        sensor_features: 与 ToolWearPredictor 对齐的传感器读数字典
        process_context: 工艺上下文（spindle_rpm / feed_rate / depth_of_cut 等）
        machine_id: 机床 ID
        tool_id: 刀具 ID
        material: 工件材料
        label: 可选标签（如 stable / chatter / tool_wear_level）
        timestamp: 时间戳（Unix 秒），None 则自动填充
        sample_id: 样本 ID，None 则自动生成
        metadata: 额外元数据
    """

    signal_type: str
    source: str
    features: list[float]
    sensor_features: dict[str, float] = field(default_factory=dict)
    process_context: dict[str, Any] = field(default_factory=dict)
    machine_id: str = ""
    tool_id: int | None = None
    material: str = ""
    label: str = ""
    timestamp: float = field(default_factory=time.time)
    sample_id: str = field(default_factory=lambda: f"sig_{uuid.uuid4().hex[:12]}")
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.signal_type not in SUPPORTED_SIGNAL_TYPES:
            logger.warning(
                "信号类型 %r 不在标准列表 %s 中，仍允许入库但检索可能无法按类型过滤",
                self.signal_type,
                SUPPORTED_SIGNAL_TYPES,
            )
        if len(self.features) != len(FEATURE_NAMES):
            logger.warning(
                "特征维度 %d 与标准 9 维不一致（signal_type=%s）",
                len(self.features),
                self.signal_type,
            )

    def to_document_text(self) -> str:
        """序列化为可嵌入的文档文本（同时保留数值精度用于检索）。"""
        parts = [
            f"[SignalSample:{self.signal_type}]",
            f"source={self.source}",
            f"machine={self.machine_id}",
            f"tool={self.tool_id}",
            f"material={self.material}",
            f"label={self.label}",
        ]
        # 9 维特征名值对（便于 BM25 / 向量检索同时匹配）
        for name, val in zip(FEATURE_NAMES, self.features):
            parts.append(f"{name}={val:.6f}")
        # 传感器读数
        for k, v in self.sensor_features.items():
            parts.append(f"{k}={v:.6f}")
        # 工艺上下文
        for k, v in self.process_context.items():
            parts.append(f"ctx.{k}={v}")
        return " ".join(parts)

    def to_metadata(self) -> dict[str, Any]:
        """转为 ChromaDB metadata（仅含可过滤的标量字段）。"""
        md: dict[str, Any] = {
            "source": SIGNAL_FUSION_SOURCE,
            "signal_type": self.signal_type,
            "data_source": self.source,
            "machine_id": self.machine_id,
            "material": self.material,
            "label": self.label,
            "timestamp": float(self.timestamp),
            "sample_id": self.sample_id,
        }
        if self.tool_id is not None:
            md["tool_id"] = int(self.tool_id)
        # 9 维特征以 JSON 字符串存储（ChromaDB metadata 仅支持标量）
        md["features_json"] = json.dumps([float(x) for x in self.features])
        md["sensor_features_json"] = json.dumps(self.sensor_features)
        md["process_context_json"] = json.dumps(self.process_context)
        if self.metadata:
            md["extra_json"] = json.dumps(self.metadata)
        return md

    @classmethod
    def from_metadata(
        cls,
        document: str,
        metadata: dict[str, Any],
    ) -> "SignalSample":
        """从 ChromaDB 文档与 metadata 重建 SignalSample。"""
        features = json.loads(metadata.get("features_json", "[]"))
        sensor_features = json.loads(metadata.get("sensor_features_json", "{}"))
        process_context = json.loads(metadata.get("process_context_json", "{}"))
        extra = json.loads(metadata.get("extra_json", "{}")) if metadata.get("extra_json") else {}
        return cls(
            signal_type=metadata.get("signal_type", "unknown"),
            source=metadata.get("data_source", "unknown"),
            features=[float(x) for x in features],
            sensor_features={k: float(v) for k, v in sensor_features.items()},
            process_context=process_context,
            machine_id=metadata.get("machine_id", ""),
            tool_id=metadata.get("tool_id"),
            material=metadata.get("material", ""),
            label=metadata.get("label", ""),
            timestamp=float(metadata.get("timestamp", 0.0)),
            sample_id=metadata.get("sample_id", ""),
            metadata=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FusionResult:
    """多源信号融合结果。"""

    fused_vector: list[float]
    strategy: str  # "weighted" | "attention"
    modality_weights: dict[str, float]
    input_sample_ids: list[str]
    dimension: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WearCorrelation:
    """信号样本与刀具磨损的关联结果。"""

    sensor_features: dict[str, float]
    source_sample_ids: list[str]
    source_count: int
    confidence: float
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChatterCorrelation:
    """信号样本与颤振稳定性的关联结果。"""

    chatter_features: dict[str, float]
    source_sample_ids: list[str]
    source_count: int
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# 知识库主类


class SignalFusionKnowledgeBase:
    """多源信号融合知识库。

    使用 ChromaDB 单一集合存储信号样本（source=signal_fusion），
    支持：
    - 按信号类型 / 机床 / 材料 / 刀具过滤检索
    - 跨信号类型的多模态融合（加权 / 注意力）
    - 与 ToolWearPredictor / ChatterPredictor 的关联接口
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._vector_store = None  # lazy
        self._embedding_service = None  # lazy
        self._weighted_fusion: Any = None  # lazy
        self._attention_fusion: Any = None  # lazy

    # 懒加载依赖（避免在 import 时触发 ChromaDB / 模型加载）

    def _get_vector_store(self):
        if self._vector_store is None:
            from app.dependencies import get_vector_store

            self._vector_store = get_vector_store()
        return self._vector_store

    def _get_embedding_service(self):
        if self._embedding_service is None:
            from app.dependencies import get_embedding_service

            self._embedding_service = get_embedding_service()
        return self._embedding_service

    def _get_weighted_fusion(self):
        if self._weighted_fusion is None:
            from app.data.pipeline.config import FusionConfig
            from app.data.pipeline.fusion import MultiModalFusion

            cfg = FusionConfig(
                modality_weights={st: 1.0 for st in SUPPORTED_SIGNAL_TYPES},
                target_dim=9,
            )
            self._weighted_fusion = MultiModalFusion(cfg)
        return self._weighted_fusion

    def _get_attention_fusion(self):
        if self._attention_fusion is None:
            from app.data.pipeline.config import FusionConfig
            from app.data.pipeline.fusion import CrossModalAttentionFusion

            cfg = FusionConfig(
                modality_weights={st: 1.0 for st in SUPPORTED_SIGNAL_TYPES},
                target_dim=9,
                attention_heads=3,
                dropout=0.0,
            )
            self._attention_fusion = CrossModalAttentionFusion(cfg)
        return self._attention_fusion

    # 写入

    def register_sample(self, sample: SignalSample) -> str:
        """注册一个信号样本到知识库。

        Args:
            sample: 信号样本

        Returns:
            样本 ID
        """
        if not sample.features:
            raise ValueError("features 不能为空")

        vs = self._get_vector_store()
        es = self._get_embedding_service()

        document = sample.to_document_text()
        metadata = sample.to_metadata()
        # 用文档文本生成嵌入向量（BM25 + 向量双重匹配）
        embedding = es.embed(document)

        with self._lock:
            vs.add(
                ids=[sample.sample_id],
                documents=[document],
                embeddings=[embedding],
                metadatas=[metadata],
            )
        logger.info(
            "注册信号样本: id=%s type=%s source=%s machine=%s",
            sample.sample_id,
            sample.signal_type,
            sample.source,
            sample.machine_id,
        )
        return sample.sample_id

    def register_samples_batch(self, samples: list[SignalSample]) -> list[str]:
        """批量注册信号样本。"""
        if not samples:
            return []
        vs = self._get_vector_store()
        es = self._get_embedding_service()

        ids = [s.sample_id for s in samples]
        documents = [s.to_document_text() for s in samples]
        metadatas = [s.to_metadata() for s in samples]
        embeddings = es.embed_batch(documents)

        with self._lock:
            vs.add(
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
            )
        logger.info("批量注册 %d 个信号样本", len(samples))
        return ids

    # 检索

    def retrieve_similar(
        self,
        features: list[float],
        signal_type: str | None = None,
        machine_id: str | None = None,
        material: str | None = None,
        tool_id: int | None = None,
        top_k: int = 10,
    ) -> list[SignalSample]:
        """检索与给定特征向量相似的信号样本。

        Args:
            features: 9 维查询特征向量
            signal_type: 可选信号类型过滤
            machine_id: 可选机床 ID 过滤
            material: 可选材料过滤
            tool_id: 可选刀具 ID 过滤
            top_k: 返回前 K 个

        Returns:
            SignalSample 列表（按相似度降序）
        """
        vs = self._get_vector_store()
        es = self._get_embedding_service()

        # 用特征向量构造查询文本（与样本入库时的文档格式一致）
        query_text = " ".join(f"{name}={val:.6f}" for name, val in zip(FEATURE_NAMES, features))
        query_embedding = es.embed(query_text)

        where: dict[str, Any] = {"source": SIGNAL_FUSION_SOURCE}
        if signal_type:
            where["signal_type"] = signal_type
        if machine_id:
            where["machine_id"] = machine_id
        if material:
            where["material"] = material
        if tool_id is not None:
            where["tool_id"] = int(tool_id)

        result = vs.query(
            query_embedding=query_embedding,
            n_results=max(1, min(top_k, 100)),
            where=where,
        )

        samples: list[SignalSample] = []
        ids = result.get("ids", [[]])
        documents = result.get("documents", [[]])
        metadatas = result.get("metadatas", [[]])
        if ids and ids[0]:
            for doc, md in zip(documents[0], metadatas[0]):
                try:
                    samples.append(SignalSample.from_metadata(doc, md))
                except Exception as e:
                    logger.warning("反序列化样本失败: %s", e)
        return samples

    def retrieve_by_signal_type(
        self,
        signal_type: str,
        limit: int = 50,
    ) -> list[SignalSample]:
        """按信号类型列出样本（无相似度排序）。"""
        vs = self._get_vector_store()
        result = vs.get(
            where={"source": SIGNAL_FUSION_SOURCE, "signal_type": signal_type},
            limit=max(1, min(limit, 500)),
        )
        samples: list[SignalSample] = []
        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])
        for doc, md in zip(documents, metadatas):
            try:
                samples.append(SignalSample.from_metadata(doc, md))
            except Exception as e:
                logger.warning("反序列化样本失败: %s", e)
        return samples

    def list_samples(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SignalSample]:
        """列出所有信号融合样本（分页）。"""
        vs = self._get_vector_store()
        result = vs.get(
            where={"source": SIGNAL_FUSION_SOURCE},
            limit=max(1, min(limit, 500)),
        )
        samples: list[SignalSample] = []
        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])
        for doc, md in zip(documents, metadatas):
            try:
                samples.append(SignalSample.from_metadata(doc, md))
            except Exception as e:
                logger.warning("反序列化样本失败: %s", e)
        return samples[offset : offset + limit]

    # 融合

    def fuse_signals(
        self,
        samples: list[SignalSample],
        strategy: str = "weighted",
        weights: dict[str, float] | None = None,
    ) -> FusionResult:
        """将多个信号样本融合为统一特征向量。

        Args:
            samples: 信号样本列表（建议来自不同 signal_type）
            strategy: 融合策略 "weighted" 或 "attention"。
                attention 依赖未经训练的随机权重，默认拒绝（学术诚信守卫），
                需设置环境变量 ``LNN_SIGNAL_FUSION_ALLOW_UNTRAINED=1`` 方可使用
            weights: 可选自定义权重（仅 weighted 策略）

        Returns:
            FusionResult
        """
        if not samples:
            raise ValueError("samples 不能为空")

        # 按 signal_type 分组（同类型取均值，避免重复样本偏置）
        grouped: dict[str, list[np.ndarray]] = {}
        for s in samples:
            arr = np.asarray(s.features, dtype=np.float32)
            grouped.setdefault(s.signal_type, []).append(arr)

        features_dict: dict[str, np.ndarray] = {}
        for st, arrs in grouped.items():
            stacked = np.stack(arrs)
            features_dict[st] = stacked.mean(axis=0)

        if strategy == "attention":
            # [学术诚信守卫] CrossModalAttentionFusion 使用随机初始化权重
            # （未经训练，模块内自带 runtime warning），融合结果不具备物理
            # 意义。默认拒绝在知识库生产端点使用；仅显式设置环境变量后方可
            # 用于开发调试。
            if os.environ.get("LNN_SIGNAL_FUSION_ALLOW_UNTRAINED", "").strip().lower() not in ("1", "true", "yes"):
                raise ValueError(
                    "[学术诚信] attention 融合策略当前使用未经训练的随机权重"
                    "（CrossModalAttentionFusion），融合结果不具备物理意义，默认禁止。"
                    "建议操作：改用 strategy='weighted'（确定性加权平均，结果可信）；"
                    "或仅在开发调试时设置环境变量 LNN_SIGNAL_FUSION_ALLOW_UNTRAINED=1。"
                )
            fusion = self._get_attention_fusion()
            fused = fusion.fuse(features_dict)
            # 注意力融合的权重提取（取均值作为各模态贡献度）
            attn_weights = fusion.get_attention_weights(features_dict)
            modality_weights = {m: float(np.mean(w)) for m, w in attn_weights.items()}
        else:
            fusion = self._get_weighted_fusion()
            if weights:
                fusion.set_weights(weights)
            fused = fusion.fuse(features_dict)
            # 加权融合的权重取实际使用值
            total = sum(fusion.weights.values()) or 1.0
            modality_weights = {m: float(fusion.weights.get(m, 0.0)) / total for m in features_dict}

        return FusionResult(
            fused_vector=[float(x) for x in np.asarray(fused).flatten()],
            strategy=strategy,
            modality_weights=modality_weights,
            input_sample_ids=[s.sample_id for s in samples],
            dimension=len(fused),
        )

    # 关联：磨损

    def correlate_with_wear(
        self,
        samples: list[SignalSample],
    ) -> WearCorrelation:
        """将信号样本关联为 ToolWearPredictor 可消费的 sensor_features。

        ToolWearPredictor.calibrate_with_real_time_data 期望 sensor_features 含：
        vibration_rms / cutting_force / temperature / acoustic_emission
        （单位与阈值见 tool_wear_predictor.py）。

        Args:
            samples: 信号样本列表（建议来自不同 signal_type）

        Returns:
            WearCorrelation
        """
        if not samples:
            raise ValueError("samples 不能为空")

        # 按 signal_type 聚合（同类型取最大值，因为磨损加速由峰值驱动）
        aggregated: dict[str, list[float]] = {}
        for s in samples:
            field = SIGNAL_TYPE_TO_SENSOR_FIELD.get(s.signal_type)
            if field is None:
                continue
            # 优先使用 sensor_features 中的读数，否则用 9 维特征中的 RMS（index 0）
            if field in s.sensor_features:
                aggregated.setdefault(field, []).append(float(s.sensor_features[field]))
            elif s.features:
                aggregated.setdefault(field, []).append(float(s.features[0]))

        sensor_features: dict[str, float] = {}
        notes: list[str] = []
        for field, vals in aggregated.items():
            sensor_features[field] = max(vals) if vals else 0.0
            notes.append(f"{field}: max({len(vals)})={sensor_features[field]:.4f}")

        # 缺失字段补 0 并提示
        for required in ("vibration_rms", "cutting_force", "temperature", "acoustic_emission"):
            if required not in sensor_features:
                sensor_features[required] = 0.0
                notes.append(f"警告: 缺失 {required}，已补 0")

        # 置信度：覆盖字段比例
        coverage = len(aggregated) / 4.0
        confidence = round(min(1.0, coverage), 3)

        return WearCorrelation(
            sensor_features=sensor_features,
            source_sample_ids=[s.sample_id for s in samples],
            source_count=len(samples),
            confidence=confidence,
            notes=notes,
        )

    # 关联：颤振

    def correlate_with_chatter(
        self,
        samples: list[SignalSample],
        process_context: dict[str, Any] | None = None,
    ) -> ChatterCorrelation:
        """将信号样本关联为 ChatterPredictor 可消费的特征。

        ChatterPredictor.predict 期望 6 维输入：
        spindle_rpm / machine_stiffness / machine_damping / machine_freq /
        tool_diameter / tool_k_s

        Args:
            samples: 信号样本列表
            process_context: 可选工艺上下文覆盖（优先级高于样本内的 process_context）

        Returns:
            ChatterCorrelation
        """
        if not samples:
            raise ValueError("samples 不能为空")

        merged_ctx: dict[str, Any] = {}
        for s in samples:
            merged_ctx.update(s.process_context)
        if process_context:
            merged_ctx.update(process_context)

        # 从振动样本提取 dominant_freq（features[6]）作为机床固有频率估计
        freq_candidates = [
            float(s.features[6]) for s in samples if s.signal_type == "vibration" and len(s.features) > 6
        ]
        machine_freq = max(freq_candidates) if freq_candidates else 0.0

        chatter_features: dict[str, float] = {
            "spindle_rpm": float(merged_ctx.get("spindle_rpm", 0.0)),
            "machine_stiffness": float(merged_ctx.get("machine_stiffness", 0.0)),
            "machine_damping": float(merged_ctx.get("machine_damping", 0.0)),
            "machine_freq": machine_freq,
            "tool_diameter": float(merged_ctx.get("tool_diameter", 0.0)),
            "tool_k_s": float(merged_ctx.get("tool_k_s", 0.0)),
        }

        notes: list[str] = []
        if machine_freq <= 0:
            notes.append("警告: 未从振动样本提取到 dominant_freq，machine_freq=0")
        for k, v in chatter_features.items():
            if v <= 0 and k != "machine_damping":
                notes.append(f"警告: {k}={v}，建议在 process_context 中提供")

        return ChatterCorrelation(
            chatter_features=chatter_features,
            source_sample_ids=[s.sample_id for s in samples],
            source_count=len(samples),
            notes=notes,
        )

    # 统计 / 删除

    def stats(self) -> dict[str, Any]:
        """返回知识库统计信息。

        计数准确性说明：
            ``VectorStore.count()`` 返回整个 collection 的文档数（含其他 source），
            不能直接用于 signal_fusion 子集统计。本方法通过 ``get(where=source)``
            一次性拉取所有 signal_fusion 样本的 metadata，在内存中按 signal_type
            分组统计，保证 ``total_signal_samples`` 与 ``type_counts`` 严格一致。

            单次查询开销：O(N)，N 为 signal_fusion 样本总数（上限 10000，
            超出时 ``truncated=True`` 标记，提示需要分批统计）。
        """
        vs = self._get_vector_store()

        # 一次性拉取所有 signal_fusion 样本（仅 ids + metadatas）
        # limit=10000：覆盖典型工业场景（单机床年采集量 < 10k）；
        # 超出时 truncated 标记为 True，调用方可据此判断是否需要分批统计
        STATS_FETCH_LIMIT = 10000
        try:
            result = vs.get(
                where={"source": SIGNAL_FUSION_SOURCE},
                limit=STATS_FETCH_LIMIT,
            )
        except (RuntimeError, OSError, ValueError) as e:
            logger.warning("统计 signal_fusion 样本失败: %s", e, exc_info=True)
            return {
                "total_signal_samples": -1,
                "supported_signal_types": list(SUPPORTED_SIGNAL_TYPES),
                "type_counts": {},
                "truncated": False,
                "error": "信号融合样本统计失败，请稍后重试",
                "feature_dimension": len(FEATURE_NAMES),
                "feature_names": list(FEATURE_NAMES),
                "source_tag": SIGNAL_FUSION_SOURCE,
            }

        all_ids = result.get("ids", [])
        all_metas = result.get("metadatas", [])
        total = len(all_ids)
        truncated = total >= STATS_FETCH_LIMIT

        # 按 signal_type 精确分组计数（内存遍历，无额外 IO）
        type_counts: dict[str, int] = {st: 0 for st in SUPPORTED_SIGNAL_TYPES}
        unknown_types: dict[str, int] = {}
        for md in all_metas:
            st = md.get("signal_type", "unknown") if isinstance(md, dict) else "unknown"
            if st in type_counts:
                type_counts[st] += 1
            else:
                unknown_types[st] = unknown_types.get(st, 0) + 1

        # 合并未知类型到统计（便于发现数据质量问题）
        type_counts_out = dict(type_counts)
        for st, cnt in unknown_types.items():
            type_counts_out[st] = cnt

        return {
            "total_signal_samples": total,
            "supported_signal_types": list(SUPPORTED_SIGNAL_TYPES),
            "type_counts": type_counts_out,
            "nonzero_signal_types": {st: cnt for st, cnt in type_counts_out.items() if cnt > 0},
            "truncated": truncated,
            "fetch_limit": STATS_FETCH_LIMIT,
            "feature_dimension": len(FEATURE_NAMES),
            "feature_names": list(FEATURE_NAMES),
            "source_tag": SIGNAL_FUSION_SOURCE,
        }

    def get_samples_by_ids(
        self,
        sample_ids: list[str],
    ) -> list[SignalSample]:
        """按样本 ID 精确反查（用于 RAG 集成点 2 的 chunk_ids → 完整文档拉取）。

        软依赖设计：
            - 单个 ID 反序列化失败时记录 warning，跳过该样本，不阻断批量返回；
            - 整体查询失败时返回空列表，由调用方决定降级策略；
            - ID 列表为空时立即返回空列表，不触发 IO。

        Args:
            sample_ids: 样本 ID 列表

        Returns:
            SignalSample 列表（顺序与输入一致，缺失或失败的 ID 被跳过）
        """
        if not sample_ids:
            return []

        vs = self._get_vector_store()
        try:
            # ChromaDB get 按 ids 过滤，limit 设为 ids 长度（理论全命中）
            result = vs.get(ids=list(sample_ids), limit=len(sample_ids))
        except (RuntimeError, OSError, ValueError) as e:
            logger.warning(
                "get_samples_by_ids 查询失败 (ids=%s): %s",
                sample_ids[:5],
                e,
                exc_info=True,
            )
            return []

        ids = result.get("ids", [])
        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])

        # 构建 id (doc, meta) 映射，保证输出顺序与输入一致
        id_to_doc_meta: dict[str, tuple[str, dict]] = {}
        for i, cid in enumerate(ids):
            doc = documents[i] if i < len(documents) else ""
            md = metadatas[i] if i < len(metadatas) else {}
            id_to_doc_meta[cid] = (doc, md)

        samples: list[SignalSample] = []
        missing_ids: list[str] = []
        for sid in sample_ids:
            pair = id_to_doc_meta.get(sid)
            if pair is None:
                missing_ids.append(sid)
                continue
            doc, md = pair
            try:
                samples.append(SignalSample.from_metadata(doc, md))
            except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
                logger.warning(
                    "反序列化样本失败 (sample_id=%s): %s",
                    sid,
                    e,
                    exc_info=True,
                )

        if missing_ids:
            logger.debug(
                "get_samples_by_ids: %d/%d IDs 未命中 (前 5: %s)",
                len(missing_ids),
                len(sample_ids),
                missing_ids[:5],
            )

        return samples

    def delete_sample(self, sample_id: str) -> int:
        """按样本 ID 删除。"""
        vs = self._get_vector_store()
        with self._lock:
            return vs.delete(ids=[sample_id])

    def delete_by_signal_type(self, signal_type: str) -> int:
        """按信号类型批量删除。"""
        vs = self._get_vector_store()
        with self._lock:
            return vs.delete(
                where={
                    "source": SIGNAL_FUSION_SOURCE,
                    "signal_type": signal_type,
                }
            )


# 单例


class _SingletonHolder:
    """线程安全的单例持有者。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: SignalFusionKnowledgeBase | None = None

    def get(self) -> SignalFusionKnowledgeBase:
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = SignalFusionKnowledgeBase()
            return self._instance


_holder = _SingletonHolder()


def get_signal_fusion_kb() -> SignalFusionKnowledgeBase:
    """获取多源信号融合知识库单例。"""
    return _holder.get()


__all__ = [
    "SIGNAL_FUSION_SOURCE",
    "SUPPORTED_SIGNAL_TYPES",
    "SIGNAL_TYPE_TO_SENSOR_FIELD",
    "FEATURE_NAMES",
    "SignalSample",
    "FusionResult",
    "WearCorrelation",
    "ChatterCorrelation",
    "SignalFusionKnowledgeBase",
    "get_signal_fusion_kb",
]
