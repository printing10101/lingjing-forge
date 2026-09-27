# -*- coding: utf-8 -*-
"""实测稳定性点验证：三个模型在 7 个真实点上的完整对比。

模型：
  A. 默认 Tlusty（引擎自带刚度/质量/阻尼）
  B. 真实模态 Tlusty（用论文 40 行实测模态参数，按悬伸+进向配置 mode-1）
  C. DL-LNN（合成 7 维切削参数空间训练后推理）

数据：measured_stability_points.csv 的 7 个真实点（Ji 2024 SciRep，DOI 10.1038/s41598-024-76165-8）
输出：results/real_points_validation_results.json + 控制台逐点对比表
"""

import csv
import json
import os
import sys
import time

import numpy as np
import torch

# 路径注入（脚本可独立运行）
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_EXP_DIR = os.path.dirname(_SCRIPT_DIR)
_RESEARCH_DIR = os.path.dirname(_EXP_DIR)
for p in (_EXP_DIR, _RESEARCH_DIR, _SCRIPT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from data_generator import TlustyAnalyticalModel, SyntheticChatterDataset, build_physics_features_7d
from config import get_config
from trainer import DLLNNTrainer
from torch.utils.data import DataLoader, Subset

RESULTS_DIR = os.path.join(_EXP_DIR, "results")
FRF_CSV = os.path.join(_RESEARCH_DIR, "datasets", "measured_stability", "measured_frf_11496886.csv")
POINTS_CSV = os.path.join(_RESEARCH_DIR, "datasets", "measured_stability", "measured_stability_points.csv")


def load_points():
    with open(POINTS_CSV, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    pts = []
    for r in rows:
        pts.append(
            {
                "source": r["source"],
                "n": float(r["n_rpm"]),
                "ap": float(r["ap_mm"]),
                "ae": float(r["ae_mm"]),
                "D": float(r["tool_diameter_mm"]),
                "z": int(r["num_teeth"]),
                "H": float(r["hardness_hb"]),
                "stable": int(r["stable"]),
            }
        )
    return pts


def load_frf():
    frf = {}
    with open(FRF_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            oh = int(r["overhang_mm"])
            if int(r["mode"]) != 1:
                continue
            frf[(oh, r["axis"])] = {
                "f": float(r["freq_hz"]),
                "zeta": float(r["damping_ratio"]),
                "m": float(r["modal_mass_kg"]),
            }
    return frf


def tlusty_a_lim(model, n, H, D, z, ae):
    return float(
        model.compute_limiting_depth(
            np.array([n]),
            hardness=np.array([H]),
            tool_diameter=np.array([D]),
            num_teeth=np.array([z]),
            feed_rate=None,
            radial_depth=np.array([ae]),
        )[0]
    )


def metrics(y_true, y_pred):
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, matthews_corrcoef

    yt = np.asarray(y_true, dtype=int)
    yp = np.asarray(y_pred, dtype=int)
    m = {
        "accuracy": float(accuracy_score(yt, yp)),
        "balanced_accuracy": float(balanced_accuracy_score(yt, yp)),
        "mcc": float(matthews_corrcoef(yt, yp)),
        "n": int(len(yt)),
        "n_stable": int((yt == 1).sum()),
        "n_unstable": int((yt == 0).sum()),
    }
    return m


def main():
    pts = load_points()
    frf = load_frf()
    print(f"真实点: {len(pts)} | 模态参数: {len(frf)} 组 mode-1")

    # 每点的悬伸/进向（从 source 解析）
    def overhang_feed(src):
        oh = 65 if "65mm" in src else 35
        ax = "y" if "y-feed" in src else "x"
        return oh, ax

    # 模型 A: 默认 Tlusty
    def pred_A(pt):
        m = TlustyAnalyticalModel(stiffness=0.9e6, modal_mass=95.0, damping_ratio=0.048, num_teeth=pt["z"])
        a = tlusty_a_lim(m, pt["n"], pt["H"], pt["D"], pt["z"], pt["ae"])
        return 1 if pt["ap"] > a else 0, a

    # 模型 B: 真实模态 Tlusty
    def pred_B(pt):
        oh, ax = overhang_feed(pt["source"])
        fm = frf.get((oh, ax))
        if fm is None:
            raise ValueError(f"缺模态参数: overhang={oh} axis={ax}")
        k = fm["m"] * (2 * np.pi * fm["f"]) ** 2
        m = TlustyAnalyticalModel(stiffness=k, modal_mass=fm["m"], damping_ratio=fm["zeta"], num_teeth=pt["z"])
        a = tlusty_a_lim(m, pt["n"], pt["H"], pt["D"], pt["z"], pt["ae"])
        return 1 if pt["ap"] > a else 0, a

    # ---- 模型 C: DL-LNN（合成 7 维空间训练）----
    print("\n[训练 DL-LNN 于合成 7 维空间（快速轮数）...]")
    cfg = get_config("realpts_dlnn")
    cfg.model.device = "cpu"
    cfg.model.num_epochs_stage1 = 6
    cfg.model.num_epochs_stage2 = 8
    cfg.model.batch_size = 64
    ds = SyntheticChatterDataset(num_samples=800, noise_level=0.05, seed=42)
    torch.manual_seed(42)
    idx = torch.randperm(len(ds))
    tr_ds = Subset(ds, idx[:560].tolist())
    va_ds = Subset(ds, idx[560:680].tolist())
    tr_loader = DataLoader(tr_ds, batch_size=64, shuffle=True)
    va_loader = DataLoader(va_ds, batch_size=64, shuffle=False)
    t0 = time.time()
    trainer = DLLNNTrainer(cfg, device="cpu")
    trainer.train(tr_loader, va_loader)
    print(f"  训练完成 {time.time() - t0:.0f}s")
    trainer.model.eval()

    def pred_C(pt):
        feats = build_physics_features_7d(
            spindle_speed=np.array([pt["n"]]),
            feed_rate=np.array([0.25]),
            axial_depth=np.array([pt["ap"]]),
            radial_depth=np.array([pt["ae"]]),
            hardness=np.array([pt["H"]]),
            tool_diameter=np.array([pt["D"]]),
            num_teeth=np.array([pt["z"]]),
        )
        with torch.no_grad():
            y_pred, _ = trainer.model(torch.from_numpy(feats))
        a = float(trainer.denormalize(y_pred.numpy())[0])
        return 1 if pt["ap"] > a else 0, a

    # 逐点评估
    rows_out = []
    yt = [p["stable"] for p in pts]
    models = {"A_default_tlusty": pred_A, "B_real_modal_tlusty": pred_B, "C_dlnn": pred_C}
    results = {}
    for mname, fn in models.items():
        preds, alims = [], []
        for pt in pts:
            s, a = fn(pt)
            preds.append(s)
            alims.append(round(a, 4))
        results[mname] = {"metrics": metrics(yt, preds), "predicted_stable": preds, "a_lim_mm": alims}

    # 逐点表
    print("\n=== 逐点对比（实测 vs 各模型预测）===")
    print(
        f"{'点':<36}{'n':>7}{'ap':>6}{'实测':>5}{'A默认':>6}{'A_alim':>8}{'B真实':>6}{'B_alim':>8}{'C_DLNN':>7}{'C_alim':>8}"
    )
    for i, pt in enumerate(pts):
        a_s = results["A_default_tlusty"]
        b_s = results["B_real_modal_tlusty"]
        c_s = results["C_dlnn"]
        print(
            f"{pt['source'][:35]:<36}{pt['n']:>7.0f}{pt['ap']:>6.1f}{pt['stable']:>5}"
            f"{a_s['predicted_stable'][i]:>6}{a_s['a_lim_mm'][i]:>8.3f}"
            f"{b_s['predicted_stable'][i]:>6}{b_s['a_lim_mm'][i]:>8.3f}"
            f"{c_s['predicted_stable'][i]:>7}{c_s['a_lim_mm'][i]:>8.3f}"
        )

    print("\n=== 汇总指标 ===")
    for mname in models:
        m = results[mname]["metrics"]
        print(f"  {mname:<22} acc={m['accuracy']:.3f} bal_acc={m['balanced_accuracy']:.3f} mcc={m['mcc']:.3f}")

    out = {
        "experiment": "real_points_validation",
        "n_points": len(pts),
        "points": [
            {"source": p["source"], "n_rpm": p["n"], "ap_mm": p["ap"], "measured_stable": p["stable"]} for p in pts
        ],
        "models": results,
        "notes": [
            "数据: Ji2024 SciRep DOI 10.1038/s41598-024-76165-8 正文报告的 A-G 点",
            "B 模型用论文 40 行实测模态参数(mode-1)按悬伸+进向配置",
            "C 模型在合成 7 维切削参数空间快速训练(6+8 epoch)",
            "feed 未报告: A/B 用默认参数, C 用 0.25 mm/tooth 假设",
        ],
    }
    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = os.path.join(RESULTS_DIR, "real_points_validation_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
