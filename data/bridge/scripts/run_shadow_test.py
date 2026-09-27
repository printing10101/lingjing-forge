"""在 5 个真实风格的 DXF 上跑影子模式，记录 baseline 与 research 的 diff。

运行方法：
    python data/bridge/scripts/run_shadow_test.py [fixtures_dir]

结果落盘：data/bridge/usage_logs/shadow_diff.jsonl
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 让脚本可以 import 顶层包
_REPO_ROOT = Path(__file__).resolve().parents[3]
_PYTHON_ROOT = _REPO_ROOT / "python"
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_PYTHON_ROOT))

logger = logging.getLogger(__name__)


def _baseline_recognize(dxf_path: str) -> dict:
    """Baseline：基于规则的识别（仅返回元信息，不识别倒角）。"""
    import ezdxf

    try:
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        # 简单统计
        return {
            "dxf_version": doc.dxfversion,
            "entity_count": sum(1 for _ in msp),
        }
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def _research_recognize(dxf_path: str) -> dict:
    """Research:调用 IJepa-3D 桥接。

    研究阶段是 stub 模式:返回空 features。
    """
    try:
        # 走桥接层(产品轨 import research_bridge)
        from app.research_bridge import ResearchApiClient
        client = ResearchApiClient.get_instance()
        result = client.call_feature_recognizer(
            input_data={"dxf_path": dxf_path},
            recognizer="ijepa_3d_recognizer",
        )
        return result or {"status": "no_response"}
    except ImportError as e:
        logger.warning(f"Research bridge 模块不可用: {e}。这是研究原型阶段,功能尚未完全集成。")
        return {"status": "stub_mode", "error": str(e), "message": "Research bridge not available"}
    except Exception as e:  # noqa: BLE001
        logger.error(f"Research recognition 失败: {e}", exc_info=True)
        return {"error": repr(e)}


def run(fixtures_dir: str = "data/test_fixtures") -> dict:
    """跑影子模式测试，返回结果汇总。"""
    out_log = Path("data/bridge/usage_logs/shadow_diff.jsonl")
    out_log.parent.mkdir(parents=True, exist_ok=True)

    fixtures = sorted(Path(fixtures_dir).glob("*.dxf"))
    if not fixtures:
        logger.error("未找到 DXF fixtures: %s", fixtures_dir)
        return {"error": "no fixtures"}

    matches = 0
    total = 0
    diffs = []
    for fx in fixtures:
        dxf_path = str(fx)
        logger.info("影子模式: %s", dxf_path)
        # baseline
        t0 = time.perf_counter()
        baseline_out = _baseline_recognize(dxf_path)
        baseline_lat = int((time.perf_counter() - t0) * 1000)
        # research
        t1 = time.perf_counter()
        research_out = _research_recognize(dxf_path)
        research_lat = int((time.perf_counter() - t1) * 1000)
        # diff
        match = baseline_out == research_out
        if match:
            matches += 1
        else:
            diffs.append(
                {
                    "dxf": dxf_path,
                    "baseline": baseline_out,
                    "research": research_out,
                }
            )
        total += 1
        # 写 jsonl
        with open(out_log, "a", encoding="utf-8") as f:
            record = {
                "feature": "shadow_mode.ijepa_3d_recognizer",
                "dxf_path": dxf_path,
                "baseline": baseline_out,
                "research": research_out,
                "match": match,
                "baseline_latency_ms": baseline_lat,
                "research_latency_ms": research_lat,
                "timestamp": datetime.now().isoformat(),
            }
            f.write(json.dumps(record, ensure_ascii=False))
            f.write("\n")

    summary = {
        "total": total,
        "matches": matches,
        "diffs": total - matches,
        "diff_rate": (total - matches) / total if total else 0.0,
        "fixtures_dir": str(fixtures_dir),
    }
    logger.info("影子模式汇总: %s", summary)
    return summary


if __name__ == "__main__":
    fixtures = sys.argv[1] if len(sys.argv) > 1 else "data/test_fixtures"
    summary = run(fixtures)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
