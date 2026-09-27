"""共享中文分词器（reranker / hybrid_search 共用）。

优先使用 jieba 进行中文分词，获得比字符级切分更精准的语义单元；
jieba 不可用时自动降级为字符级 + 英文空格切分，保证可用性。

设计目标：
- 统一 RAG 各模块的分词行为，避免 reranker 与 hybrid_search 使用不同分词器
  导致 BM25 与 reranker 评分不一致
- 懒加载 jieba（首次调用才初始化词典），避免导入时阻塞
- 线程安全的单例模式
- 支持制造领域自定义词典扩展（钛合金牌号、刀具型号、工艺术语）
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable

logger = logging.getLogger(__name__)

# 制造领域专用词典：提升关键术语的分词准确率
# 这些词在通用 jieba 词典中可能被错误切分（如 "TC4" 可能被切成 "T" "C" "4"）
DOMAIN_LEXICON: tuple[str, ...] = (
    # 钛合金牌号
    "TC4",
    "Ti-6Al-4V",
    "TA1",
    "TA2",
    "TB6",
    # 不锈钢牌号
    "304不锈钢",
    "316不锈钢",
    "17-4PH",
    "2205双相",
    "904L",
    "HRC52",
    "HRC45",
    "HRC60",
    # 铝合金牌号
    "6061",
    "6061-T6",
    "7075",
    "2024",
    # 刀具材料
    "CBN",
    "PCD",
    "硬质合金",
    "陶瓷刀具",
    "涂层刀具",
    # 工艺术语
    "切削速度",
    "进给量",
    "切削深度",
    "背吃刀量",
    "主轴转速",
    "表面粗糙度",
    "刀具磨损",
    "后刀面磨损",
    "颤振",
    "振动",
    "声发射",
    "频域",
    "频谱",
    # 数据集名称
    "PHM2010",
    "Uniwear",
    "Bosch",
    # 加工方式
    "高速铣削",
    "五轴铣削",
    "深孔钻孔",
    "无心磨削",
    "内圆磨削",
    "电火花线切割",
    "成形电火花",
    # 测量信号
    "RMS",
    "FFT",
    "AE信号",
)

# 单例状态
_jieba_available: bool | None = None
_jieba_lock = threading.Lock()
_lexicon_loaded = False


def _ensure_jieba() -> bool:
    """懒加载 jieba 并注册领域词典。

    Returns:
        True 表示 jieba 可用；False 表示降级到字符级分词
    """
    global _jieba_available, _lexicon_loaded

    if _jieba_available is not None:
        return _jieba_available

    with _jieba_lock:
        if _jieba_available is not None:
            return _jieba_available

        try:
            import jieba

            # 静默加载，避免 jieba 默认的初始化日志污染
            jieba.setLogLevel(20)  # WARNING+
            # 触发词典加载
            jieba.initialize()

            # 注册领域词典（提升制造术语切分准确率）
            for word in DOMAIN_LEXICON:
                jieba.add_word(word, freq=1000)

            _jieba_available = True
            _lexicon_loaded = True
            logger.info(
                "jieba tokenizer initialized with %d domain terms",
                len(DOMAIN_LEXICON),
            )
        except ImportError:
            _jieba_available = False
            logger.warning(
                "jieba not installed, falling back to character-level tokenization. Install with: pip install jieba"
            )
        except (RuntimeError, OSError) as e:
            _jieba_available = False
            logger.warning("jieba initialization failed (%s), using character-level fallback", e)

    return _jieba_available


def tokenize(text: str) -> list[str]:
    """中文分词（reranker / hybrid_search 共用）。

    Args:
        text: 待分词文本

    Returns:
        token 列表（小写、去空白）；
        jieba 可用时返回词级别分词；
        不可用时降级为字符级 + 英文空格分词
    """
    if not text:
        return []

    if _ensure_jieba():
        try:
            import jieba

            # cut 返回生成器；lazily 使用 jieba.cut 精确模式
            tokens = [tok.strip().lower() for tok in jieba.cut(text, cut_all=False) if tok and tok.strip()]
            return tokens
        except (RuntimeError, OSError, ValueError) as e:
            logger.debug("jieba cut failed, using fallback: %s", e)

    # Fallback：字符级 + 英文空格切分
    return _char_level_tokenize(text)


def _char_level_tokenize(text: str) -> list[str]:
    """字符级分词（jieba 不可用时的 fallback）。"""
    tokens: list[str] = []
    for word in text.lower().split():
        if any("\u4e00" <= ch <= "\u9fff" for ch in word):
            tokens.extend(ch for ch in word if "\u4e00" <= ch <= "\u9fff")
        else:
            tokens.append(word)
    return tokens


def tokenize_batch(texts: Iterable[str]) -> list[list[str]]:
    """批量分词。

    Args:
        texts: 文本列表

    Returns:
        每个文本对应的 token 列表
    """
    return [tokenize(t) for t in texts]


def is_jieba_active() -> bool:
    """检查 jieba 是否可用（用于诊断端点）。"""
    return _ensure_jieba()


def get_tokenizer_info() -> dict:
    """获取分词器状态信息（用于诊断端点）。"""
    return {
        "jieba_available": _ensure_jieba(),
        "lexicon_size": len(DOMAIN_LEXICON) if _lexicon_loaded else 0,
        "fallback_mode": "character_level" if not _ensure_jieba() else "jieba",
    }


__all__ = [
    "tokenize",
    "tokenize_batch",
    "is_jieba_active",
    "get_tokenizer_info",
    "DOMAIN_LEXICON",
]
