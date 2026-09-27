"""PHM2010Dataset 连通性测试：验证真实数据加载与特征提取。"""

import sys
import os
import types

# WinSock 绕过补丁
try:
    import _overlapped  # noqa: F401
except OSError:
    _patch = types.ModuleType("_overlapped")
    _patch.Overlapped = type("Overlapped", (), {})
    sys.modules["_overlapped"] = _patch

_EXPERIMENTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_EXPERIMENTS_DIR)
sys.path.insert(0, _PROJECT_ROOT)
sys.path.insert(0, _EXPERIMENTS_DIR)

import numpy as np
from experiments.data_generator import PHM2010Dataset


def main():
    print("=" * 70)
    print("PHM2010Dataset 连通性测试")
    print("=" * 70)

    try:
        dataset = PHM2010Dataset(
            num_samples=500,
            noise_level=0.05,
            window_size=500,
        )
    except Exception as e:
        print(f"[FAIL] PHM2010Dataset 实例化失败: {e}")
        sys.exit(1)

    data = dataset.data
    data_source = data.get("data_source", "unknown")
    print(f"\n数据来源: {data_source}")

    if data_source == "synthetic_fallback":
        print("[WARN] 回退到合成数据！真实 PHM2010 数据加载失败。")
        print("       请检查 python/data/uniwear/phm2010_bundle_high_resolution.csv")
        sys.exit(2)

    features = data["features"]
    a_lim = data["a_lim"]
    a_lim_clean = data["a_lim_clean"]

    print(f"\n样本数: {len(features)}")
    print(f"特征维度: {features.shape[1]}")
    print(f"experiment_tags: {data.get('experiment_tags', [])}")

    print("\n特征统计（每维 min/max/mean/std）:")
    for i in range(features.shape[1]):
        col = features[:, i]
        print(
            f"  dim[{i}]: min={col.min():.4f}, max={col.max():.4f}, "
            f"mean={col.mean():.4f}, std={col.std():.4f}"
        )

    print("\n标签 a_lim 统计:")
    print(
        f"  a_lim:      min={a_lim.min():.4f}, max={a_lim.max():.4f}, "
        f"mean={a_lim.mean():.4f}, std={a_lim.std():.4f}"
    )
    print(
        f"  a_lim_clean: min={a_lim_clean.min():.4f}, max={a_lim_clean.max():.4f}, "
        f"mean={a_lim_clean.mean():.4f}, std={a_lim_clean.std():.4f}"
    )

    # 验证 __getitem__ 接口
    sample = dataset[0]
    print(f"\n__getitem__(0) 返回: {len(sample)} 元组")
    for i, item in enumerate(sample):
        if hasattr(item, "shape"):
            print(f"  [{i}] shape={item.shape}, dtype={item.dtype}, value={item.flatten()[:3]}")
        else:
            print(f"  [{i}] {type(item)}: {item}")

    # 验证 target 归一化所需的统计量
    print(f"\nTarget 归一化预计算:")
    print(f"  y_true mean = {a_lim.mean():.4f}")
    print(f"  y_true std  = {a_lim.std():.4f}")
    print(f"  归一化后 y 范围: [{(a_lim.min() - a_lim.mean()) / a_lim.std():.4f}, "
          f"{(a_lim.max() - a_lim.mean()) / a_lim.std():.4f}]")

    print("\n" + "=" * 70)
    print("[OK] PHM2010Dataset 加载真实数据成功，可集成到主实验")
    print("=" * 70)


if __name__ == "__main__":
    main()
