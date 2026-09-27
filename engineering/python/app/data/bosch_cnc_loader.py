"""Bosch CNC 数据加载器.

从本地 CSV 文件读取真实 Bosch CNC 加工数据集。历史版本曾在此无条件生成
100 条线性递增的伪造记录（忽略 ``data_dir``），导致训练出的"刀具磨损模型"
指标全部失真——已改为：数据文件缺失时抛出 ``FileNotFoundError``，绝不返回
合成数据（学术诚信要求）。

期望数据布局
------------
``data_dir`` 下按 split 放置 CSV：

    data_dir/train.csv   （必须包含 tool_wear / cutting_force / vibration 列）
    data_dir/val.csv     （可选）
    data_dir/test.csv    （可选）

可选列：``id`` / ``material`` / ``machine`` / ``process`` / ``label``，
用于 ``load_dataset`` 的过滤参数。
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 必需列：缺失任一列视为数据不合规，拒绝加载
REQUIRED_COLUMNS = ("tool_wear", "cutting_force", "vibration")

# 过滤维度 → CSV 列名
_FILTER_COLUMNS = {"machines": "machine", "processes": "process", "labels": "label"}

_VALID_SPLITS = ("train", "val", "test")


class BoschCNCDataLoader:
    """
    Bosch CNC 数据集加载器

    从 ``data_dir`` 下的真实 CSV 文件加载数据。文件不存在或缺少必需列时
    抛出异常，不生成任何合成数据。

    Attributes:
        data_dir: 数据目录路径
    """

    def __init__(self, data_dir: str | Path):
        """
        初始化加载器

        Args:
            data_dir: 数据目录路径
        """
        self.data_dir = Path(data_dir)
        self._data_cache: dict[str, list[dict[str, Any]]] = {}

    def _split_path(self, split: str) -> Path:
        """定位 split 对应的 CSV 文件路径."""
        if split not in _VALID_SPLITS:
            raise ValueError(f"split 只支持 {_VALID_SPLITS}，收到: {split!r}")
        return self.data_dir / f"{split}.csv"

    def _load_split_uncached(self, split: str) -> list[dict[str, Any]]:
        """读取并校验单个 split 的 CSV，返回记录列表."""
        path = self._split_path(split)
        if not path.is_file():
            raise FileNotFoundError(
                f"[数据缺失] 未找到 Bosch CNC 数据文件: {path}。"
                f"建议操作：将真实数据集 CSV 放置到该目录（需包含 {list(REQUIRED_COLUMNS)} 列），"
                "禁止使用合成数据替代。"
            )
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            missing = [c for c in REQUIRED_COLUMNS if c not in header]
            if missing:
                raise ValueError(
                    f"[数据不合规] {path} 缺少必需列: {missing}。建议操作：补齐 {list(REQUIRED_COLUMNS)} 列后重试。"
                )
            records: list[dict[str, Any]] = []
            for row in reader:
                rec: dict[str, Any] = {}
                try:
                    for col in REQUIRED_COLUMNS:
                        rec[col] = float(row[col])
                except (TypeError, ValueError):
                    # 必需列含非数值 → 跳过该行并记录
                    logger.warning("bosch_cnc_loader: %s 存在非数值行，已跳过", path.name)
                    continue
                for col in header:
                    if col in rec or col == "":
                        continue
                    rec[col] = row[col]
                if "id" not in rec:
                    rec["id"] = f"bosch_{split}_{len(records)}"
                records.append(rec)
        return records

    def load_dataset(self, split: str = "train", **kwargs: Any) -> list[dict[str, Any]]:
        """
        加载数据集

        Args:
            split: 数据集分割 ('train', 'val', 'test')
            machines: 按 ``machine`` 列过滤的机器列表（列缺失时结果为空）
            processes: 按 ``process`` 列过滤的工艺列表（列缺失时结果为空）
            labels: 按 ``label`` 列过滤的标签列表（列缺失时结果为空）

        Returns:
            数据样本列表（每条至少包含 tool_wear / cutting_force / vibration）

        Raises:
            FileNotFoundError: split 对应的 CSV 不存在
            ValueError: CSV 缺少必需列，或 split 非法
        """
        if split not in self._data_cache:
            self._data_cache[split] = self._load_split_uncached(split)
        records = self._data_cache[split]

        for kwarg, column in _FILTER_COLUMNS.items():
            wanted = kwargs.get(kwarg)
            if not wanted:
                continue
            wanted_set = {str(w) for w in wanted}
            records = [r for r in records if str(r.get(column, "")) in wanted_set]
        return records

    def extract_features(self, data: Any) -> dict[str, float]:
        """从单个样本提取特征（振动/切削力时域与频域近似值）。

        Args:
            data: 单条样本（dict）。非 dict 或缺少必需键时抛出 ValueError。

        Raises:
            ValueError: 样本不是 dict 或缺少必需列。
        """
        if not isinstance(data, dict):
            raise ValueError(f"extract_features 需要 dict 样本，收到: {type(data).__name__}")
        missing = [c for c in REQUIRED_COLUMNS if c not in data]
        if missing:
            raise ValueError(f"样本缺少必需字段: {missing}")
        force = float(data["cutting_force"])
        vib = float(data["vibration"])
        wear = float(data["tool_wear"])
        return {
            "time_x_rms": round(vib * 0.7, 6),
            "time_y_rms": round(vib * 0.5, 6),
            "time_z_rms": round(vib * 0.3, 6),
            "freq_x_dominant_freq": round(force / 10.0, 3),
            "cross_x_energy_ratio": round(wear * 0.01, 6),
        }

    def _available_values(self, column: str) -> list[str]:
        """扫描全部 split，返回某列的去重取值（排序）。"""
        values: set[str] = set()
        for split in _VALID_SPLITS:
            path = self._split_path(split)
            if not path.is_file():
                continue
            for rec in self.load_dataset(split):
                v = rec.get(column)
                if v:
                    values.add(str(v))
        return sorted(values)

    def get_dataset_summary(self) -> dict[str, Any]:
        """返回数据集可用进程/机器/标签概览（基于真实文件扫描）。"""
        return {
            "available_processes": self._available_values("process"),
            "available_machines": self._available_values("machine"),
            "available_labels": self._available_values("label"),
            **self.get_statistics(),
        }

    def get_statistics(self) -> dict[str, Any]:
        """
        获取数据集统计信息（基于真实文件，文件缺失的 split 计为 0）。

        Returns:
            统计信息字典
        """
        counts = {s: len(self.load_dataset(s)) for s in _VALID_SPLITS if self._split_path(s).is_file()}
        return {
            "total_samples": sum(counts.values()),
            "train_samples": counts.get("train", 0),
            "val_samples": counts.get("val", 0),
            "test_samples": counts.get("test", 0),
            "features": list(REQUIRED_COLUMNS),
        }
