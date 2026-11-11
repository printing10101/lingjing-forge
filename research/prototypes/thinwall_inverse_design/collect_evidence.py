"""Read-only targeted asset inventory and public metadata capture.

No source content is executed. API failure is recorded; zero hits never proves
novelty. No user credentials, private email, or project files are uploaded.
"""

import csv
import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
EXCLUDED = {"__pycache__", "node_modules", "target", "dist", "site-packages", ".git"}
DOIS = [
    "10.1016/j.mfglet.2023.10.004",
    "10.3390/app132413340",
    "10.1016/j.ijmachtools.2018.08.003",
    "10.1243/09544054JEM1696",
    "10.1177/0954405420933710",
    "10.1088/2631-7990/adec24",
    "10.1016/j.ijmecsci.2026.112225",
]
QUERIES = [
    '"thin-walled" milling',
    '"thin-walled" milling "structural optimization"',
    '"chatter" "preform"',
    '"chatter" "thickness" "design"',
    '"chatter" "static stiffness"',
    '"milling" "robust" "structural design"',
]


def request_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "ThinwallFeasibilityResearch/0.1"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def usable_files(folder):
    if not folder.exists():
        return []
    return [p for p in folder.rglob("*") if p.is_file()
            and not any(part in EXCLUDED or part.startswith(".venv") for part in p.parts)]


def main():
    output = ROOT/"evidence"
    output.mkdir(exist_ok=True)
    assets = []
    scopes = ["engineering/python/app/simulation/chatter", "engineering/python/app/chatter_prediction",
              "research/experiments/real_validation", "research/datasets/measured_stability"]
    for scope in scopes:
        files = usable_files(REPO/scope)
        python_files = [p for p in files if p.suffix == ".py"]
        texts = [p.read_text(encoding="utf-8", errors="replace") for p in python_files]
        assets.append({"scope": scope, "all_file_count": len(files), "python_file_count": len(texts),
                       "python_lines": sum(len(t.splitlines()) for t in texts),
                       "test_function_count": sum(len(re.findall(r"^\s*(?:async )?def test_", t, re.M)) for t in texts),
                       "result_json_count": sum(p.suffix == ".json" for p in files)})
    csv_inventory = []
    for path in sorted((REPO/"research/datasets/measured_stability").glob("*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        csv_inventory.append({"path": str(path.relative_to(REPO)), "rows": len(rows),
                              "columns": list(rows[0]) if rows else [],
                              "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                              "sources": dict(Counter(r.get("source", "not present") for r in rows))})
    metadata = []
    for doi in DOIS:
        try:
            message = request_json("https://api.crossref.org/works/"+urllib.parse.quote(doi, safe=""))["message"]
            metadata.append({"requested_doi": doi, "status": "ok", "metadata": message})
        except Exception as exc:
            metadata.append({"requested_doi": doi, "status": "error", "error": str(exc)})
        print("crossref", doi, metadata[-1]["status"], flush=True)
    searches = []
    for query in QUERIES:
        params = {"filter": "title_and_abstract.search:"+query, "per_page": 15}
        url = "https://api.openalex.org/works?"+urllib.parse.urlencode(params)
        try:
            result = request_json(url)
            hits = [{"title": w.get("title"), "doi": w.get("doi"), "year": w.get("publication_year"),
                     "open_access": w.get("open_access")} for w in result.get("results", [])]
            searches.append({"query": query, "url": url, "status": "ok", "count": result.get("meta", {}).get("count"),
                             "hits": hits, "relevance_validated": False})
        except Exception as exc:
            searches.append({"query": query, "url": url, "status": "error", "error": str(exc)})
        print("openalex", query, searches[-1]["status"], flush=True)
    payload = {"captured_utc": datetime.now(timezone.utc).isoformat(),
               "inventory_scope": "Targeted machining dynamics assets, not a full repository inventory",
               "assets": assets, "csv_inventory": csv_inventory, "crossref": metadata,
               "openalex": searches,
               "limitations": "Metadata and exploratory web/API search only. No WoS/Scopus/CNKI systematic search. Public hit counts are not novelty evidence. No API mailto provided because no user email was authorized."}
    (output/"inventory_and_metadata.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
