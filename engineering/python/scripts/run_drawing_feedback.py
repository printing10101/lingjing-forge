"""Run the isolated single-drawing feedback experiment from the repository root."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipelines.drawing_feedback import run_drawing_feedback


def main() -> int:
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description="单图纸 NC 与反馈规则闭环验收")
    parser.add_argument("--drawing", type=Path, default=root / "data/test_fixtures/case3_polyline_outer.dxf")
    parser.add_argument("--output", type=Path, default=root / "engineering/python/data/ict_single_drawing" / datetime.now().strftime("run_%Y%m%d_%H%M%S_%f"))
    args = parser.parse_args()
    result = run_drawing_feedback(args.drawing, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
