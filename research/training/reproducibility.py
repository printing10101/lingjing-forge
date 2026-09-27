"""可复现性工具 — 全局随机种子与确定性设置。

学术要求：
- 论文报告的实验结果必须可复现
- 同一种子 + 同一代码 + 同一硬件 = 相同结果
- cudnn.deterministic 强制使用确定性算法（代价：训练速度降低 5-15%）
"""

import os
import random
import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_SEED = 42

def set_global_seed(seed: int = _DEFAULT_SEED) -> None:
    """设置全局随机种子以确保可复现性。

    必须在以下操作之前调用：
    - DataLoader 创建
    - 模型初始化
    - 权重初始化
    - 任何随机数据生成

    Args:
        seed: 随机种子，默认 42
    """
    # Python 内置随机
    random.seed(seed)

    # NumPy 随机
    np.random.seed(seed)

    # Python hash 随机化（影响 dict 迭代顺序）
    os.environ["PYTHONHASHSEED"] = str(seed)

    # PyTorch 随机
    try:
        import torch
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed(seed)
            torch.cuda.manual_seed_all(seed)

        # 强制 cuDNN 使用确定性算法
        # 代价：训练速度降低 5-15%，但确保 GPU 训练可复现
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False

        logger.info("全局随机种子已设置: seed=%d, cudnn.deterministic=True", seed)
    except ImportError:
        logger.warning("PyTorch 未安装，跳过 torch 种子设置")


def get_worker_init_fn(base_seed: int = _DEFAULT_SEED):
    """返回 DataLoader 的 worker_init_fn，确保多进程数据加载可复现。

    用法：
        DataLoader(..., worker_init_fn=get_worker_init_fn(seed))
    """
    def worker_init_fn(worker_id: int) -> None:
        seed = base_seed + worker_id
        np.random.seed(seed)
        random.seed(seed)
        try:
            import torch
            torch.manual_seed(seed)
        except ImportError:
            pass
    return worker_init_fn
