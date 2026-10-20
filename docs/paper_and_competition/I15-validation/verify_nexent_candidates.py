"""Submit the already-collected vendor facts as pending via real Nexent tool validation."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sys

directory = Path(__file__).resolve().parent
root = directory.parents[2]
sys.path.insert(0, str(root / "scripts"))
from nexent_client import authenticated_client  # noqa: E402


def unpack(value):
    for _ in range(10):
        if isinstance(value, str):
            value = json.loads(value)
        elif isinstance(value, dict) and value.get("type") == "text":
            value = value["text"]
        elif isinstance(value, list) and len(value) == 1:
            value = value[0]
        else:
            return value
    raise ValueError("Unexpected tool response nesting")


candidates = json.loads((root / "competition/nexent/knowledge-candidates/dormer-drill-candidates.json").read_text(encoding="utf-8"))["candidates"]
pdf_hash = hashlib.sha256(Path("D:/nexent-deploy/sources/dormer-drills.pdf").read_bytes()).hexdigest()
results = []
with closing(authenticated_client(Path("D:/nexent-deploy"), timeout=45)) as client:
    tools = {tool["name"]: tool for tool in client.get("/tool/list").json()}
    def call(name, inputs):
        tool = tools[name]
        response = client.post("/tool/validate", json={"name": name, "source": tool["source"], "usage": tool["usage"], "inputs": inputs})
        response.raise_for_status()
        result = unpack(response.json())
        assert result.get("code") == 0 and not result.get("error"), name
        return result["data"]
    for candidate in candidates:
        assert candidate["provenance"]["sha256"] == pdf_hash
        content = {"feature": candidate["feature"], "process": candidate["process"], "tool": "dormer_r10a_hss",
            "material": candidate["material"], "parameters": candidate["parameters"],
            "candidate_context": {"source_url": candidate["provenance"]["url"], "document_sha256": pdf_hash,
                "page": candidate["provenance"]["pdf_pages"][0], "parameter_units": {key: "mm/rev" for key in candidate["parameters"]},
                "applicability": f'原始刀具：{candidate["tool"]}；直径 {candidate["conditions"]["diameter_mm"]} mm；硬度 {candidate["conditions"]["hardness"]}；不等同于精确 8 mm 或现场刀具确认。',
                "verification_method": f'I11 原文抓取、页 4/5 渲染与进给单位换算；定位：{candidate["provenance"]["locator"]}；尚未完成工艺专家审核。',
                "unresolved": candidate["unresolved"], "note": "厂商公开资料候选，等待人工核对；不用于正式加工推荐"},
            "tags": ["vendor_reference", "pending_human_review"]}
        inputs = {"submission_id": candidate["candidate_id"], "content": content}
        first = call("cam_submit_knowledge_candidate", inputs)
        retry = call("cam_submit_knowledge_candidate", inputs)
        assert retry["created"] is False and retry["record"]["record_id"] == first["record"]["record_id"]
        state = call("cam_get_knowledge_candidate", {"record_id": first["record"]["record_id"]})
        assert state["record"]["review"]["status"] == "pending" and state["eligible_for_recommendation"] is False
        recommendation = call("cam_recommend_process", {"feature": candidate["feature"], "material": candidate["material"], "top_k": 3})
        assert recommendation["recommendations"] == []
        results.append({"candidate_id": candidate["candidate_id"], "record_id": first["record"]["record_id"],
                        "material": candidate["material"], "status": "pending", "retry_deduplicated": True,
                        "eligible_for_recommendation": False, "formal_recommendations": 0})
        print(json.dumps(results[-1]), flush=True)
(directory / "nexent-candidate-execution.json").write_text(json.dumps({"real_vendor_candidates": results,
    "nexent_tool_calls": len(results) * 4, "real_approvals": 0, "llm_autonomous_execution": False}, indent=2), encoding="utf-8")
