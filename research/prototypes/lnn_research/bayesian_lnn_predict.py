"""Bayesian-LNN 推理桥接：被 research_api_client 通过子进程调用。

输入：JSON 序列化的预测输入
输出：JSON 序列化的预测结果（含 mean、std、samples）
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any


def predict_with_uncertainty(payload: dict) -> dict:
    """子进程入口函数。"""
    t0 = time.perf_counter()
    try:
        result = _do_predict(payload)
        result["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        return result
    except Exception as e:  # noqa: BLE001
        return {
            "status": "error",
            "error_message": repr(e),
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }


def _do_predict(payload: dict) -> dict:
    """实际预测（占位实现，研究阶段会被替换为 Bayesian-LNN 推理）。"""
    # 研究阶段：返回零预测
    return {
        "status": "ok",
        "mean": 0.0,
        "std": 0.0,
        "samples": [0.0],
        "recognizer_name": "bayesian_lnn_stub",
        "recognizer_version": "0.1.0",
    }


if __name__ == "__main__":
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(predict_with_uncertainty(payload), ensure_ascii=False))
