#!/usr/bin/env python3
"""重新生成 E2E NC 基准语料（`data/outputs/e2e_v2/`）。

为什么需要这个脚本（2026-10-07 全库定级发现）：
    旧语料 160 个 `.nc`（8 控制器 × 20 fixture）**一条切削指令都没有**，
    只有程序头 + `M03/M08` + 程序尾（日期 2026-06-18），但同目录的
    `e2e_v2_summary.json` 给每个案例都记了 `gcode_ok: true`。
    那是阶段 6 的 `_run_gcode` 还只写 header/footer 占位时期的产物——
    当时"端到端成功"只等于"没抛异常"。旧语料已连同旧 summary 一起
    归档到 `docs/archive/zero_cutting_evidence_20260618/`。

    没有可复现生成器，语料就会继续以"看起来通过"的形态存在。
    本脚本用**当前主路径**（`DxfProcessService`）重新生成，并对每个产物
    现场计数切削指令：任何一个文件没有切削运动即报错退出（exit 1），
    避免同一类空壳再次进仓。

用法：
    # 用 desktop_runtime（推荐，与后端一致）
    engineering/python/desktop_runtime/runtime/python.exe scripts/regenerate_e2e_nc_corpus.py
    # 或用系统 Python 3.14
    C:/Users/<user>/AppData/Local/Programs/Python/Python314/python.exe scripts/regenerate_e2e_nc_corpus.py

产出：
    data/outputs/e2e_v2/<controller>/<case>/<case>.<controller>.nc
    data/outputs/e2e_v2/e2e_v2_summary.json   （含切削指令数、钻孔循环数、
                                               刀轨策略、耗时；gcode_ok 现在
                                               要求"确有切削运动"）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "engineering" / "python"
sys.path.insert(0, str(BACKEND_ROOT))

FIXTURE_DIR = REPO_ROOT / "data" / "test_fixtures"
OUT_ROOT = REPO_ROOT / "data" / "outputs" / "e2e_v2"

# 与内置后处理器注册表一致的 8 个控制器方言
CONTROLLERS = [
    "fanuc_0i",
    "siemens_840d",
    "heidenhain_tnc",
    "gsk_980_25i",
    "hnc_848_22",
    "knd_1000_2000_3000",
    "mitsubishi_m70_m80",
    "fagor_8055",
]

# 切削运动 = 进给直线/圆弧（G01/G02/G03 带轴地址）；钻孔循环单独计数。
# 方言写法不统一：Heidenhain 用 `L  X+.. Y+.. F137.5`（快移是同一 L 字但 FMAX），
# 圆弧是 CC/CR；固定循环是 CYCL DEF/CYCL CALL。只用 `^G0?[123]` 计数会把
# Heidenhain 的正确产物误判成"零切削空壳"，因此按方言分支。
CUTTING_RE = re.compile(r"^\s*G0?[123]\s+[XYZ]", re.M)
# 钻孔循环：Fanuc 族用 G73/G81/G83/G82/G89；Siemens 用 CYCLE81/CYCLE83 等固定循环
# 只数 G73/G81 会把 Siemens 的 25 个孔计成 0 个——错数据比没数据更糟。
DRILL_RE = re.compile(r"\bG(?:73|81|83|82|89)\b|\bCYCLE\d+\b")
# Heidenhain 的切削行有两种形态：带块号（`2  L  Z+80.000 R0 FMAX`）和不带块号
# （`L  X+95.000 Y+5.000 Z+48.000 R0 F137.500`）。快移同样是 L，但进给写作 FMAX，
# 所以用「有 F+数字」区分切削段与快移段。
HEIDEN_CUT_RE = re.compile(r"^\s*(?:\d+\s+)?(?:L|CC|CR)\s[^\n]*\bF\d", re.M)
HEIDEN_DRILL_RE = re.compile(r"\bCYCL CALL\b")
STRATEGY_RE = re.compile(r"刀轨策略: ([a-z_]+)")


def _count_metrics(text: str, controller_id: str) -> dict[str, Any]:
    heiden = controller_id.startswith("heidenhain")
    cutting = len(HEIDEN_CUT_RE.findall(text)) if heiden else len(CUTTING_RE.findall(text))
    drills = len(HEIDEN_DRILL_RE.findall(text)) if heiden else len(DRILL_RE.findall(text))
    return {
        "lines": len(text.splitlines()),
        "cutting_moves": cutting,
        "drill_cycles": drills,
        "strategies": sorted(set(STRATEGY_RE.findall(text))),
    }


def regenerate(controllers: list[str], cases: list[str] | None) -> tuple[int, list[str]]:
    """生成语料，返回 (成功文件数, 失败说明列表)。"""
    from app.dxf.process_service import DxfProcessService

    fixtures = sorted(p for p in FIXTURE_DIR.glob("*.dxf") if not cases or p.stem in cases)
    if not fixtures:
        raise SystemExit(f"未找到 fixture: {FIXTURE_DIR}")

    summary: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "generator": "scripts/regenerate_e2e_nc_corpus.py",
        "fixture_count": len(fixtures),
        "postprocessor_count": len(controllers),
        "postprocessors": controllers,
        "fixtures": {},
    }
    failures: list[str] = []
    written = 0

    for fx in fixtures:
        per_controller: dict[str, Any] = {}
        for controller in controllers:
            out_dir = OUT_ROOT / controller / fx.stem
            out_dir.mkdir(parents=True, exist_ok=True)
            t0 = time.time()
            service = DxfProcessService()
            result = service.process(dxf_path=fx, output_dir=out_dir, postprocessor=controller)
            nc_path = out_dir / f"{fx.stem}.{controller}.nc"
            ok = bool(result.gcode and result.gcode.success and nc_path.exists())
            entry: dict[str, Any] = {
                "success": ok,
                "parse_ok": bool(result.parse.success),
                "features_ok": bool(result.features.success),
                "model3d_ok": bool(result.model3d.success) if result.model3d else None,
                "gcode_ok": ok,
                "total_latency_ms": round((time.time() - t0) * 1000, 2),
            }
            if ok:
                metrics = _count_metrics(nc_path.read_text(encoding="utf-8"), controller)
                entry.update(metrics)
                # 关键门禁：gcode_ok 不再等于"没抛异常"，必须有切削运动
                if metrics["cutting_moves"] == 0:
                    entry["gcode_ok"] = False
                    ok = False
                    failures.append(f"{controller}/{fx.stem}: 零切削空壳（{metrics['lines']} 行）")
                written += 1
            else:
                failures.append(f"{controller}/{fx.stem}: {(result.gcode.error if result.gcode else '无 gcode 阶段')}")
            per_controller[controller] = entry
        summary["fixtures"][fx.name] = {"results_by_controller": per_controller}

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    (OUT_ROOT / "e2e_v2_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return written, failures


def main() -> int:
    parser = argparse.ArgumentParser(description="重新生成 E2E NC 基准语料")
    parser.add_argument("--controllers", nargs="*", default=CONTROLLERS, help="控制器方言列表")
    parser.add_argument("--cases", nargs="*", default=None, help="只生成指定 fixture 名（默认全部）")
    args = parser.parse_args()

    written, failures = regenerate(list(args.controllers), args.cases or None)
    total_files = len(list(OUT_ROOT.glob("*/*/*.nc")))
    print(f"生成 NC 文件: {written} 个（目录内共 {total_files} 个 .nc）")
    print(f"失败/空壳: {len(failures)}")
    for f in failures[:20]:
        print(f"  - {f}")
    print(f"summary: {OUT_ROOT / 'e2e_v2_summary.json'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
