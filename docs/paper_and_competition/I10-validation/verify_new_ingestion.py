"""Upload a synthetic marker to a separate private acceptance KB and retrieve its source."""
from contextlib import closing
import json
from pathlib import Path
import sys
import time

evidence = Path(__file__).resolve().parent
sys.path.insert(0, str(evidence.parents[2] / "scripts"))
from nexent_client import authenticated_client

name = "lingjing_acceptance_i10_synthetic"
filename = "lingjing_i10_ingestion_fixture.md"
marker = "LINGJING_I10_20261016_INGEST"
content = f"# 合成验收样本\n\n唯一标记：{marker}\n\n本文件仅用于验证上传、文本处理、向量入库及来源追溯，不含加工参数，不是行业真实数据或审核通过的工艺知识。\n"
(evidence / filename).write_text(content, encoding="utf-8")
report = {"synthetic_fixture": True, "bound_to_business_agent": False, "calls": []}


def record(stage, response):
    body = response.json()
    report["calls"].append({"stage": stage, "status": response.status_code, "body": body})
    (evidence / "new-ingestion.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    response.raise_for_status()
    return body


with closing(authenticated_client(Path("D:/nexent-deploy"), timeout=60)) as client:
    listing = record("list", client.get("/indices", params={"include_stats": True}))
    info = listing["indices_info"]
    assert info, "Knowledge catalog is not ready; do not create a replacement knowledge base"
    existing = next((item for item in info if item.get("display_name") == name), None)
    if existing:
        index_name = existing["name"]
    else:
        created = record("create_acceptance_kb", client.post(f"/indices/{name}", json={
            "embedding_model_id": info[0]["embedding_model_id"], "ingroup_permission": "PRIVATE", "preserve_source_file": True}))
        index_name = created["id"]
    report["index_name"] = index_name
    files = record("files_before", client.get(f"/indices/{index_name}/files"))["files"]
    failure_record = evidence / "new-ingestion-memory-failure.json"
    original_file_id = None
    if failure_record.exists():
        previous = json.loads(failure_record.read_text(encoding="utf-8"))
        original_file_id = next((item["body"]["file_records"][0]["file_id"] for item in previous["calls"]
                                 if item["stage"] == "upload"), None)
    found = next((item for item in files if item.get("file_id") == original_file_id), None)
    if found is None:
        found = next((item for item in files if item.get("file") == filename), None)
    if found is None:
        uploaded = record("upload", client.post("/file/upload", data={"destination": "minio", "index_name": index_name},
            files=[("file", (filename, content.encode("utf-8"), "text/markdown"))]))
        assert not uploaded["errors"] and len(uploaded["uploaded_file_paths"]) == 1
        file_record = uploaded.get("file_records", [{}])[0]
        details = {"path_or_url": uploaded["uploaded_file_paths"][0], "filename": filename}
        if file_record.get("file_id"):
            details["file_id"] = file_record["file_id"]
        record("process_submit", client.post("/file/process", json={"files": [details], "index_name": index_name,
            "destination": "minio", "chunking_strategy": "basic"}))
    elif found.get("status", "").endswith("FAILED") and "--retry-failed" in sys.argv:
        details = {"path_or_url": found["path_or_url"], "filename": found["file"], "file_id": found["file_id"]}
        record("retry_same_file", client.post("/file/process", json={"files": [details], "index_name": index_name,
            "destination": "minio", "chunking_strategy": "basic"}))
    tracked_file_id = details.get("file_id") if "details" in locals() else found["file_id"]
    previous_task_id = found.get("latest_task_id") if found else None
    for attempt in range(30):
        response = client.get(f"/indices/{index_name}/files")
        body = record("processing_status", response)
        found = next((item for item in body["files"] if item.get("file_id") == tracked_file_id), None)
        if found and found.get("status") == "COMPLETED":
            break
        if found and found.get("status", "").endswith("FAILED") and (
            found.get("latest_task_id") != previous_task_id or attempt >= 5
        ):
            break
        time.sleep(3)
    assert found and found["status"] == "COMPLETED", "File processing did not complete"
    assert found["chunk_count"] > 0 and found["source_available"]
    retrieved = record("retrieve_marker", client.post("/indices/search/hybrid", json={"query": marker,
        "index_names": [index_name], "top_k": 3}))
    matched = [row for row in retrieved["results"] if marker in row.get("content", "")]
    assert matched and any(row.get("id") and row.get("path_or_url") == found["path_or_url"] for row in matched)
    report["completed_and_source_traceable"] = True
    report["chunk_count"] = found["chunk_count"]
    (evidence / "new-ingestion.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"completed": True, "chunks": found["chunk_count"], "marker_hits": len(matched)}), flush=True)
