# -*- coding: utf-8 -*-
"""快速真实数据验证：在 PHM2010 真实信号上训练 DL-LNN 并评估（缩减轮数）。

目的：用仓库内【真实测量信号】（PHM2010，104677 行 7 通道力/振动/AE）
验证引擎的可训练性与真实数据上的表现。
诚实标注：
  - 输入特征来自真实 PHM2010 信号（真实测量）
  - a_lim 标签为 Tlusty 物理模型从振动能量派生的代理标签（非实测稳定性边界）
  - 本实验报告的是"真实信号 + 物理代理标签"的结果，不可声称实测稳定性验证
"""
import os, sys, json, time
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_EXP_DIR = os.path.dirname(_SCRIPT_DIR)          # experiments/
_RESEARCH_DIR = os.path.dirname(_EXP_DIR)        # research/
for p in (_EXP_DIR, _RESEARCH_DIR, _SCRIPT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np
import torch

from data_generator import PHM2010Dataset, get_dataset_class
from config import get_config
from trainer import DLLNNTrainer
from metrics import MetricsTracker

def main():
    config = get_config("realdata_phm2010_quick")
    config.model.device = "cpu"
    # 缩减轮数（快速验证；论文级训练用完整 Stage1=100/Stage2=200）
    config.model.num_epochs_stage1 = 8
    config.model.num_epochs_stage2 = 10
    config.model.batch_size = 64

    # 显式指向真实数据位置（research/datasets/uniwear/uniwear/）
    _uniwear_dir = os.path.join(_RESEARCH_DIR, "datasets", "uniwear", "uniwear")
    ds = PHM2010Dataset(num_samples=1500, window_size=500, seed=42, data_dir=_uniwear_dir)
    _feats = ds.data["features"] if isinstance(ds.data, dict) and "features" in ds.data else None
    print(f"[PHM2010 真实数据] 样本数: {len(ds)} | data_source: {ds.data.get('data_source') if isinstance(ds.data, dict) else '?'} | 特征: {None if _feats is None else _feats.shape}")
    if _feats is None:
        raise RuntimeError("真实数据未加载（回退合成），中止")

    # 划分
    n = len(ds)
    torch.manual_seed(42)
    idx = torch.randperm(n)
    tr_n, va_n = int(n * 0.7), int(n * 0.15)
    from torch.utils.data import Subset, DataLoader
    tr_ds = Subset(ds, idx[:tr_n].tolist())
    va_ds = Subset(ds, idx[tr_n:tr_n + va_n].tolist())
    te_ds = Subset(ds, idx[tr_n + va_n:].tolist())
    tr_loader = DataLoader(tr_ds, batch_size=config.model.batch_size, shuffle=True)
    va_loader = DataLoader(va_ds, batch_size=config.model.batch_size, shuffle=False)
    te_loader = DataLoader(te_ds, batch_size=config.model.batch_size, shuffle=False)
    print(f"train={len(tr_ds)} val={len(va_ds)} test={len(te_ds)}")

    t0 = time.time()
    trainer = DLLNNTrainer(config, device="cpu")
    trainer.train(tr_loader, va_loader)
    metrics = trainer.evaluate(te_loader)
    dt = time.time() - t0

    print("\n=== 真实数据测试集评估（PHM2010 真实信号 + 物理代理标签）===")
    for k, v in metrics.items():
        if isinstance(v, (int, float)):
            print(f"  {k}: {v:.6f}")
    result = {
        "experiment": "realdata_phm2010_quick",
        "dataset": "PHM2010 (真实信号)",
        "note": "输入=真实PHM2010信号统计特征；a_lim标签=Tlusty物理代理标签（非实测稳定性边界）",
        "samples": n,
        "train_val_test": [len(tr_ds), len(va_ds), len(te_ds)],
        "epochs": [config.model.num_epochs_stage1, config.model.num_epochs_stage2],
        "elapsed_s": round(dt, 1),
        "metrics": {k: (float(v) if isinstance(v, (int, float)) else v) for k, v in metrics.items()},
    }
    out_path = os.path.join("results", "realdata_phm2010_quick_results.json")
    os.makedirs("results", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")

if __name__ == "__main__":
    main()
