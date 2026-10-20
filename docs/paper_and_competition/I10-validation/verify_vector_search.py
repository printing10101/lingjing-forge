"""Verify actual embedding output and authenticated Nexent retrieval with source metadata."""
from contextlib import closing
import json
import math
from pathlib import Path
import sys
import time

import httpx

evidence = Path(__file__).resolve().parent
sys.path.insert(0, str(evidence.parents[2] / "scripts"))
from nexent_client import authenticated_client

key = Path("<llama_cpp目录>/.api-key").read_text(encoding="utf-8").strip()
with httpx.Client(base_url="http://127.0.0.1:8090", headers={"Authorization": "Bearer " + key},
                  trust_env=False, timeout=60) as embedding:
    for _ in range(12):
        try:
            ready = embedding.get("/health")
            if ready.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(3)
    else:
        raise RuntimeError("Embedding service did not become ready")
    models = embedding.get("/v1/models")
    models.raise_for_status()
    model = models.json()["data"][0]["id"]
    response = embedding.post("/v1/embeddings", json={"model": model, "input": "钻孔加工参数"})
    response.raise_for_status()
    vector = response.json()["data"][0]["embedding"]
    assert len(vector) == 1024 and all(math.isfinite(v) for v in vector) and any(vector)
report = {"embedding_http_status": response.status_code, "embedding_dimension": len(vector),
          "finite_nonzero_vector": True, "searches": []}
(evidence / "vector-search.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
with closing(authenticated_client(Path("D:/nexent-deploy"), timeout=90)) as client:
    listing = client.get("/indices", params={"include_stats": True})
    listing.raise_for_status()
    indices = [row["name"] for row in listing.json()["indices_info"]
               if row.get("display_name") in ("kb_lingjing_manual", "lingjing_machining_kb")]
    assert len(indices) == 2, "Original business knowledge bases are not ready"
    for query in ["钻孔", "加工失败"]:
        response = client.post("/indices/search/hybrid", json={"query": query, "index_names": indices, "top_k": 3})
        body = response.json()
        rows = body.get("results", [])
        sources_present = bool(rows) and all(row.get("id") and row.get("filename") and row.get("path_or_url") for row in rows)
        report["searches"].append({"query": query, "http_status": response.status_code,
                                  "total": body.get("total"), "response": body,
                                  "nonempty_results": bool(rows), "source_metadata_present": sources_present})
        (evidence / "vector-search.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"query": query, "status": response.status_code, "results": len(rows)}, ensure_ascii=False), flush=True)
        response.raise_for_status()
        assert rows, "No knowledge retrieved"
        assert sources_present, "Retrieved chunks have no traceable source metadata"
