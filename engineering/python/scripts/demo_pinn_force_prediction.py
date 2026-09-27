"""XM-100 PINN 切削力预测展示脚本。

本脚本演示物理信息神经网络（PINN）在 XM-100 桌面五轴加工中心
典型工况下的切削力预测能力，并与经典 Kienzle 解析解进行对比。

展示场景：
    1. 多材料对比：45钢 / 铝合金 / 不锈钢 / 钛合金 在相同工况下的切削力
    2. PINN vs Kienzle：同一工况下两种方法的预测结果对比
    3. 转速扫描：固定进给与切深，扫描转速观察切削力变化趋势
    4. 切深扫描：固定转速与进给，扫描切深观察切削力非线性增长

输出：
    - 控制台对比表格
    - JSON 报告：python/output/xm100_demo/pinn_force_report.json
    - Markdown 报告：python/output/xm100_demo/pinn_force_report.md

注意：
    - PINN 训练范围：speed(500-10000rpm)、feed(100-5000mm/min)、depth(0.1-5.0mm)
    - XM-100 最高 20000 RPM，本展示将转速限制在 10000 RPM 内以匹配 PINN 训练域
    - 若 PINN 检查点未训练，predict_cutting_force() 会自动回退到 Kienzle 解析解
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from typing import Any

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app.simulation.cutting_force.predictor import predict_cutting_force
from app.simulation.cutting_force.kienzle import compute_cutting_forces


# 数据模型


@dataclass
class ForcePrediction:
    """单次切削力预测结果。"""

    material: str
    tool: str
    speed: float
    feed: float
    depth: float
    fx: float
    fy: float
    fz: float
    method: str  # 'pinn' 或 'kienzle'
    elapsed_ms: float


# 展示场景


# 场景1：多材料对比（XM-100 典型工况）
MATERIAL_COMPARISON = [
    ("45steel", "endmill_d10", 6000, 800, 1.0),
    ("aluminum_6061", "endmill_d10", 8000, 1200, 1.5),
    ("stainless_304", "endmill_d10", 5000, 600, 0.8),
    ("titanium_tc4", "endmill_d10", 4000, 400, 0.5),
    ("cast_iron_ht200", "endmill_d10", 6000, 900, 1.2),
    ("copper", "endmill_d10", 7000, 1000, 1.0),
]

# 场景3：转速扫描（45钢，固定进给切深）
SPEED_SWEEP = [(s, 800, 1.0) for s in [2000, 4000, 6000, 8000, 10000]]

# 场景4：切深扫描（45钢，固定转速进给）
DEPTH_SWEEP = [(6000, 800, d) for d in [0.2, 0.5, 1.0, 1.5, 2.0, 3.0]]


# 核心展示逻辑


def run_prediction(
    material: str,
    tool: str,
    speed: float,
    feed: float,
    depth: float,
    use_pinn: bool = True,
) -> ForcePrediction:
    """执行一次切削力预测。"""
    params = {"speed": speed, "feed": feed, "depth": depth}
    start = time.perf_counter()
    result = predict_cutting_force(material=material, tool=tool, params=params, use_pinn=use_pinn)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return ForcePrediction(
        material=material,
        tool=tool,
        speed=speed,
        feed=feed,
        depth=depth,
        fx=result["Fx"],
        fy=result["Fy"],
        fz=result["Fz"],
        method=result["method"],
        elapsed_ms=elapsed_ms,
    )


def run_kienzle_only(
    material: str,
    tool: str,
    speed: float,
    feed: float,
    depth: float,
) -> ForcePrediction:
    """仅使用 Kienzle 解析解（用于对比基准）。"""
    # predict_cutting_force 内部 Kienzle 映射：h = depth*0.1, b = depth
    chip_thickness = max(depth * 0.1, 0.001)
    width = max(depth, 0.01)
    start = time.perf_counter()
    result = compute_cutting_forces(material=material, width=width, chip_thickness=chip_thickness)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return ForcePrediction(
        material=material,
        tool=tool,
        speed=speed,
        feed=feed,
        depth=depth,
        fx=result["Fx"],
        fy=result["Fy"],
        fz=result["Fz"],
        method="kienzle",
        elapsed_ms=elapsed_ms,
    )


def print_table(title: str, rows: list[ForcePrediction]) -> None:
    """打印控制台表格。"""
    print("\n" + "=" * 100)
    print(title)
    print("=" * 100)
    print(
        f"{'材料':<18}{'转速':<8}{'进给':<8}{'切深':<6}{'Fx(N)':<10}{'Fy(N)':<10}"
        f"{'Fz(N)':<10}{'合力(N)':<10}{'方法':<10}{'耗时(ms)':<10}"
    )
    print("-" * 100)
    for r in rows:
        resultant = (r.fx**2 + r.fy**2 + r.fz**2) ** 0.5
        print(
            f"{r.material:<18}{r.speed:<8.0f}{r.feed:<8.0f}{r.depth:<6.2f}"
            f"{r.fx:<10.2f}{r.fy:<10.2f}{r.fz:<10.2f}{resultant:<10.2f}"
            f"{r.method:<10}{r.elapsed_ms:<10.2f}"
        )
    print("=" * 100)


def write_reports(
    material_results: list[ForcePrediction],
    pinn_vs_kienzle: dict[str, list[ForcePrediction]],
    speed_sweep: list[ForcePrediction],
    depth_sweep: list[ForcePrediction],
    output_dir: str,
) -> None:
    """写出 JSON 与 Markdown 报告。"""
    os.makedirs(output_dir, exist_ok=True)

    def to_dict(r: ForcePrediction) -> dict[str, Any]:
        d = asdict(r)
        d["resultant"] = round((r.fx**2 + r.fy**2 + r.fz**2) ** 0.5, 2)
        return d

    report = {
        "title": "XM-100 PINN 切削力预测展示报告",
        "machine": "XM-100 (Xmaker, Fanuc 0i 兼容)",
        "pinn_training_range": {
            "speed_rpm": [500, 10000],
            "feed_mm_min": [100, 5000],
            "depth_mm": [0.1, 5.0],
        },
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scenarios": {
            "material_comparison": [to_dict(r) for r in material_results],
            "pinn_vs_kienzle_45steel": {
                "pinn": [to_dict(r) for r in pinn_vs_kienzle["pinn"]],
                "kienzle": [to_dict(r) for r in pinn_vs_kienzle["kienzle"]],
            },
            "speed_sweep_45steel": [to_dict(r) for r in speed_sweep],
            "depth_sweep_45steel": [to_dict(r) for r in depth_sweep],
        },
    }

    json_path = os.path.join(output_dir, "pinn_force_report.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n[JSON 报告] {json_path}")

    md_path = os.path.join(output_dir, "pinn_force_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# XM-100 PINN 切削力预测展示报告\n\n")
        f.write("- **机床**: XM-100 (Xmaker, Fanuc 0i 兼容)\n")
        f.write(f"- **生成时间**: {report['timestamp']}\n")
        f.write("- **PINN 训练域**: speed 500-10000 RPM, feed 100-5000 mm/min, depth 0.1-5.0 mm\n\n")

        f.write("## 1. 多材料对比（XM-100 典型工况）\n\n")
        f.write("| 材料 | 转速(RPM) | 进给(mm/min) | 切深(mm) | Fx(N) | Fy(N) | Fz(N) | 合力(N) | 方法 |\n")
        f.write("|------|-----------|--------------|----------|-------|-------|-------|---------|------|\n")
        for r in material_results:
            res = (r.fx**2 + r.fy**2 + r.fz**2) ** 0.5
            f.write(
                f"| {r.material} | {r.speed:.0f} | {r.feed:.0f} | {r.depth:.2f} | "
                f"{r.fx:.2f} | {r.fy:.2f} | {r.fz:.2f} | {res:.2f} | {r.method} |\n"
            )

        f.write("\n## 2. PINN vs Kienzle 对比（45钢，6000RPM/800mm/min/1.0mm）\n\n")
        f.write("| 方法 | Fx(N) | Fy(N) | Fz(N) | 合力(N) | 耗时(ms) |\n")
        f.write("|------|-------|-------|-------|---------|----------|\n")
        for method in ["pinn", "kienzle"]:
            for r in pinn_vs_kienzle[method]:
                res = (r.fx**2 + r.fy**2 + r.fz**2) ** 0.5
                f.write(f"| {r.method} | {r.fx:.2f} | {r.fy:.2f} | {r.fz:.2f} | {res:.2f} | {r.elapsed_ms:.3f} |\n")

        f.write("\n## 3. 转速扫描（45钢，进给800mm/min，切深1.0mm）\n\n")
        f.write("| 转速(RPM) | Fx(N) | Fy(N) | Fz(N) | 合力(N) | 方法 |\n")
        f.write("|-----------|-------|-------|-------|---------|------|\n")
        for r in speed_sweep:
            res = (r.fx**2 + r.fy**2 + r.fz**2) ** 0.5
            f.write(f"| {r.speed:.0f} | {r.fx:.2f} | {r.fy:.2f} | {r.fz:.2f} | {res:.2f} | {r.method} |\n")

        f.write("\n## 4. 切深扫描（45钢，转速6000RPM，进给800mm/min）\n\n")
        f.write("| 切深(mm) | Fx(N) | Fy(N) | Fz(N) | 合力(N) | 方法 |\n")
        f.write("|----------|-------|-------|-------|---------|------|\n")
        for r in depth_sweep:
            res = (r.fx**2 + r.fy**2 + r.fz**2) ** 0.5
            f.write(f"| {r.depth:.2f} | {r.fx:.2f} | {r.fy:.2f} | {r.fz:.2f} | {res:.2f} | {r.method} |\n")

        f.write("\n## 说明\n\n")
        f.write("- **PINN**: 物理信息神经网络，3→64→64→32→3 残差学习架构\n")
        f.write("- **Kienzle**: 经典解析解 Fz = kc1.1 × b × h^(1-mc)\n")
        f.write("- 当 PINN 检查点未训练时，predict_cutting_force() 自动回退到 Kienzle\n")
        f.write("- 合力 = √(Fx² + Fy² + Fz²)\n")
    print(f"[Markdown 报告] {md_path}")


def main() -> int:
    print("=" * 60)
    print("XM-100 PINN 切削力预测展示")
    print("=" * 60)

    # 场景1：多材料对比
    print("\n[1/4] 多材料对比（XM-100 典型工况）...")
    material_results: list[ForcePrediction] = []
    for mat, tool, speed, feed, depth in MATERIAL_COMPARISON:
        r = run_prediction(mat, tool, speed, feed, depth, use_pinn=True)
        material_results.append(r)
        print(f"    ✓ {mat}: Fz={r.fz:.2f}N ({r.method})")
    print_table("场景1：多材料切削力对比", material_results)

    # 场景2：PINN vs Kienzle
    print("\n[2/4] PINN vs Kienzle 对比（45钢标准工况）...")
    pinn_vs_kienzle: dict[str, list[ForcePrediction]] = {"pinn": [], "kienzle": []}
    pinn_r = run_prediction("45steel", "endmill_d10", 6000, 800, 1.0, use_pinn=True)
    kienzle_r = run_kienzle_only("45steel", "endmill_d10", 6000, 800, 1.0)
    pinn_vs_kienzle["pinn"].append(pinn_r)
    pinn_vs_kienzle["kienzle"].append(kienzle_r)
    print(f"    ✓ PINN:    Fz={pinn_r.fz:.2f}N ({pinn_r.method})")
    print(f"    ✓ Kienzle: Fz={kienzle_r.fz:.2f}N ({kienzle_r.method})")

    # 场景3：转速扫描
    print("\n[3/4] 转速扫描（45钢，800mm/min，1.0mm）...")
    speed_sweep: list[ForcePrediction] = []
    for speed, feed, depth in SPEED_SWEEP:
        r = run_prediction("45steel", "endmill_d10", speed, feed, depth, use_pinn=True)
        speed_sweep.append(r)
        print(f"    ✓ {speed}RPM: Fz={r.fz:.2f}N")
    print_table("场景3：转速扫描（45钢）", speed_sweep)

    # 场景4：切深扫描
    print("\n[4/4] 切深扫描（45钢，6000RPM，800mm/min）...")
    depth_sweep: list[ForcePrediction] = []
    for speed, feed, depth in DEPTH_SWEEP:
        r = run_prediction("45steel", "endmill_d10", speed, feed, depth, use_pinn=True)
        depth_sweep.append(r)
        print(f"    ✓ depth={depth}mm: Fz={r.fz:.2f}N")
    print_table("场景4：切深扫描（45钢）", depth_sweep)

    # 生成报告
    print("\n生成报告...")
    output_dir = os.path.join(_PROJECT_ROOT, "output", "xm100_demo")
    write_reports(material_results, pinn_vs_kienzle, speed_sweep, depth_sweep, output_dir)

    print("\n展示完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
