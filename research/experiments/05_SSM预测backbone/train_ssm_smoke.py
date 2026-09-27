"""SSM 预测 backbone 冒烟训练（Phase 3a：④ Mamba 思路）。

在 GPU（默认）或 CPU 上，用合成颤振数据训练 TorchMambaLNN：
- 验证纯 PyTorch SSM 实现可收敛（无需 mamba-ssm 依赖）；
- 产出 checkpoint 到 output/ssm_smoke/ 供后续工程侧接入。

用法（research/ 目录下）：
    python experiments/05_SSM预测backbone/train_ssm_smoke.py --epochs 12
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import torch
import torch.nn as nn

# research/ 为根：允许直接 import models/datasets
_RESEARCH_ROOT = Path(__file__).resolve().parents[2]
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

from datasets.synthetic_chatter import generate_chatter_dataset, make_chatter_dataloader  # noqa: E402
from models.torch_base_lnn import LNNConfig  # noqa: E402
from models.torch_mamba_lnn import TorchMambaLNN  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ssm-smoke")


def _pick_device(prefer_cuda: bool) -> torch.device:
    if prefer_cuda and torch.cuda.is_available():
        logger.info("使用 GPU: %s", torch.cuda.get_device_name(0))
        return torch.device("cuda")
    logger.info("使用 CPU")
    return torch.device("cpu")


def run(
    epochs: int = 12,
    batch_size: int = 32,
    seq_len: int = 200,
    hidden: int = 64,
    num_layers: int = 2,
    n_train: int = 1024,
    n_val: int = 256,
    lr: float = 1e-3,
    seed: int = 42,
    prefer_cuda: bool = True,
    selective: bool = True,
    out_dir: str = "output/ssm_smoke",
) -> dict:
    torch.manual_seed(seed)
    device = _pick_device(prefer_cuda)

    # 数据
    logger.info("生成合成颤振数据: train=%d val=%d seq_len=%d", n_train, n_val, seq_len)
    X_tr, yi_tr, yc_tr, cfg = generate_chatter_dataset(n_samples=n_train, seq_len=seq_len, seed=seed)
    X_va, yi_va, yc_va, _ = generate_chatter_dataset(n_samples=n_val, seq_len=seq_len, seed=seed + 1)
    train_loader = make_chatter_dataloader(X_tr, yi_tr, yc_tr, batch_size=batch_size)
    val_loader = make_chatter_dataloader(X_va, yi_va, yc_va, batch_size=batch_size, shuffle=False)

    # 模型：output_size=2（强度回归 + 颤振 logit）
    config = LNNConfig(
        input_size=cfg.n_features,
        hidden_size=hidden,
        output_size=2,
        num_layers=num_layers,
        dropout=0.1,
        time_constant=0.01,
    )
    model = TorchMambaLNN(config, selective=selective).to(device)
    logger.info("模型: %s | 参数量: %d", model.model_name, model.get_info()["total_parameters"])

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    loss_fn = nn.MSELoss()
    bce_fn = nn.BCEWithLogitsLoss()

    best_val = float("inf")
    best_state: dict | None = None
    dt = 0.01
    history: list[dict] = []

    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0
        n_batch = 0
        t0 = time.perf_counter()
        for X_b, yi_b, yc_b in train_loader:
            X_b, yi_b, yc_b = X_b.to(device), yi_b.to(device), yc_b.to(device)
            optimizer.zero_grad()
            outputs, _ = model.forward_sequence(X_b, dt)  # (B, T, 2)
            pooled = outputs.mean(dim=1)  # (B, 2) 时间维平均：捕捉任意时刻的颤振突发
            loss = loss_fn(pooled[:, :1], yi_b) + bce_fn(pooled[:, 1], yc_b.squeeze(-1))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += loss.item()
            n_batch += 1

        # 验证
        model.eval()
        with torch.no_grad():
            va_loss = 0.0
            correct = 0
            total = 0
            for X_b, yi_b, yc_b in val_loader:
                X_b, yi_b, yc_b = X_b.to(device), yi_b.to(device), yc_b.to(device)
                outputs, _ = model.forward_sequence(X_b, dt)
                pooled = outputs.mean(dim=1)
                va_loss += (loss_fn(pooled[:, :1], yi_b) + bce_fn(pooled[:, 1], yc_b.squeeze(-1))).item()
                pred = (torch.sigmoid(pooled[:, 1]) > 0.5).long()
                correct += (pred == yc_b.squeeze(-1).long()).sum().item()
                total += yc_b.size(0)
            va_loss /= max(len(val_loader), 1)
            acc = correct / max(total, 1)

        elapsed = time.perf_counter() - t0
        logger.info(
            "epoch %2d/%d | train_loss=%.4f | val_loss=%.4f | chatter_acc=%.3f | %.1fs",
            epoch, epochs, total_loss / max(n_batch, 1), va_loss, acc, elapsed,
        )
        history.append({"epoch": epoch, "train_loss": total_loss / max(n_batch, 1), "val_loss": va_loss, "acc": acc})

        if va_loss < best_val:
            best_val = va_loss
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}

    # 保存
    out_path = Path(out_dir) / "torch_mamba_lnn.pt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if best_state is not None:
        torch.save({"model_name": model.model_name, "config": config.to_dict(), "state_dict": best_state}, out_path)
        logger.info("最佳 checkpoint 已保存: %s (val_loss=%.4f)", out_path, best_val)
    else:
        torch.save({"model_name": model.model_name, "config": config.to_dict(), "state_dict": model.state_dict()}, out_path)

    return {
        "model": model.model_name,
        "device": str(device),
        "best_val_loss": best_val,
        "last_acc": history[-1]["acc"] if history else 0.0,
        "checkpoint": str(out_path),
        "history": history,
        "data_meta": cfg.meta,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="SSM 预测 backbone 冒烟训练")
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--seq-len", type=int, default=200)
    parser.add_argument("--hidden", type=int, default=64)
    parser.add_argument("--layers", type=int, default=2)
    parser.add_argument("--n-train", type=int, default=1024)
    parser.add_argument("--n-val", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--cpu", action="store_true", help="强制 CPU")
    parser.add_argument("--lti", action="store_true", help="使用 S4 风格 LTI（非选择性）")
    parser.add_argument("--out-dir", default="output/ssm_smoke")
    args = parser.parse_args()

    result = run(
        epochs=args.epochs,
        batch_size=args.batch_size,
        seq_len=args.seq_len,
        hidden=args.hidden,
        num_layers=args.layers,
        n_train=args.n_train,
        n_val=args.n_val,
        lr=args.lr,
        seed=args.seed,
        prefer_cuda=not args.cpu,
        selective=not args.lti,
        out_dir=args.out_dir,
    )
    logger.info("训练完成: %s", {k: v for k, v in result.items() if k != "history"})


if __name__ == "__main__":
    main()
