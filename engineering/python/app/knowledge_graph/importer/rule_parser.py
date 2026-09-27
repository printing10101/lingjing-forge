"""知识图谱规则解析模块（M1.3）

职责
----
将 ``process_rules.json`` 中以 ``id / name / category / description / details``
形式表达的工艺规则拆解为"条件（IF）"与"动作（THEN）"两部分，并转换为：

- 1 个 ``Process`` 节点（rule 本体）；
- 1-2 个 ``Feature`` 节点（rule 涉及到的几何特征）；
- 1-2 条 ``(Process) -[APPLIED_TO]-> (Feature)`` 关系。

实现说明
---------
- ``process_rules.json`` 当前的 schema 中，``IF-THEN`` 语义是隐式的，
  需要从 ``name`` / ``description`` 文本中通过关键词抽取得到。
- 关键词词典针对加工领域的常见几何特征：
    * 平面 / 面 / 基准面 → "面 / 平面 / 基准面" feature
    * 孔                  → "孔 / 钻孔"      feature
    * 型腔                → "型腔 / 腔"      feature
    * 轮廓                → "轮廓 / 外形"    feature
    * 槽                  → "槽 / 凹槽"      feature
    * 螺纹                → "螺纹"           feature
- 解析得到的所有 feature 共享同一份 ``Feature`` 字典（基于 name 去重），
  便于后续在不同规则之间共享相同 feature 节点。

设计原则
--------
- **极简**：仅完成"规则 → 关系"映射，不做语义推理或冲突处理。
- **不依赖通用解析器**：所有解析针对 ``process_rules.json`` 的固定 schema
  定制。
- **可测试**：``RuleParser`` 为纯类，可注入关键词词典便于测试。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Iterable

logger = logging.getLogger(__name__)


# 数据类


@dataclass
class ParsedFeature:
    """从规则中抽取出来的 Feature 实体（仅保留必要字段）。"""

    feature_id: str
    name: str
    feature_type: str = ""


@dataclass
class ParsedRule:
    """单条规则的解析结果。

    Attributes:
        process_id: 对应 ``process_rules.json`` 中的 ``id``，用作 Process
            节点的 ``node_id``（``process-<id>`` 前缀由调用方添加）。
        process_name: 规则名称。
        process_category: 规则类别（sequence / parameter / fixture）。
        process_description: 规则描述。
        process_details: 规则细节参数（透传原 JSON 字段）。
        features: 从规则中抽取出的 Feature 列表。
        confidence: 关系可信度。规则类关系默认 0.9。
        source: 关系来源标识，固定为 ``"rule"``。
    """

    process_id: str
    process_name: str
    process_category: str = ""
    process_description: str = ""
    process_details: dict[str, Any] = field(default_factory=dict)
    features: list[ParsedFeature] = field(default_factory=list)
    confidence: float = 0.9
    source: str = "rule"

    def has_features(self) -> bool:
        """是否抽取到至少一个 Feature。"""
        return len(self.features) > 0


# 关键词词典（加工领域 Feature 名称 Feature 类型）


# 格式：(正则 pattern, feature_name, feature_type)
# 优先级：靠前的先匹配，后续同名词不重复添加。
_DEFAULT_KEYWORDS: list[tuple[str, str, str]] = [
    (r"基准面", "基准面", "datum"),
    (r"定位平面|定位面|定位基准", "定位平面", "face"),
    (r"平面|表面", "面", "face"),
    (r"钻孔|孔加工|孔", "孔", "hole"),
    (r"型腔|腔体|腔", "型腔", "pocket"),
    (r"轮廓|外形|外形面", "轮廓", "contour"),
    (r"凹槽|键槽|槽", "槽", "slot"),
    (r"螺纹", "螺纹", "thread"),
]


# RuleParser


class RuleParser:
    """``process_rules.json`` 规则解析器。

    使用示例::

        parser = RuleParser()
        rules = parser.parse_rules_file(Path("process_rules.json"))
        for rule in rules:
            # rule.process_name -> [f.name for f in rule.features]
    """

    def __init__(
        self,
        keywords: list[tuple[str, str, str]] | None = None,
        default_confidence: float = 0.9,
    ) -> None:
        self._keywords = keywords if keywords is not None else _DEFAULT_KEYWORDS
        self._default_confidence = float(default_confidence)
        # 跨规则共享：feature_name -> feature_id（去重辅助）
        self._shared_features: dict[str, str] = {}

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _slugify(text: str) -> str:
        """将任意字符串规整为 ``<type>-<slug>`` 中可用的 slug。

        仅保留 ASCII 字母 / 数字 / 下划线 / 横线 / 点号，剔除其它字符，
        并将中文等非 ASCII 转写为 ``_zh_XXXX`` 形式以保证唯一性。
        为简化实现，对中文字符直接转写为基于其 ``ord`` 哈希的稳定 hex。
        """
        if not text:
            return "x"
        buf: list[str] = []
        for ch in text:
            if ch.isascii() and (ch.isalnum() or ch in "_-."):
                buf.append(ch)
            else:
                # 中文字符用 ord 哈希保证可逆+稳定
                buf.append(f"u{ord(ch):x}")
        slug = "".join(buf).strip("-_.")
        if not slug:
            slug = "x"
        # 限制长度，避免 node_id 超过 128 字符
        return slug[:80]

    def _feature_id(self, feature_name: str) -> str:
        """基于 feature 名称生成稳定的 feature_id。"""
        if feature_name in self._shared_features:
            return self._shared_features[feature_name]
        slug = self._slugify(feature_name)
        fid = f"feature-{slug}"
        self._shared_features[feature_name] = fid
        return fid

    # -------------------------------------------------------------- 解析入口

    def parse_rules_file(self, rules: Iterable[dict[str, Any]]) -> list[ParsedRule]:
        """解析 ``process_rules.json`` 加载后的规则列表。"""
        results: list[ParsedRule] = []
        for raw in rules:
            try:
                parsed = self.parse_single_rule(raw)
            except (ValueError, TypeError, KeyError) as exc:
                # 跳过异常规则，保持容错
                logger.warning("rule parse error: %s", exc)
                continue
            results.append(parsed)
        return results

    def parse_single_rule(self, raw: dict[str, Any]) -> ParsedRule:
        """解析单条规则字典。"""
        if not isinstance(raw, dict):
            raise TypeError(f"rule must be dict, got {type(raw).__name__}")

        process_id = str(raw.get("id", "")).strip()
        if not process_id:
            raise ValueError("rule.id is required and must be non-empty")

        name = str(raw.get("name", "")).strip()
        category = str(raw.get("category", "")).strip()
        description = str(raw.get("description", "")).strip()
        details = raw.get("details", {}) or {}
        if not isinstance(details, dict):
            details = {"value": details}

        # 在 name + description + rationale 文本上做关键词匹配
        text_parts = [name, description]
        rationale = details.get("rationale") if isinstance(details, dict) else None
        if isinstance(rationale, str):
            text_parts.append(rationale)
        haystack = "\n".join(p for p in text_parts if p)

        features = self._extract_features(haystack)

        return ParsedRule(
            process_id=process_id,
            process_name=name or process_id,
            process_category=category,
            process_description=description,
            process_details=details,
            features=features,
            confidence=self._default_confidence,
            source="rule",
        )

    # -------------------------------------------------------------- 关键词提取

    def _extract_features(self, text: str) -> list[ParsedFeature]:
        """从给定文本中抽取所有匹配到的 Feature 实体。

        实现：
            - 遍历 self._keywords；
            - 用正则 ``search`` 找到首个命中位置，避免一处文本多次匹配同一
              关键词（取去重后的 feature 集合）。
        """
        if not text:
            return []
        seen: set[str] = set()
        results: list[ParsedFeature] = []
        for pattern, feature_name, feature_type in self._keywords:
            if feature_name in seen:
                continue
            if re.search(pattern, text):
                fid = self._feature_id(feature_name)
                results.append(
                    ParsedFeature(
                        feature_id=fid,
                        name=feature_name,
                        feature_type=feature_type,
                    )
                )
                seen.add(feature_name)
        return results


# 便捷函数


def parse_process_rules(
    rules: Iterable[dict[str, Any]],
    *,
    keywords: list[tuple[str, str, str]] | None = None,
    default_confidence: float = 0.9,
) -> list[ParsedRule]:
    """便捷函数：解析 ``process_rules.json`` 加载后的规则列表。

    Args:
        rules: 规则字典迭代器。
        keywords: 可选自定义关键词词典。
        default_confidence: 关系默认可信度。

    Returns:
        :class:`ParsedRule` 列表。
    """
    parser = RuleParser(keywords=keywords, default_confidence=default_confidence)
    return parser.parse_rules_file(rules)


__all__ = [
    "ParsedRule",
    "ParsedFeature",
    "RuleParser",
    "parse_process_rules",
]
