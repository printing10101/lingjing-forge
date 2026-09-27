"""临时测试 PHM2010Dataset 加载"""
import sys
import types

# WinSock 绕过补丁
try:
    import _overlapped  # noqa: F401
except OSError:
    _patch = types.ModuleType("_overlapped")
    _patch.Overlapped = type("Overlapped", (), {})
    sys.modules["_overlapped"] = _patch
    print("[warn] _overlapped 模块加载失败，已注入空实现绕过 WinSock 损坏。")

sys.path.insert(0, "python")
sys.path.insert(0, "python/experiments")

print("导入 PHM2010Dataset...")
from experiments.data_generator import PHM2010Dataset

print("实例化 PHM2010Dataset(num_samples=100, window_size=500)...")
try:
    ds = PHM2010Dataset(num_samples=100, window_size=500)
    print(f"PHM2010Dataset OK: {len(ds)} samples")
    print(f"features shape: {ds.data['features'].shape}")
    print(f"a_lim range: [{ds.data['a_lim'].min():.4f}, {ds.data['a_lim'].max():.4f}]")
    print(f"a_lim_clean range: [{ds.data['a_lim_clean'].min():.4f}, {ds.data['a_lim_clean'].max():.4f}]")
except Exception as e:
    print(f"PHM2010Dataset FAILED: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
