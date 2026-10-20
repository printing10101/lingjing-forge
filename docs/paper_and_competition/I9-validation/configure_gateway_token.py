"""Create a scoped deployment credential without reactivating revoked tokens."""
import json
from pathlib import Path
import sys

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
sys.path.insert(0, str(root / "engineering/python"))
from app.agent.auth import AgentTokenStore

deployment = Path("D:/nexent-deploy")
store = AgentTokenStore(str(deployment / "agent_tokens.json"))
raw = (deployment / "agent-token.txt").read_text().strip()
token = store.validate_token(raw)
if token is None:
    raw, token = store.create_token(scopes=["R", "W"], expires_in=7 * 86400, paper_only=True)
    (deployment / "agent-token.txt").write_text(raw, encoding="utf-8")
assert token.scopes == ["R", "W"] and token.paper_only
report = {"agent_id": token.agent_id, "scopes": token.scopes, "paper_only": token.paper_only,
          "expires_at": token.expires_at, "separate_deployment_store": True,
          "revoked_tokens_reactivated": False}
(evidence / "gateway-auth.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
