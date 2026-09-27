"""CI流程中的几何精度验证运行脚本。

供 .github/workflows/geometry-validation.yml 调用，
执行完整的验证流程并输出汇总结果。
"""
# 2026-06-11: 触发工作流重跑（手动 re-run 的替代方式）

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.validation import BenchmarkDataset, GeometricValidator  # noqa: E402


def main():
    start = time.perf_counter()

    report_file = Path(__file__).resolve()
    python_dir = report_file.parent.parent.parent
    reports_dir = python_dir / "app" / "validation" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    dataset = BenchmarkDataset()
    validator = GeometricValidator(dataset=dataset)

    parts = dataset.list_parts()
    parts = [p for p in parts if not p.startswith("test_")]
    if not parts:
        print("未找到基准零件数据集，跳过验证")
        sys.exit(0)

    print(f"发现 {len(parts)} 个基准零件: {parts}")

    summary: dict[str, dict] = {}
    all_passed = True

    for part_id in parts:
        t0 = time.perf_counter()
        print(f"\n验证零件: {part_id} ...")

        try:
            # P1 学术诚信修复：CI 验证脚本显式传 allow_mock_fallback=True。
            # 当前 CI 环境无真实 3D 重建模型，使用 mock 数据生成验证报告用于流水线冒烟测试。
            # 生产环境或学术论文实验必须替换为真实 reconstructed_model。
            report = validator.validate_reconstruction(
                part_id, allow_mock_fallback=True
            )

            report_path = reports_dir / f"validation_{part_id}.html"
            html = validator.generate_report(report, str(report_path))
            print(f"  报告已生成: {report_path} ({len(html)} 字符)")

            json_path = reports_dir / f"validation_{part_id}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                f.write(report.to_json())

            elapsed = time.perf_counter() - t0
            print(f"  耗时: {elapsed:.2f}s | 通过: {report.overall_pass}")

            if not report.overall_pass:
                all_passed = False
                for w in report.warnings:
                    print(f"  警告: {w}")

            summary[part_id] = {
                "overall_pass": report.overall_pass,
                "feature_recall": report.metrics.feature_recall,
                "feature_precision": report.metrics.feature_precision,
                "dimension_accuracy": report.metrics.dimension_accuracy,
                "tolerance_compliance": report.metrics.tolerance_compliance,
                "topology_correctness": report.metrics.topology_correctness,
                "duration_seconds": round(elapsed, 2),
                "warnings": report.warnings,
            }
        except Exception as exc:
            print(f"  错误: {exc}")
            summary[part_id] = {"error": str(exc)}
            all_passed = False

    total = time.perf_counter() - start
    print(f"\n总耗时: {total:.2f}s")

    summary_path = reports_dir / "ci_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"汇总已保存: {summary_path}")

    if not all_passed:
        sys.exit(1)
    print("所有零件验证通过")


if __name__ == "__main__":
    main()
