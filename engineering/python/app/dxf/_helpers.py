"""加工特征文本解析工具（从 feature_extractor 拆出）。"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def is_counterbore_text(text: str) -> bool:
    """判断尺寸标注文本是否指示沉头孔。"""
    if "通孔" in text or "通" in text:
        return False
    keywords = ["沉头", "C'BORE", "CBORE", "COUNTERBORE"]
    return any(kw in text.upper() for kw in keywords)


def extract_tolerance_from_text(text: str) -> str:
    """从标注文本中提取公差等级指示。"""
    match = re.search(r"IT(\d{1,2})", text, re.IGNORECASE)
    if match:
        grade = int(match.group(1))
        if 1 <= grade <= 18:
            return f"IT{grade}"

    match = re.search(r"H(\d{1,2})", text)
    if match:
        grade = int(match.group(1))
        if 5 <= grade <= 14:
            return f"IT{grade}"

    if "±" in text:
        tol_match = re.search(r"±\s*([\d.]+)", text)
        if tol_match:
            try:
                tol_val = float(tol_match.group(1))
                if tol_val <= 0.01:
                    return "IT5"
                elif tol_val <= 0.03:
                    return "IT6"
                elif tol_val <= 0.05:
                    return "IT7"
                elif tol_val <= 0.1:
                    return "IT8"
                elif tol_val <= 0.2:
                    return "IT9"
                elif tol_val <= 0.5:
                    return "IT10"
            except ValueError as tol_err:
                # 公差数值解析失败时返回空字符串，调用方按空等级处理
                logger.debug(
                    "Failed to parse tolerance value from text %r: %s",
                    text,
                    tol_err,
                    exc_info=True,
                )

    return ""
