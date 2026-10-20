"""Build traceable, unreviewed candidates without promoting vendor facts to NC advice."""
from __future__ import annotations

import hashlib
import json
import math
from urllib.parse import urlsplit


def _positive_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 必须为有限正数")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} 必须为有限正数")
    return float(value)


def build_vendor_candidates(pdf: bytes, manifest: dict) -> list[dict]:
    """Normalize confirmed feed units; preserve uncertain speed codes unchanged.

    A manifest records selected facts checked against a manufacturer's PDF.
    Downloaded bytes must match its pinned hash. All outputs remain unreviewed.
    """
    if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
        raise ValueError("[来源错误] 文件不是 PDF。建议操作：核对厂商原始下载地址。")
    if not isinstance(manifest, dict):
        raise ValueError("来源清单必须为对象")
    digest = hashlib.sha256(pdf).hexdigest()
    if digest != manifest.get("sha256"):
        raise ValueError("[来源错误] 原文校验值不一致。建议操作：重新核查版本和提取记录，勿沿用旧页码。")
    url = manifest.get("url", "")
    if not isinstance(url, str):
        raise ValueError("来源地址必须为字符串")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("来源必须是无登录凭据的 HTTPS 地址")
    records = manifest.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("来源记录不能为空")
    candidates = []
    seen = set()
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("每条来源记录必须为对象")
        for name in ("material_group", "tool", "locator", "hardness_condition", "speed_code"):
            if not isinstance(row.get(name), str) or not row[name].strip():
                raise ValueError(f"缺少来源条件：{name}")
        pages = row.get("pdf_pages")
        if not isinstance(pages, list) or not pages or any(type(p) is not int or p < 1 for p in pages):
            raise ValueError("必须给出从 1 开始的 PDF 页码")
        feed = _positive_number(row.get("feed"), "feed")
        diameter = _positive_number(row.get("diameter_inch"), "diameter_inch")
        tolerance = _positive_number(row.get("feed_tolerance_percent"), "feed_tolerance_percent")
        if tolerance >= 100:
            raise ValueError("进给公差必须小于 100%")
        unit = row.get("feed_unit")
        if unit == "inch/rev":
            feed_mm = feed * 25.4
        elif unit == "mm/rev":
            feed_mm = feed
        else:
            raise ValueError("未确认的进给单位，禁止转换；每齿进给不能作为每转进给")
        feed_mm = _positive_number(feed_mm, "converted_feed")
        diameter_mm = _positive_number(diameter * 25.4, "converted_diameter")
        minimum = _positive_number(feed_mm * (1 - tolerance / 100), "minimum_feed")
        maximum = _positive_number(feed_mm * (1 + tolerance / 100), "maximum_feed")
        identity = json.dumps({"source_sha256": digest, "url": url, "record": row}, sort_keys=True, ensure_ascii=False)
        candidate_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if candidate_id in seen:
            raise ValueError("同一来源记录重复")
        seen.add(candidate_id)
        candidates.append({
            "candidate_id": candidate_id, "source": "manufacturer_candidate",
            "review_status": "unreviewed", "eligible_for_recommendation": False,
            "feature": "hole", "process": "drill", "tool": row["tool"],
            "material": row["material_group"],
            "conditions": {"hardness": row["hardness_condition"], "diameter_mm": diameter_mm},
            "parameters": {"feed_per_revolution_mm": round(feed_mm, 9),
                           "feed_min_mm_per_rev": round(minimum, 9),
                           "feed_max_mm_per_rev": round(maximum, 9)},
            "unresolved": ["cutting_speed_unit", "material_grade_equivalence", "machining_review"],
            "original_record": dict(row),
            "provenance": {"url": url, "sha256": digest, "pdf_pages": list(pages), "locator": row["locator"]},
        })
    return candidates
