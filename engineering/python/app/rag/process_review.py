"""Content-bound human review policy; no network or persistence side effects."""
from __future__ import annotations

import hashlib
import json
import math
import re
from urllib.parse import urlsplit

from .process_applicability import DrillingLimits


CONTENT_FIELDS = ("feature", "process", "tool", "parameters", "material", "confidence", "source", "chunk_ids", "tags")


def content_digest(record: dict) -> str:
    content = {key: record[key] for key in CONTENT_FIELDS}
    # Candidate annotations are persisted but cannot grant approval or alter approved evidence.
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def evidence_digest(evidence: dict) -> str:
    return hashlib.sha256(json.dumps(evidence, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def validate_approval(record: dict, evidence: dict) -> None:
    """Require explicit, inspectable evidence; completeness is not physical validation."""
    if not isinstance(evidence, dict):
        raise ValueError("审核依据必须为对象")
    url = urlsplit(str(evidence.get("source_url", "")))
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("需要无凭据的 HTTPS 来源地址")
    if not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("document_sha256", ""))):
        raise ValueError("需要来源文档 SHA256")
    page = evidence.get("page")
    if type(page) is not int or page < 1:
        raise ValueError("需要正整数页码")
    if evidence.get("unresolved") != []:
        raise ValueError("未解决事项必须明确为空列表")
    if record["material"] == "general" or evidence.get("material") != record["material"]:
        raise ValueError("必须审核明确且匹配的材料")
    if evidence.get("tool") != record["tool"]:
        raise ValueError("审核刀具必须匹配记录")
    units = evidence.get("parameter_units")
    if not record["parameters"] or not isinstance(units, dict) or set(units) != set(record["parameters"]):
        raise ValueError("每个参数必须有对应单位")
    if any(not isinstance(unit, str) or not unit.strip() for unit in units.values()):
        raise ValueError("参数单位不能为空")
    if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
           for value in record["parameters"].values()):
        raise ValueError("审核参数必须为有限正数；范围应拆分为带单位的上下界")
    for key in ("applicability", "verification_method"):
        if not isinstance(evidence.get(key), str) or not evidence[key].strip():
            raise ValueError(f"需要 {key} 的具体审核说明")
    DrillingLimits.model_validate(evidence.get("constraints"))


def is_approved(record: dict) -> bool:
    review = record.get("review", {})
    if review.get("status") != "approved" or review.get("digest") != content_digest(record):
        return False
    try:
        validate_approval(record, review.get("evidence"))
        if review.get("evidence_digest") != evidence_digest(review["evidence"]):
            return False
    except (ValueError, TypeError):
        return False
    return bool(review.get("actor")) and review.get("revision") == record.get("revision")
