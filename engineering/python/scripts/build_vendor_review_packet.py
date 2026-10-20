"""Build a local, unapproved review packet from a pinned PDF and source supplement."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.rag.source_review_packet import build_vendor_review_packet  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pdf", "manifest", "supplement", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    packet = build_vendor_review_packet(args.pdf.read_bytes(),
        json.loads(args.manifest.read_text(encoding="utf-8")),
        json.loads(args.supplement.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"count": packet["count"], "review_status": packet["review_status"],
                      "eligible_for_recommendation": False}))


if __name__ == "__main__":
    main()
