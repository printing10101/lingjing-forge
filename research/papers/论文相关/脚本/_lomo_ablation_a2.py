"""LOMO A2 消融实验 —— 验证 L_pcc 在跨材料泛化上的贡献。

背景：
    合成数据消融实验（ablation_smoke/）显示 A2（λ₃=0, PINN）在 in-distribution 上
    略优于 Full（MAE 0.2258 vs 0.2338）。这不符合论文 L_pcc 提升性能的声明。

    本脚本在 LOMO（Leave-One-Material-Out, OOD）协议下对比 Full vs A2，
    验证 L_pcc 的真正价值在于提升 OOD 泛化能力，而非 in-distribution 拟合。

设计：
    - 复用 lomo_loco_experiment.py 的 LomoLocoDataset / train_and_evaluate
    - 通过覆盖 config.model.lambda_pcc = 0.0 实现 A2 配置
    - 同样的 5 fold LOMO 协议，仅 λ₃ 不同
    - 输出到 results/lomo_a2/，与 Full 的 results/lomo_loco/ 对比

运行方式：
    python _lomo_ablation_a2.py

预期结果：
    - 若 L_pcc 有效：A2 的 LOMO MAE > Full 的 LOMO MAE（L_pcc 改善 OOD）
    - 若 L_pcc 无效：A2 ≈ Full 或 A2 < Full（需重新审视论文声明）
"""

import sys
import os
import types
import json
import time
from pathlib import Path

# WinSock 损坏绕过补丁
try:
    import _overlapped  # noqa: F401
except OSError:
    _patch = types.ModuleType("_overlapped")
    _patch.Overlapped = type("Overlapped", (), {})
    sys.modules["_overlapped"] = _patch
    print("[warn] _overlapped 模块加载失败，已注入空实现绕过 WinSock 损坏。")

import numpy as np
import torch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_DIR = PROJECT_ROOT / "python"
EXPERIMENTS_DIR = PYTHON_DIR / "experiments"
SCRIPTS_DIR = PROJECT_ROOT / "论文相关" / "脚本"
sys.path.insert(0, str(PYTHON_DIR))
sys.path.insert(0, str(EXPERIMENTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

# 复用 LOMO 实验模块
from training.reproducibility import set_global_seed
from experiments.config import get_config
from lomo_loco_experiment import (
    LomoLocoDataset,
    run_lomo_experiment,
    save_summary_csv,
    save_report_md,
)


def main():
    # 参数与 Full LOMO 一致
    samples_per_group = 150
    stage1_epochs = 30
    stage2_epochs = 50
    baseline_epochs = 150  # 不用于 DL-LNN
    seed = 42
    output_dir = "论文相关/脚本/results/lomo_a2"

    os.makedirs(output_dir, exist_ok=True)

    print("=" * 70)
    print("LOMO A2 消融实验（λ₃=0，验证 L_pcc 在 OOD 上的贡献）")
    print("=" * 70)
    print(f"参数: samples_per_group={samples_per_group}, stage1={stage1_epochs}, stage2={stage2_epochs}")
    print(f"配置: lambda_phys=0.5, lambda_pcc=0.0 (A2 = PINN)")
    print(f"输出: {output_dir}")
    print("=" * 70)

    # 构造数据集（与 Full 相同的种子和参数）
    print("\n[1/3] 构造 LOMO 数据集...")
    dataset = LomoLocoDataset(samples_per_group=samples_per_group, seed=seed)
    print(f"  总样本: {len(dataset)}, 材料: {len(dataset.materials)}, 工况: {len(dataset.conditions)}")

    # 获取配置并覆盖 lambda_pcc = 0.0（A2 配置）
    print("\n[2/3] 配置 A2 消融（λ₃=0）...")
    config = get_config("lomo_loco_experiment")
    config.model.device = "cuda" if torch.cuda.is_available() else "cpu"
    config.model.lambda_pcc = 0.0  # A2: 去除 L_pcc
    config.model.lambda_phys = 0.5  # 保留 L_phys
    print(f"  lambda_phys={config.model.lambda_phys}, lambda_pcc={config.model.lambda_pcc}")
    print(f"  device={config.model.device}")

    # 运行 LOMO 实验
    print("\n[3/3] 运行 LOMO A2 实验...")
    t0 = time.time()
    result = run_lomo_experiment(
        model_name="DL-LNN",
        dataset=dataset,
        config=config,
        seed=seed,
        stage1_epochs=stage1_epochs,
        stage2_epochs=stage2_epochs,
        baseline_epochs=baseline_epochs,
        output_dir=output_dir,  # 方案 A：启用 fold 级 checkpoint，崩溃可续跑
    )
    elapsed = time.time() - t0

    # 保存结果
    output_file = os.path.join(output_dir, "lomo_a2_results.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({"A2": result}, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n[OK] 结果已保存至: {output_file}")

    # 保存 CSV 和 MD
    csv_path = save_summary_csv({"A2": result}, "LOMO", output_dir)
    md_path = save_report_md({"A2": result}, "LOMO", output_dir, "synthetic_multi")
    print(f"[OK] CSV: {csv_path}")
    print(f"[OK] MD: {md_path}")

    # 打印汇总
    print("\n" + "=" * 70)
    print("LOMO A2 汇总结果")
    print("=" * 70)
    s = result.get("summary", {})
    print(f"  MAE:  {s.get('mae_mean', 0):.4f} ± {s.get('mae_std', 0):.4f}")
    print(f"  RMSE: {s.get('rmse_mean', 0):.4f} ± {s.get('rmse_std', 0):.4f}")
    print(f"  R²:   {s.get('r2_mean', 0):.4f} ± {s.get('r2_std', 0):.4f}")
    print(f"  PCC:  {s.get('pcc_mean', 0):.4f} ± {s.get('pcc_std', 0):.4f}")
    print(f"  耗时: {elapsed:.1f}s ({elapsed / 60:.1f} min)")
    print("=" * 70)

    return result


if __name__ == "__main__":
    main()
