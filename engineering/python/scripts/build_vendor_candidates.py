"""Build a candidate artifact from a pinned vendor PDF and a checked extraction manifest."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.rag.source_candidates import build_vendor_candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidates = build_vendor_candidates(args.pdf.read_bytes(), json.loads(args.manifest.read_text(encoding="utf-8")))
    output = {"schema_version": 1, "review_status": "unreviewed", "count": len(candidates), "candidates": candidates}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"count": len(candidates), "review_status": "unreviewed"}))


if __name__ == "__main__":
    main()
