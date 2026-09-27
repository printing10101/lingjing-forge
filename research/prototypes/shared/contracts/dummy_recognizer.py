"""兜底识别器：当研究模块不可用时使用。

避免 research_api_client 在研究模块缺失时崩溃。
"""
from __future__ import annotations

import json
import sys
from typing import Any


def recognize(payload: dict) -> dict:
    """永远返回空 features。"""
    return {
        "status": "ok",
        "features": [],
        "overall_confidence": 0.0,
        "recognizer_name": "dummy_recognizer",
        "recognizer_version": "1.0.0",
    }


if __name__ == "__main__":
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(recognize(payload), ensure_ascii=False))
