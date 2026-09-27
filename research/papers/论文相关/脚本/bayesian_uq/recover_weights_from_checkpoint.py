"""
从中间 checkpoint 恢复 full_weights.pt（无需重跑训练）
====================================================

用途：
    如果 rerun_full_save_weights.py 在评估或最终保存阶段失败，
    但中间 checkpoint (stage2_done.pt) 已生成，可用本脚本直接生成
    full_weights.pt，避免重跑 10+ 小时训练。

使用方式：
    python recover_weights_from_checkpoint.py

输入：
    results/checkpoints/stage2_done.pt

输出：
    results/full_weights.pt
"""

import sys
import types
from pathlib import Path

# WinSock 损坏绕过补丁
try:
    import _overlapped  # noqa: F401
except OSError:
    _patch = types.ModuleType("_overlapped")
    _patch.Overlapped = type("Overlapped", (), {})
    sys.modules["_overlapped"] = _patch

import torch

# 路径设置
script_dir = Path(__file__).resolve().parent
project_root = script_dir
for _ in range(6):
    if (project_root / "research" / "training" / "reproducibility.py").exists():
        break
    project_root = project_root.parent
else:
    project_root = script_dir.parents[5]

sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "engineering" / "python"))
sys.path.insert(0, str(project_root / "research"))
sys.path.insert(0, str(project_root / "research" / "experiments"))

from experiments.config import get_config
from experiments.models import DLLNNWithPhysics


def main():
    ckpt_path = script_dir / "results" / "checkpoints" / "stage2_done.pt"
    out_path = script_dir / "results" / "full_weights.pt"

    if not ckpt_path.exists():
        print(f"[错误] 中间 checkpoint 不存在: {ckpt_path}")
        print("        请先运行 rerun_full_save_weights.py 完成训练。")
        sys.exit(1)

    print(f"[加载] 中间 checkpoint: {ckpt_path}")
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)

    config = get_config()
    model = DLLNNWithPhysics(
        input_dim=7,
        hidden_dim=config.model.hidden_dim,
        num_layers=config.model.num_layers,
        output_dim=1,
        dt=config.model.ltc_dt,
        dropout=config.model.dropout,
    )

    # 优先使用 best_model_state（阶段二训练中保存的最佳权重）
    if ckpt.get("best_model_state") is not None:
        print("[使用] best_model_state（阶段二训练中的最佳权重）")
        model.load_state_dict(ckpt["best_model_state"])
    else:
        print("[使用] model_state_dict（最终状态，可能略过拟合）")
        model.load_state_dict(ckpt["model_state_dict"])

    target_mean = ckpt["target_mean"]
    target_std = ckpt["target_std"]

    save_obj = {
        "model_state_dict": model.state_dict(),
        "target_mean": target_mean,
        "target_std": target_std,
        "config": {
            "input_dim": 7,
            "hidden_dim": config.model.hidden_dim,
            "num_layers": config.model.num_layers,
            "output_dim": 1,
            "dt": config.model.ltc_dt,
            "dropout": config.model.dropout,
            "lambda_phys": config.model.lambda_phys,
            "lambda_pcc": config.model.lambda_pcc,
        },
        "metrics": {},  # 从 checkpoint 恢复时不包含评估指标
        "train_history": {
            "stage1_epochs": 30,
            "stage2_epochs": 60,
            "final_val_loss": -1.0,  # 未知
            "recovered_from": str(ckpt_path),
        },
        "saved_at": "[recovered from checkpoint]",
        "elapsed_sec": -1.0,
    }

    torch.save(save_obj, out_path)
    print(f"\n[已保存] 权重文件: {out_path}")
    print(f"  target_mean = {target_mean:.4f}")
    print(f"  target_std  = {target_std:.4f}")
    print(f"  文件大小: {out_path.stat().st_size / 1024 / 1024:.1f} MB")
    print("\n[注意] 此权重从中间 checkpoint 恢复，metrics 字段为空。")
    print("        如需完整指标，请重跑 rerun_full_save_weights.py。")


if __name__ == "__main__":
    main()
