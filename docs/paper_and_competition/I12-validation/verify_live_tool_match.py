"""Check the actual Nexent draft preserves tool-family and parameter-source uncertainty."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import httpx

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
for attempt in range(12):
    try:
        response = httpx.get("http://127.0.0.1:8765/api/health", trust_env=False, timeout=2)
        if response.status_code == 200:
            break
    except httpx.HTTPError:
        pass
    time.sleep(5)
else:
    raise RuntimeError("Desktop backend did not become ready")
subprocess.run([sys.executable, str(root / "docs/paper_and_competition/I9-validation/verify_nexent_tools.py")], cwd=root, check=True, timeout=60)
source = root / "docs/paper_and_competition/I9-validation/mcp-tool-execution.json"
shutil.copy2(source, evidence / "mcp-tool-execution.json")
calls = json.loads(source.read_text(encoding="utf-8"))["cases"]
draft = next(c["result"]["data"] for c in calls if c["tool"] == "process_plan_run")
tools = draft["process_plans"][0]["tools"]
drill = next(t for t in tools if t["application"] == "钻孔")
center = next(t for t in tools if t["application"] == "打中心孔定位")
assert drill["diameter_mm"] == 8 and drill["diameter_verified"] is False
assert drill["diameter_range_mm"][0] <= 8 <= drill["diameter_range_mm"][1]
assert drill["suitability_score"] <= 50
assert drill["cutting_parameters"]["source_verified"] is False
assert drill["cutting_parameters"]["source"] == "handbook_common_range"
assert any("尚未核验" in warning for warning in drill["warnings"])
assert center["tool_series"] == "center_drill" and center["diameter_mm"] == 0 and center["diameter_verified"] is False
assert draft["operation_plan"]["tool_diameter_mm"] == 8
status = next(c["result"]["data"]["recommendation_status"] for c in calls if c["tool"] == "cam_recommend_process")
assert status == "knowledge_empty"
report = {"mcp_contracts_passed": sum(c["contract_passed"] for c in calls), "draft_generated": draft["success"],
          "drill_diameter_mm": drill["diameter_mm"], "tool_family_range_mm": drill["diameter_range_mm"],
          "diameter_verified": drill["diameter_verified"], "parameter_source": drill["cutting_parameters"]["source"],
          "parameter_source_verified": False, "unverified_warning_visible": True, "center_diameter_unknown": True,
          "formal_recommendation_status": status, "physical_machine_acceptance": False}
(evidence / "live-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False), flush=True)
