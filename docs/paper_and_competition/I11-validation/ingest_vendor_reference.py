"""Import source notes into a separate PRIVATE, unreviewed KB; verify retrieval only."""
from contextlib import closing
import json
from pathlib import Path
import sys
import time

evidence = Path(__file__).resolve().parent
root = evidence.parents[2]
sys.path.insert(0, str(root / "scripts"))
from nexent_client import authenticated_client

name = "lingjing_vendor_references_pending_review"
document = root / "competition/nexent/knowledge-candidates/dormer-reference-note.md"
marker = "DORMER_R10A_REFERENCE_PENDING_REVIEW"
report = {"business_agent_binding_changed": False, "review_status": "unreviewed", "retrieval_only": True, "calls": []}


def record(stage, response):
    body = response.json()
    report["calls"].append({"stage": stage, "http_status": response.status_code, "body": body})
    (evidence / "vendor-ingestion.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    response.raise_for_status()
    return body


with closing(authenticated_client(Path("D:/nexent-deploy"), timeout=60)) as client:
    info = record("list", client.get("/indices", params={"include_stats": True}))["indices_info"]
    existing = next((row for row in info if row.get("display_name") == name), None)
    if existing:
        index_name = existing["name"]
    else:
        model = next(row["embedding_model_id"] for row in info if row.get("display_name") == "lingjing_machining_kb")
        index_name = record("create", client.post(f"/indices/{name}", json={"embedding_model_id": model,
            "ingroup_permission": "PRIVATE", "preserve_source_file": True}))["id"]
    report["index_name"] = index_name
    existing_files = record("files", client.get(f"/indices/{index_name}/files"))["files"]
    found = next((row for row in existing_files if row.get("file") == document.name), None)
    if found is None:
        uploaded = record("upload", client.post("/file/upload", data={"destination": "minio", "index_name": index_name},
            files=[("file", (document.name, document.read_bytes(), "text/markdown"))]))
        assert not uploaded["errors"] and len(uploaded["file_records"]) == 1
        file_id = uploaded["file_records"][0]["file_id"]
        path = uploaded["uploaded_file_paths"][0]
        record("process", client.post("/file/process", json={"files": [{"file_id": file_id, "filename": document.name,
            "path_or_url": path}], "index_name": index_name, "destination": "minio", "chunking_strategy": "basic"}))
    else:
        file_id, path = found["file_id"], found["path_or_url"]
    for attempt in range(20):
        files = record("status", client.get(f"/indices/{index_name}/files"))["files"]
        found = next((row for row in files if row.get("file_id") == file_id), None)
        if found and found.get("status") == "COMPLETED":
            break
        if found and found.get("status", "").endswith("FAILED"):
            raise RuntimeError("Vendor reference ingestion failed; see preserved status record")
        time.sleep(3)
    assert found and found["status"] == "COMPLETED" and found["source_available"] and found["chunk_count"] > 0
    results = record("search", client.post("/indices/search/hybrid", json={"query": marker, "index_names": [index_name], "top_k": 3}))["results"]
    hits = [row for row in results if row.get("path_or_url") == path and marker in row.get("content", "")]
    assert hits and all(row.get("id") for row in hits)
    report.update(completed_and_source_traceable=True, chunks=found["chunk_count"], marker_hits=len(hits))
    (evidence / "vendor-ingestion.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"completed": True, "chunks": found["chunk_count"], "marker_hits": len(hits), "review_status": "unreviewed"}), flush=True)
