"""IJepa-3D 推理桥接：被 research_api_client 通过子进程调用。

输入：JSON 序列化的 RecognitionInput
输出：JSON 序列化的 RecognitionResult
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from typing import Any

# 兼容子进程调用路径
try:
    from shared.contracts.feature_recognizer import (
        RecognitionInput,
        RecognitionResult,
        RecognizedFeature,
        FeatureType,
    )
    from multimodal_jepa.ijepa_3d import chamfer_heuristic
except ImportError:
    # 当作为脚本直接运行时
    _here = os.path.dirname(os.path.abspath(__file__))
    _root = os.path.dirname(os.path.dirname(os.path.dirname(_here)))
    sys.path.insert(0, _root)
    from shared.contracts.feature_recognizer import (
        RecognitionInput,
        RecognitionResult,
        RecognizedFeature,
        FeatureType,
    )
    from multimodal_jepa.ijepa_3d import chamfer_heuristic


def recognize(payload: dict) -> dict:
    """子进程入口函数。"""
    t0 = time.perf_counter()
    try:
        inp = RecognitionInput(
            dxf_path=payload.get("dxf_path"),
            image_paths=payload.get("image_paths"),
            file_hash=payload.get("file_hash"),
            extra=payload.get("extra", {}),
        )
        result = _do_recognize(inp)
        result.latency_ms = int((time.perf_counter() - t0) * 1000)
        return result.to_dict()
    except Exception as e:  # noqa: BLE001
        return RecognitionResult(
            status="error",
            error_message=repr(e),
            recognizer_name="ijepa_3d_recognizer",
            recognizer_version="0.2.0",
            latency_ms=int((time.perf_counter() - t0) * 1000),
        ).to_dict()


def _do_recognize(inp: RecognitionInput) -> RecognitionResult:
    """实际识别逻辑：先跑启发式（DXF 可用时），未来接入 IJepa-3D 模型。

    当前阶段产物：
    - 如果 dxf_path 可用：用启发式推断 chamfer / fillet / step / slot
    - 如果 image_paths 可用：占位（未来由 IJepa-3D 模型接管）
    """
    features: list[RecognizedFeature] = []

    if inp.dxf_path:
        try:
            # 解析 DXF 拿到几何
            from app.dxf.dxf_parser import DxfParser  # type: ignore

            parsed = DxfParser().parse(inp.dxf_path)
            features.extend(chamfer_heuristic.detect_all(parsed))
        except Exception as e:  # noqa: BLE001
            return RecognitionResult(
                status="error",
                features=[],
                error_message=f"ijepa3d_bridge_dxf_failed: {e}",
                recognizer_name="ijepa_3d_recognizer",
                recognizer_version="0.2.0",
            )

    if inp.image_paths:
        # 占位：未来用 IJEPA3DInference 跑三视图，识别 chamfer / fillet 等
        # 当前阶段在 extra.image_only 模式时不报 error，只是不输出任何 features
        pass

    # 计算 overall_confidence
    if features:
        overall_conf = sum(f.confidence for f in features) / len(features)
    else:
        overall_conf = 0.0

    return RecognitionResult(
        status="ok" if features else "no_features",
        features=features,
        overall_confidence=round(overall_conf, 4),
        recognizer_name="ijepa_3d_chamfer_recognizer",
        recognizer_version="0.2.0",
    )


if __name__ == "__main__":
    payload = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    print(json.dumps(recognize(payload), ensure_ascii=False))
