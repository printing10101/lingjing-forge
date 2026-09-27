"""导出注册表模型训练集 CLI（接线方案 WP2）。

用法（在 engineering/python 目录下）：
    python scripts/export_training_set.py --model cutting_force \
        --dataset-name synthetic_machining_params_v1 --out data/training_sets/cutting_force

产物：X.npy / y.npy / feature_manifest.json（训练脚本的输入契约）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_PYTHON_DIR = Path(__file__).resolve().parent.parent
if str(_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(_PYTHON_DIR))

from app.training.exporters import export_registry_training_set  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="导出注册表模型训练集（DatasetStore → X/y + 特征清单）")
    parser.add_argument("--model", default="cutting_force", help="注册表模型名（当前支持 cutting_force）")
    parser.add_argument("--dataset-name", default="synthetic_machining_params_v1", help="源数据集名")
    parser.add_argument("--dataset-id", default=None, help="源数据集 id（给定时跳过按名解析）")
    parser.add_argument("--version", default=None, help="源数据集版本（缺省取最新）")
    parser.add_argument(
        "--out",
        default=None,
        help="输出目录（缺省 data/training_sets/<model>）",
    )
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else _PYTHON_DIR / "data" / "training_sets" / args.model

    export = asyncio.run(
        export_registry_training_set(
            args.model,
            out_dir,
            dataset_name=args.dataset_name,
            dataset_id=args.dataset_id,
            version=args.version,
        )
    )
    export.write()

    print(json.dumps(export.feature_manifest, ensure_ascii=False, indent=2))
    print(f"\n导出完成: {export.x_path}\n        {export.y_path}\n        {export.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
