"""BoschCNCDataLoader 真实数据加载测试.

历史版本曾无条件生成 100 条线性伪造记录（忽略 data_dir），导致下游
训练/校准指标失真。本文件锁定"真实读取 + 诚实报错"行为：
- 数据文件缺失 → FileNotFoundError
- 缺少必需列 → ValueError
- 过滤参数（machines/processes/labels）生效
- 统计信息来自真实文件，而非写死数值
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.data.bosch_cnc_loader import BoschCNCDataLoader

_REQUIRED = ["tool_wear", "cutting_force", "vibration"]


_FULL_HEADER = ["id", "machine", "process", "label", "tool_wear", "cutting_force", "vibration"]


def _write_csv(path: Path, rows: list[dict], header: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = header or (list(rows[0].keys()) if rows else _REQUIRED)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        writer.writerows(rows)


def _sample(i: int, machine: str = "machine_1", process: str = "milling", label: str = "good") -> dict:
    return {
        "id": f"bosch_{i}",
        "machine": machine,
        "process": process,
        "label": label,
        "tool_wear": 0.05 + i * 0.01,
        "cutting_force": 400.0 + i,
        "vibration": 0.04 + i * 0.001,
    }


class TestRealDataLoading:
    def test_missing_file_raises(self, tmp_path: Path):
        loader = BoschCNCDataLoader(data_dir=tmp_path)
        with pytest.raises(FileNotFoundError, match="train.csv"):
            loader.load_dataset(split="train")

    def test_missing_required_column_raises(self, tmp_path: Path):
        _write_csv(tmp_path / "train.csv", [{"tool_wear": 0.1, "cutting_force": 400.0}])
        loader = BoschCNCDataLoader(data_dir=tmp_path)
        with pytest.raises(ValueError, match="vibration"):
            loader.load_dataset(split="train")

    def test_reads_real_values(self, tmp_path: Path):
        rows = [_sample(0), _sample(1)]
        _write_csv(tmp_path / "train.csv", rows)
        loader = BoschCNCDataLoader(data_dir=tmp_path)
        records = loader.load_dataset(split="train")
        assert len(records) == 2
        assert records[0]["tool_wear"] == pytest.approx(0.05)
        assert records[1]["cutting_force"] == pytest.approx(401.0)
        assert records[0]["machine"] == "machine_1"  # 非必需列原样保留

    def test_filters_apply(self, tmp_path: Path):
        rows = [
            _sample(0),  # machine_1 / milling / good
            _sample(1, machine="machine_2"),  # machine_2 / milling / good
            _sample(2, process="turning", label="bad"),  # machine_1 / turning / bad
            _sample(3, label="bad"),  # machine_1 / milling / bad
        ]
        _write_csv(tmp_path / "train.csv", rows, header=_FULL_HEADER)
        loader = BoschCNCDataLoader(data_dir=tmp_path)
        assert len(loader.load_dataset(machines=["machine_2"])) == 1
        assert len(loader.load_dataset(processes=["turning"])) == 1
        assert len(loader.load_dataset(labels=["bad"])) == 2
        assert len(loader.load_dataset(machines=["machine_1"], labels=["good"])) == 1

    def test_non_numeric_required_row_skipped(self, tmp_path: Path):
        bad = {k: "N/A" for k in _REQUIRED}
        _write_csv(tmp_path / "train.csv", [bad, _sample(0)], header=_FULL_HEADER)
        loader = BoschCNCDataLoader(data_dir=tmp_path)
        records = loader.load_dataset(split="train")
        assert len(records) == 1

    def test_statistics_from_real_files(self, tmp_path: Path):
        _write_csv(tmp_path / "train.csv", [_sample(i) for i in range(3)])
        _write_csv(tmp_path / "val.csv", [_sample(0)])
        loader = BoschCNCDataLoader(data_dir=tmp_path)
        stats = loader.get_statistics()
        assert stats["train_samples"] == 3
        assert stats["val_samples"] == 1
        assert stats["test_samples"] == 0
        assert stats["total_samples"] == 4

    def test_summary_scans_available_values(self, tmp_path: Path):
        rows = [_sample(0), _sample(1, machine="machine_2", process="turning", label="bad")]
        _write_csv(tmp_path / "train.csv", rows)
        loader = BoschCNCDataLoader(data_dir=tmp_path)
        summary = loader.get_dataset_summary()
        assert summary["available_machines"] == ["machine_1", "machine_2"]
        assert summary["available_processes"] == ["milling", "turning"]
        assert summary["available_labels"] == ["bad", "good"]

    def test_invalid_split_rejected(self, tmp_path: Path):
        loader = BoschCNCDataLoader(data_dir=tmp_path)
        with pytest.raises(ValueError, match="split"):
            loader.load_dataset(split="dev")

    def test_extract_features_requires_dict(self, tmp_path: Path):
        loader = BoschCNCDataLoader(data_dir=tmp_path)
        with pytest.raises(ValueError, match="dict"):
            loader.extract_features("not-a-dict")
        with pytest.raises(ValueError, match="缺少必需字段"):
            loader.extract_features({"tool_wear": 0.1})
        feats = loader.extract_features({"tool_wear": 0.1, "cutting_force": 500.0, "vibration": 0.05})
        assert feats["freq_x_dominant_freq"] == pytest.approx(50.0)
