"""Exercise actual Nexent -> MCP -> desktop-backend tool calls."""
import json
from pathlib import Path
import time
import sys

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
sys.path.insert(0, str(root / "scripts"))
from nexent_client import authenticated_client
client = authenticated_client(Path("D:/nexent-deploy"), timeout=45)

tools = {t["name"]: t for t in client.get("/tool/list").json()}
def unpack(value):
    for _ in range(8):
        if isinstance(value, str):
            value = json.loads(value)
        elif isinstance(value, dict) and value.get("type") == "text":
            value = value["text"]
        elif isinstance(value, list) and len(value) == 1:
            value = value[0]
        else:
            return value
    raise ValueError("Unexpected nested response")

part = {"material": "45#钢", "part_type": "plate", "holes": [
    {"id": "h1", "type": "through", "position": {"x": 20, "y": 20}, "diameter": 8.0, "depth": 12.0}]}
cases = [
    ("gcode_get_failure_stats", {}),
    ("gcode_list_failure_cases", {"limit": 5, "outcome": "failure"}),
    ("cam_get_quadruple_stats", {}),
    ("cam_recommend_process", {"feature": "hole", "material": "steel_45", "top_k": 3}),
    ("dxf_describe_file", {"path": str(root / "engineering/python/output/competition_i9/plate.dxf")}),
    ("process_plan_run", {"part_description": part, "controller_type": "fanuc_0i", "safe_z": 60.0, "program_number": 1901}),
]
results = []
for name, inputs in cases:
    tool = tools[name]
    started = time.monotonic()
    response = client.post("/tool/validate", json={"name": name, "source": tool["source"],
                                                  "usage": tool["usage"], "inputs": inputs})
    result = unpack(response.json())
    passed = response.status_code == 200 and isinstance(result, dict) and result.get("code") == 0 and not result.get("error")
    if passed and name == "process_plan_run":
        passed = result["data"].get("success") is True and bool(result["data"].get("gcode", {}).get("program_text"))
    results.append({"tool": name, "inputs": inputs, "http_status": response.status_code,
                    "contract_passed": passed, "recommendation_available": (bool(result.get("data", {}).get("recommendations")) if name == "cam_recommend_process" else None), "duration_seconds": round(time.monotonic() - started, 3), "result": result})
    (evidence / "mcp-tool-execution.json").write_text(json.dumps({"synthetic_fixture": True, "cases": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"tool": name, "passed": passed, "duration": results[-1]["duration_seconds"]}), flush=True)
client.close()
raise SystemExit(0 if all(r["contract_passed"] for r in results) else 1)
