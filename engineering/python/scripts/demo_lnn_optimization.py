"""XM-100 LNN 切削参数优化对比展示脚本。

本脚本演示如何使用 LNN（液态神经网络）引擎对 XM-100 桌面五轴加工中心
在不同切削参数方案下的加工效果进行预测对比，为工艺优化提供决策支持。

展示场景：
    针对 45 钢 / φ10 立铣刀 的方肩铣削工序，对比三种切削参数方案：
    - 保守方案：低转速、低进给、小切深（追求表面质量与刀具寿命）
    - 标准方案：中等参数（平衡效率与质量，XM-100 推荐工况）
    - 激进方案：高转速、高进给、大切深（追求材料去除率）

    使用 LNN 引擎对每种方案预测主切削力，并给出综合评估。

输出：
    - 控制台对比表格
    - JSON 报告：python/output/xm100_demo/lnn_optimization_report.json
    - Markdown 报告：python/output/xm100_demo/lnn_optimization_report.md

注意：
    - XM-100 主轴最高 20000 RPM，但 LNN 预定义 cutting_force 模型
      输入特征为 {force_x, force_y, force_z, spindle_speed, feed_rate}，
      本展示使用模型对三种方案进行相对比较。
    - 若模型权重未训练，将使用初始化权重，结果仅作相对趋势参考。
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from typing import Any

# 确保可以导入 app 包
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from app.ai.lnn.inference.predictor import LNNPredictor
from app.ai.lnn.inference.registry import LNNModelRegistry


# 数据模型


@dataclass
class CuttingScenario:
    """切削方案定义。"""

    name: str
    description: str
    spindle_speed: float  # RPM
    feed_rate: float  # mm/min
    depth_of_cut: float  # mm
    # 用于 LNN cutting_force 模型的输入（force_x/y/z 用经验比例预估）
    force_x_hint: float
    force_y_hint: float
    force_z_hint: float


@dataclass
class ScenarioResult:
    """单方案预测结果。"""

    scenario: CuttingScenario
    predicted_force: Any
    inference_time_ms: float
    confidence: float
    error: str | None = None


# 三种切削方案（基于 XM-100 能力与 45 钢加工经验）


SCENARIOS: list[CuttingScenario] = [
    CuttingScenario(
        name="conservative",
        description="保守方案：低转速、低进给、小切深 — 追求表面质量与刀具寿命",
        spindle_speed=3000,
        feed_rate=300,
        depth_of_cut=0.5,
        force_x_hint=45.0,
        force_y_hint=60.0,
        force_z_hint=150.0,
    ),
    CuttingScenario(
        name="standard",
        description="标准方案：中等参数 — XM-100 推荐工况，平衡效率与质量",
        spindle_speed=6000,
        feed_rate=800,
        depth_of_cut=1.0,
        force_x_hint=80.0,
        force_y_hint=110.0,
        force_z_hint=280.0,
    ),
    CuttingScenario(
        name="aggressive",
        description="激进方案：高转速、高进给、大切深 — 追求材料去除率",
        spindle_speed=10000,
        feed_rate=1500,
        depth_of_cut=2.0,
        force_x_hint=140.0,
        force_y_hint=190.0,
        force_z_hint=480.0,
    ),
]


# 核心展示逻辑


def build_predictor() -> LNNPredictor:
    """从注册表构建 cutting_force 预测器。"""
    registry = LNNModelRegistry()
    predictor = LNNPredictor.from_registry(
        registry=registry,
        model_name="cutting_force",
        use_amp=False,
        auto_device=True,
    )
    return predictor


def run_scenario(predictor: LNNPredictor, scenario: CuttingScenario) -> ScenarioResult:
    """对单个方案执行 LNN 推理。"""
    # LNN cutting_force 模型输入特征顺序：
    # [force_x, force_y, force_z, spindle_speed, feed_rate]
    input_data = {
        "force_x": scenario.force_x_hint,
        "force_y": scenario.force_y_hint,
        "force_z": scenario.force_z_hint,
        "spindle_speed": scenario.spindle_speed,
        "feed_rate": scenario.feed_rate,
    }

    start = time.perf_counter()
    try:
        result = predictor.predict(input_data, return_confidence=True)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # result 是 PredictionResult
        value = result.value
        confidence = float(result.confidence)
        return ScenarioResult(
            scenario=scenario,
            predicted_force=value.tolist() if hasattr(value, "tolist") else value,
            inference_time_ms=elapsed_ms,
            confidence=confidence,
        )
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - start) * 1000
        return ScenarioResult(
            scenario=scenario,
            predicted_force=None,
            inference_time_ms=elapsed_ms,
            confidence=0.0,
            error=str(exc),
        )


def evaluate_scenario(result: ScenarioResult) -> dict[str, Any]:
    """对单方案结果做综合评估。"""
    s = result.scenario
    # 材料去除率 MRR = width * depth * feed_per_tooth * N_teeth * spindle_speed
    # 简化：使用 feed_rate * depth_of_cut 作为相对 MRR 指标 (mm^2/min)
    mrr_relative = s.feed_rate * s.depth_of_cut

    # 预测力（若可用）
    pred_force = result.predicted_force
    if isinstance(pred_force, list) and pred_force:
        # cutting_force 模型输出 predicted_cutting_force
        if isinstance(pred_force[0], list):
            pred_value = float(pred_force[0][0])
        else:
            pred_value = float(pred_force[0])
        # 模型权重未训练时可能输出负值，取绝对值作为相对力大小参考
        pred_value = abs(pred_value)
    else:
        pred_value = None

    # 综合评分（启发式）：
    # - MRR 越高越好（效率）
    # - 预测力越低越好（刀具寿命、表面质量）
    # - 激进方案受 XM-100 主轴功率 2.2kW 限制
    efficiency_score = min(mrr_relative / 3000.0, 1.0) * 40.0  # 满分 40
    if pred_value is not None and pred_value > 0:
        force_score = max(40.0 - (pred_value / 20.0), 0.0)  # 满分 40
    else:
        force_score = 20.0  # 无预测力时给中等分
    # XM-100 约束分：转速接近上限扣分
    constraint_score = 20.0 - max(0.0, (s.spindle_speed - 8000) / 200.0)
    constraint_score = max(constraint_score, 0.0)

    total = efficiency_score + force_score + constraint_score
    return {
        "mrr_relative": round(mrr_relative, 2),
        "predicted_force": pred_value,
        "efficiency_score": round(efficiency_score, 2),
        "force_score": round(force_score, 2),
        "constraint_score": round(constraint_score, 2),
        "total_score": round(total, 2),
    }


def print_comparison_table(results: list[ScenarioResult]) -> None:
    """打印控制台对比表。"""
    print("\n" + "=" * 92)
    print("XM-100 LNN 切削参数优化对比（45 钢 / φ10 立铣刀 / 方肩铣削）")
    print("=" * 92)
    print(
        f"{'方案':<14}{'转速(RPM)':<12}{'进给(mm/min)':<14}{'切深(mm)':<10}{'预测力':<12}{'推理(ms)':<10}{'综合分':<10}"
    )
    print("-" * 92)
    for r in results:
        s = r.scenario
        eval_ = evaluate_scenario(r)
        pred_str = f"{eval_['predicted_force']:.2f}" if eval_["predicted_force"] is not None else "N/A"
        print(
            f"{s.name:<14}{s.spindle_speed:<12}{s.feed_rate:<14}{s.depth_of_cut:<10}"
            f"{pred_str:<12}{r.inference_time_ms:<10.2f}{eval_['total_score']:<10.2f}"
        )
    print("=" * 92)
    print("评分维度：效率(40) + 切削力(40) + XM-100约束(20) = 100")
    print("注：预测力为 LNN cutting_force 模型输出，相对值，用于方案对比。")


def write_reports(results: list[ScenarioResult], output_dir: str) -> None:
    """写出 JSON 与 Markdown 报告。"""
    os.makedirs(output_dir, exist_ok=True)

    # JSON 报告
    report_data = {
        "title": "XM-100 LNN 切削参数优化对比报告",
        "machine": "XM-100 (Xmaker, Fanuc 0i 兼容)",
        "workpiece": "45 steel",
        "tool": "endmill_d10 (φ10 立铣刀)",
        "operation": "shoulder_milling",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scenarios": [],
    }
    for r in results:
        eval_ = evaluate_scenario(r)
        report_data["scenarios"].append(
            {
                "scenario": asdict(r.scenario),
                "predicted_force": eval_["predicted_force"],
                "inference_time_ms": round(r.inference_time_ms, 3),
                "confidence": round(r.confidence, 4),
                "evaluation": eval_,
                "error": r.error,
            }
        )

    json_path = os.path.join(output_dir, "lnn_optimization_report.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)
    print(f"\n[JSON 报告] {json_path}")

    # Markdown 报告
    md_path = os.path.join(output_dir, "lnn_optimization_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# XM-100 LNN 切削参数优化对比报告\n\n")
        f.write("- **机床**: XM-100 (Xmaker, Fanuc 0i 兼容)\n")
        f.write("- **工件**: 45 steel\n")
        f.write("- **刀具**: φ10 立铣刀\n")
        f.write("- **工序**: 方肩铣削\n")
        f.write(f"- **生成时间**: {report_data['timestamp']}\n\n")
        f.write("## 方案对比\n\n")
        f.write("| 方案 | 描述 | 转速(RPM) | 进给(mm/min) | 切深(mm) | 预测力 | 综合分 |\n")
        f.write("|------|------|-----------|--------------|----------|--------|--------|\n")
        for r in results:
            s = r.scenario
            eval_ = evaluate_scenario(r)
            pred_str = f"{eval_['predicted_force']:.2f}" if eval_["predicted_force"] is not None else "N/A"
            f.write(
                f"| {s.name} | {s.description} | {s.spindle_speed} | {s.feed_rate} | "
                f"{s.depth_of_cut} | {pred_str} | {eval_['total_score']:.2f} |\n"
            )
        f.write("\n## 评分维度\n\n")
        f.write("- 效率分 (40): 基于相对材料去除率 MRR = feed × depth\n")
        f.write("- 切削力分 (40): 预测力越低分越高（刀具寿命、表面质量）\n")
        f.write("- XM-100 约束分 (20): 转速接近 20000 RPM 上限扣分\n\n")
        f.write("## 详细评估\n\n")
        for r in results:
            s = r.scenario
            eval_ = evaluate_scenario(r)
            f.write(f"### {s.name}\n\n")
            f.write(f"- **描述**: {s.description}\n")
            f.write(f"- **转速**: {s.spindle_speed} RPM\n")
            f.write(f"- **进给**: {s.feed_rate} mm/min\n")
            f.write(f"- **切深**: {s.depth_of_cut} mm\n")
            f.write(f"- **相对 MRR**: {eval_['mrr_relative']}\n")
            f.write(f"- **预测力**: {eval_['predicted_force'] if eval_['predicted_force'] is not None else 'N/A'}\n")
            f.write(f"- **推理耗时**: {r.inference_time_ms:.3f} ms\n")
            f.write(f"- **置信度**: {r.confidence:.4f}\n")
            f.write(f"- **综合评分**: {eval_['total_score']:.2f} / 100\n")
            if r.error:
                f.write(f"- **错误**: {r.error}\n")
            f.write("\n")
    print(f"[Markdown 报告] {md_path}")


def main() -> int:
    print("=" * 60)
    print("XM-100 LNN 切削参数优化对比展示")
    print("=" * 60)

    print("\n[1/4] 构建 LNN 预测器（cutting_force 模型）...")
    try:
        predictor = build_predictor()
        print(f"    ✓ 预测器已就绪，设备: {predictor.device}")
    except Exception as exc:  # noqa: BLE001
        print(f"    ✗ 构建预测器失败: {exc}")
        return 1

    print(f"\n[2/4] 加载 {len(SCENARIOS)} 种切削方案...")
    for s in SCENARIOS:
        print(f"    - {s.name}: {s.description}")

    print("\n[3/4] 执行 LNN 推理...")
    results: list[ScenarioResult] = []
    for s in SCENARIOS:
        print(f"    推理中: {s.name}...")
        r = run_scenario(predictor, s)
        if r.error:
            print(f"    ✗ {s.name} 失败: {r.error}")
        else:
            print(f"    ✓ {s.name} 完成，耗时 {r.inference_time_ms:.2f} ms，置信度 {r.confidence:.4f}")
        results.append(r)

    print("\n[4/4] 生成对比报告...")
    print_comparison_table(results)

    output_dir = os.path.join(_PROJECT_ROOT, "output", "xm100_demo")
    write_reports(results, output_dir)

    # 推荐方案
    best = max(results, key=lambda r: evaluate_scenario(r)["total_score"])
    print(f"\n[推荐方案] {best.scenario.name}")
    print(f"    {best.scenario.description}")
    print(f"    综合评分: {evaluate_scenario(best)['total_score']:.2f} / 100")

    print("\n展示完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
