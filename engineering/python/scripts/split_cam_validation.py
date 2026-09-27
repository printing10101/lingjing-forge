"""Extract schemas from cam_validation/routes.py into schemas.py"""
from pathlib import Path

ROUTES = Path(__file__).resolve().parent.parent / "app" / "api" / "v1" / "cam_validation" / "routes.py"
content = ROUTES.read_text(encoding="utf-8")
lines = content.split("\n")

# Find the schemas section boundaries
schemas_start = None
schemas_end = None
for i, line in enumerate(lines):
    if "# 请求 / 响应模型" in line:
        schemas_start = i - 2  # Include the ==== separator line above
    if schemas_start and i > schemas_start:
        if "# ====" in line and i > schemas_start + 5:
            schemas_end = i
            break

print(f"Schemas: lines {schemas_start+1}-{schemas_end}")

# Extract imports (lines 1 to schemas_start)
imports = "\n".join(lines[:schemas_start])
schemas_body = "\n".join(lines[schemas_start:schemas_end])
routes_body = "\n".join(lines[schemas_end:])

# Write schemas.py
schemas_dir = ROUTES.parent
schemas_imports = imports + "\n# Schemas extracted from routes.py (V3.0 split)\n"
schemas_file = schemas_dir / "schemas.py"
schemas_file.write_text(schemas_imports + schemas_body, encoding="utf-8")
print(f"Created {schemas_file.name}: {len(schemas_body.split(chr(10)))} lines")

# Write new routes.py (imports + routes without schemas)
new_routes = imports + '\nfrom app.api.v1.cam_validation.schemas import (\n'
# Collect all class names from schemas
schema_classes = [line.strip() for line in schemas_body.split("\n") if line.startswith("class ")]
for cls in schema_classes:
    class_name = cls.split("(")[0].replace("class ", "").strip()
    new_routes += f"    {class_name},\n"
new_routes += ")\n\n" + routes_body

ROUTES.write_text(new_routes, encoding="utf-8")
print(f"Rewrote routes.py: {len(new_routes.split(chr(10)))} lines")
