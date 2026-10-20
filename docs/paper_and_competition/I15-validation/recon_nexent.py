"""Read safe identifiers only; authentication fields stay in private files."""
from contextlib import closing
import json
from pathlib import Path
import sys

directory = Path(__file__).resolve().parent
root = directory.parents[2]
sys.path.insert(0, str(root / "scripts"))
from nexent_client import authenticated_client  # noqa: E402

with closing(authenticated_client(Path("D:/nexent-deploy"), timeout=40)) as client:
    response = client.get("/mcp/list")
    response.raise_for_status()
    value = response.json()
    entries = value if isinstance(value, list) else value.get("remote_mcp_server_list", [])
    report = {"mcp_shape": type(value).__name__, "mcp_keys": list(value) if isinstance(value, dict) else [],
              "services": [{key: row.get(key) for key in ("mcp_id", "mcp_name", "enabled", "status")} for row in entries]}
    tools = client.get("/tool/list").json()
    report["candidate_tools"] = [{key: tool.get(key) for key in ("tool_id", "name", "source", "usage")}
                                 for tool in tools if "knowledge_candidate" in tool.get("name", "")]
print(json.dumps(report, ensure_ascii=False), flush=True)
(directory / "nexent-discovery.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
