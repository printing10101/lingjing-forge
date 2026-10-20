"""Move existing deployment login literals to private configuration, without logging them."""
import ast
import json
from pathlib import Path
import shutil

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
deployment = Path("D:/nexent-deploy")
private = deployment / "preserved"
private.mkdir(exist_ok=True)
credential_file = deployment / "svc-login.json"
reference = (deployment / "configure_agent.py").read_text(encoding="utf-8")
if not credential_file.exists():
    for node in ast.walk(ast.parse(reference)):
        if isinstance(node, ast.Dict):
            try:
                obj = ast.literal_eval(node)
            except (ValueError, TypeError):
                continue
            if isinstance(obj, dict) and isinstance(obj.get("email"), str) and isinstance(obj.get("password"), str):
                credential_file.write_text(json.dumps({key: obj[key] for key in ("email", "password")}), encoding="utf-8")
                break
    else:
        raise RuntimeError("No existing service credential found; do not invent one")

shutil.copy2(root / "scripts/nexent_client.py", deployment / "nexent_connection.py")
changed = []
for filename in ("configure_agent.py", "test_agent_dialog.py"):
    path = deployment / filename
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    edits = []
    for index, statement in enumerate(function.body):
        if not isinstance(statement, ast.Assign) or not isinstance(statement.value, ast.Call):
            continue
        call = statement.value
        if isinstance(call.func, ast.Attribute) and call.func.attr == "Client":
            timeout = next((ast.unparse(k.value) for k in call.keywords if k.arg == "timeout"), "30")
            edits.append((statement.lineno - 1, statement.end_lineno,
                          f"    client = authenticated_client(Path(__file__).resolve().parent, timeout={timeout})\n"))
        if call.args and isinstance(call.args[0], ast.Constant) and call.args[0].value == "/user/signin":
            end = statement.end_lineno
            following = function.body[index + 1]
            if (isinstance(following, ast.Expr) and isinstance(following.value, ast.Call)
                    and isinstance(following.value.func, ast.Attribute) and following.value.func.attr == "raise_for_status"):
                end = following.end_lineno
            edits.append((statement.lineno - 1, end, ""))
    if edits:
        backup = private / f"{filename}.before-i9"
        if not backup.exists():
            shutil.copy2(path, backup)
        lines = source.splitlines(keepends=True)
        for start, end, replacement in sorted(edits, reverse=True):
            lines[start:end] = [replacement] if replacement else []
        source = "".join(lines)
        imports = "from nexent_connection import authenticated_client\n"
        if "from pathlib import Path" not in source:
            imports += "from pathlib import Path\n"
        source = source.replace("import httpx\n", "import httpx\n" + imports, 1)
        compile(source, str(path), "exec")
        path.write_text(source, encoding="utf-8")
        changed.append(filename)
print(json.dumps({"scripts_migrated": changed, "private_credentials_configured": credential_file.exists(),
                  "helper_deployed": True}), flush=True)
