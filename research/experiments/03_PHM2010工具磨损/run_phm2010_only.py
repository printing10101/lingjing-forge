"""单独重跑 PHM2010 实验（绕过 C 扩展冲突崩溃）。

根因：先导入 run_experiment.py（触发 torchdiffeq/torch C 扩展）后，
PHM2010Dataset._load_real_data 中的 pandas/numpy 操作会触发 0xC0000005 访问违规。
解决方案：在导入 trainer/models 之前先实例化 PHM2010Dataset 预加载数据，
然后用 InMemoryDataset 包装预加载数据，传给 run_single_dataset_experiment。
"""

import sys
import os
import json
import types
import gc
import time

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# WinSock 损坏绕过补丁
try:
    import _overlapped  # noqa: F401
except OSError:
    _patch = types.ModuleType("_overlapped")
    _patch.Overlapped = type("Overlapped", (), {})
    sys.modules["_overlapped"] = _patch
    print("[warn] _overlapped 模块加载失败，已注入空实现绕过 WinSock 损坏。", flush=True)

# 关键：在导入 trainer/models（触发 C 扩展）之前，先加载 PHM2010 数据
_EXPERIMENTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_EXPERIMENTS_DIR)
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, _EXPERIMENTS_DIR)

import numpy as np
import torch
from torch.utils.data import Dataset

print("步骤1: 预加载 PHM2010 数据（在导入重型 C 扩展之前）...", flush=True)
from experiments.data_generator import PHM2010Dataset

_pre_ds = PHM2010Dataset(num_samples=300, window_size=500, noise_level=0.05)
_PRELOADED_DATA = _pre_ds.data
print(f"  预加载完成: {len(_pre_ds)} 样本, 特征形状: {_PRELOADED_DATA['features'].shape}", flush=True)
print(f"  a_lim range: [{_PRELOADED_DATA['a_lim'].min():.4f}, {_PRELOADED_DATA['a_lim'].max():.4f}]", flush=True)
print(f"  data_source: {_PRELOADED_DATA.get('data_source', 'unknown')}", flush=True)
del _pre_ds
gc.collect()


class InMemoryPHM2010Dataset(Dataset):
    """从预加载数据字典构造的内存 Dataset，绕过 _load_real_data 的 C 扩展冲突。"""

    def __init__(self, num_samples: int = 300, noise_level: float = 0.05, window_size: int = 500, **kwargs):
        super().__init__()
        self.data = _PRELOADED_DATA
        self.num_samples = len(self.data["features"])
        self.dataset_name = "PHM2010"

    def __len__(self) -> int:
        return len(self.data["features"])

    def __getitem__(self, idx):
        feats = torch.tensor(self.data["features"][idx], dtype=torch.float32)
        a_lim = torch.tensor(self.data["a_lim"][idx], dtype=torch.float32)
        a_lim_physics = torch.tensor(self.data.get("a_lim_clean", self.data["a_lim"])[idx], dtype=torch.float32)
        return feats, a_lim, a_lim_physics


print("\n步骤2: 导入 run_experiment（现在可以安全导入 C 扩展）...", flush=True)
from experiments.config import get_config
from experiments.run_experiment import run_single_dataset_experiment

RESULTS_PATH = os.path.join(_EXPERIMENTS_DIR, "results", "all_experiments_results.json")


def main():
    # 加载现有结果
    if os.path.exists(RESULTS_PATH):
        with open(RESULTS_PATH, "r", encoding="utf-8") as f:
            all_results = json.load(f)
    else:
        all_results = {}
    print(f"[加载] 现有结果包含数据集: {list(all_results.keys())}", flush=True)

    # 获取配置
    config = get_config("main_experiment")
    config.model.device = "cpu"

    # 加载 Optuna 超参
    best_params_path = os.path.join(_EXPERIMENTS_DIR, "results", "best_hyperparams.json")
    if os.path.exists(best_params_path):
        with open(best_params_path, "r", encoding="utf-8") as f:
            best_params = json.load(f)
        print(f"[Optuna] 加载超参搜索结果: {best_params_path}", flush=True)
        if "GP" in best_params:
            config.gp_best_params = best_params["GP"]
        if "DL-LNN" in best_params:
            dlnn_p = best_params["DL-LNN"]
            if "learning_rate" in dlnn_p:
                config.model.learning_rate = dlnn_p["learning_rate"]
            if "weight_decay" in dlnn_p:
                config.model.weight_decay = dlnn_p["weight_decay"]
            if "dropout" in dlnn_p:
                config.model.dropout = dlnn_p["dropout"]

    # 运行 PHM2010 实验（使用 InMemoryPHM2010Dataset 绕过 C 扩展冲突）
    print("\n" + "=" * 80, flush=True)
    print("单独运行 PHM2010 实验 (InMemoryDataset, 208 样本)", flush=True)
    print("=" * 80, flush=True)

    start_time = time.time()
    phm2010_results = run_single_dataset_experiment(
        config,
        "PHM2010",
        InMemoryPHM2010Dataset,
        {
            "num_samples": 300,
            "noise_level": 0.05,
            "window_size": 500,
        },
    )
    elapsed = time.time() - start_time
    print(f"\n[PHM2010] 完成，耗时 {elapsed / 60:.1f} 分钟", flush=True)

    gc.collect()

    # 合并结果
    all_results["PHM2010"] = {
        model_name: {k: float(v) for k, v in metrics.items()} for model_name, metrics in phm2010_results.items()
    }

    # 保存
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f"\n[OK] 合并结果已保存至: {RESULTS_PATH}", flush=True)
    print(f"     包含数据集: {list(all_results.keys())}", flush=True)
    for ds, models in all_results.items():
        print(f"     {ds}: {len(models)} 个模型", flush=True)


if __name__ == "__main__":
    main()
