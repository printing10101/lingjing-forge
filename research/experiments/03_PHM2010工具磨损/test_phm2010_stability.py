"""测试 PHM2010Dataset 在不同 num_samples 下的加载稳定性。"""
import sys
import os
import types

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

try:
    import _overlapped  # noqa: F401
except OSError:
    _patch = types.ModuleType("_overlapped")
    _patch.Overlapped = type("Overlapped", (), {})
    sys.modules["_overlapped"] = _patch
    print("[warn] _overlapped 补丁已注入。", flush=True)

sys.path.insert(0, "python")
sys.path.insert(0, "python/experiments")

print("导入 PHM2010Dataset...", flush=True)
from experiments.data_generator import PHM2010Dataset

for n in [100, 300, 500]:
    print(f"\n测试 num_samples={n}...", flush=True)
    try:
        ds = PHM2010Dataset(num_samples=n, window_size=500, noise_level=0.05)
        print(f"  OK: {len(ds)} samples, features shape: {ds.data['features'].shape}", flush=True)
        print(f"  a_lim range: [{ds.data['a_lim'].min():.4f}, {ds.data['a_lim'].max():.4f}]", flush=True)
        print(f"  data_source: {ds.data.get('data_source', 'unknown')}", flush=True)
        del ds
        import gc
        gc.collect()
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()

print("\n所有测试完成。", flush=True)
