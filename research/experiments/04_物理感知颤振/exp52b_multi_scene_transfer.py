# -*- coding: utf-8 -*-
"""
实验52b：泛化性多场景扩展——回应审稿人"泛化性证据不足（仅单一跨数据集场景）"

场景矩阵（全部 3 seeds，Euler 求解器，协议同 exp52）：
- A  跨数据集零样本：nuaa(W1-W9) → phm2010(c1,c4,c6)   [exp52 原有]
- A2 反向跨数据集零样本：phm2010(c1,c4,c6) → nuaa(W1-W9) [新增]
- B2 同数据集跨组（分布外）：nuaa(W1-W4) → nuaa(W5-W9)  [新增]
- B  同数据集迁移：phm2010(c1,c4) → phm2010(c6)        [exp52 原有]

诚实预期：A/A2 跨数据集零样本大概率失败（门控盲区数据集级）；B2 同数据集跨组
（训练见过相似工况分布）可能部分成功——若成功则泛化性叙事立体化：
分布内跨组可迁移 → 跨数据集零样本不可 → 判据边界更精细。
"""
import sys
from pathlib import Path
from datetime import datetime
import json

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import exp52_cross_dataset_transfer as X52
import exp50_uniwear_real as E
from metrics import ChatterMetrics
import models as _models
_models._HAS_TORCHDIFFEQ = False  # 强制 Euler（诚信记录，见 dlnn-research-pipeline 技能）

UNIWEAR_CSV = E.UNIWEAR_CSV
NUM_EPOCHS = 60
SEEDS = [42, 43, 44]
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "results"

SCENES = [
    ("A_cross_dataset", ["W1","W2","W3","W4","W5","W6","W7","W8","W9"], ["c1","c4","c6"]),
    ("A2_reverse_cross", ["c1","c4","c6"], ["W1","W2","W3","W4","W5","W6","W7","W8","W9"]),
    ("B2_within_nuaa", ["W1","W2","W3","W4"], ["W5","W6","W7","W8","W9"]),
    ("B_within_phm2010", ["c1","c4"], ["c6"]),
]


def main():
    print("Device: cpu (exp52b multi-scene transfer)", flush=True)
    df = pd.read_csv(UNIWEAR_CSV)
    metrics_calc = ChatterMetrics()
    results = {
        "experiment": "exp52b_multi_scene_transfer",
        "timestamp": datetime.now().isoformat(),
        "ltc_solver": "euler",
        "num_epochs": NUM_EPOCHS,
        "seeds": SEEDS,
        "scenes": {},
    }
    for scene_name, tr_groups, te_groups in SCENES:
        print(f"\n=== {scene_name}: train={tr_groups} test={te_groups} ===", flush=True)
        r = {"base_MAE": [], "lstm_MAE": [], "lstm_R2": [], "dlnn_MAE": [], "dlnn_R2": [], "gate": []}
        for seed in SEEDS:
            res = X52.run_scene(df, tr_groups, te_groups, scene_name, seed, metrics_calc)
            r["base_MAE"].append(res["baseline"]["MAE"])
            r["lstm_MAE"].append(res["lstm"]["MAE"])
            r["lstm_R2"].append(res["lstm"]["R2"])
            r["dlnn_MAE"].append(res["dlnn"]["MAE"])
            r["dlnn_R2"].append(res["dlnn"]["R2"])
            r["gate"].append(res["dlnn"]["gate"])
            print(f"  seed{seed}: base={res['baseline']['MAE']:.4f} lstm={res['lstm']['MAE']:.4f} "
                  f"(R2={res['lstm']['R2']:.2f}) dlnn={res['dlnn']['MAE']:.4f} "
                  f"(R2={res['dlnn']['R2']:.2f}) alpha={res['dlnn']['gate']:.3f}", flush=True)
        for k, v in list(r.items()):
            if k.endswith("_mean"):
                continue
            r[k + "_mean"] = float(np.mean(v))
        mae_l = np.array(r["lstm_MAE"]); mae_d = np.array(r["dlnn_MAE"])
        if np.std(mae_l) > 0:
            from scipy import stats
            t_stat, p_val = stats.ttest_rel(mae_l, mae_d)
            r["paired_t_p"] = float(p_val)
            print(f"  dlnn vs lstm paired t: p={p_val:.3f} (n=3)", flush=True)
        results["scenes"][scene_name] = r
        print(f"  mean: base={r['base_MAE_mean']:.4f} lstm={r['lstm_MAE_mean']:.4f} "
              f"dlnn={r['dlnn_MAE_mean']:.4f} gate={r['gate_mean']:.3f}", flush=True)

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_file = OUTPUT_DIR / "multi_scene_transfer_results.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to {out_file}", flush=True)


if __name__ == "__main__":
    main()
