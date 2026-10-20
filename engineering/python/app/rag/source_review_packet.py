"""Prepare source supplements for human review without granting recommendation trust."""
from __future__ import annotations

from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from .source_candidates import build_vendor_candidates


class SourceFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: StrictStr = Field(min_length=1, max_length=80)
    value: StrictStr = Field(min_length=1, max_length=2000)
    pdf_pages: list[StrictInt] = Field(min_length=1, max_length=10)
    locator: StrictStr = Field(min_length=1, max_length=500)


class SourceSupplement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_sha256: StrictStr
    shared_facts: list[SourceFact] = Field(min_length=1)
    materials: dict[StrictStr, list[SourceFact]]
    open_questions: list[StrictStr] = Field(min_length=1)


def build_vendor_review_packet(pdf: bytes, manifest: dict, supplement: dict) -> dict:
    """Bind manually checked facts to the pinned document and retain every blocker.

    Hash validation establishes document identity, not factual correctness. This
    artifact is review preparation only; it neither writes the index nor approves.
    Original candidate identities and parameters are preserved for reconciliation.
    """
    candidates = build_vendor_candidates(pdf, manifest)
    source = SourceSupplement.model_validate(supplement)
    if source.document_sha256 != manifest["sha256"]:
        raise ValueError("补充资料的原文校验值不一致")
    page_count = manifest.get("page_count")
    if type(page_count) is not int or page_count < 1:
        raise ValueError("审核资料需要来源清单中的页数")
    materials = {candidate["material"] for candidate in candidates}
    if set(source.materials) != materials:
        raise ValueError("补充资料的材料组必须与原始候选逐一对应")
    if any(not question.strip() for question in source.open_questions):
        raise ValueError("待解决问题不能为空白")
    packets = []
    for candidate in candidates:
        if not source.materials[candidate["material"]]:
            raise ValueError("每个材料组至少需要一条来源事实")
        facts = [*source.shared_facts, *source.materials[candidate["material"]]]
        keys = [fact.key for fact in facts]
        if len(set(keys)) != len(keys):
            raise ValueError("同一候选的来源事实键重复")
        for fact in facts:
            if not fact.key.strip() or not fact.value.strip() or not fact.locator.strip():
                raise ValueError("来源事实与定位不能为空白")
            if any(page < 1 or page > page_count for page in fact.pdf_pages):
                raise ValueError("来源事实页码超出文档范围")
        blockers = list(dict.fromkeys([*candidate["unresolved"], *source.open_questions]))
        descriptions = [f"{fact.key}: {fact.value}（PDF页 {','.join(map(str, fact.pdf_pages))}；{fact.locator}）"
                        for fact in facts]
        packet = deepcopy(candidate)
        packet["source_facts"] = [fact.model_dump() for fact in facts]
        packet["unresolved"] = blockers
        packet["candidate_context"] = {
            "source_url": manifest["url"], "document_sha256": manifest["sha256"],
            "page": min(page for fact in facts for page in fact.pdf_pages),
            "parameter_units": {key: "mm/rev" for key in candidate["parameters"]},
            "applicability": "；".join(descriptions),
            "verification_method": "原文哈希核对及页码目视核对；补充资料只供人工审核，尚未完成现场工艺确认。",
            "unresolved": blockers,
            "note": "原始候选标识用于关联；补充内容不代表已提交或批准，不可据此生成正式推荐。",
        }
        packets.append(packet)
    return {"schema_version": 1, "artifact_kind": "human_review_preparation",
            "review_status": "unreviewed", "eligible_for_recommendation": False,
            "document_sha256": manifest["sha256"], "count": len(packets), "candidates": packets}
