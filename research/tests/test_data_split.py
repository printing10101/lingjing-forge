"""
Unit tests for training data split logic.

Tests cover:
- Train/val/test three-way split
- Disjoint dataset verification (no overlapping sample IDs)
- Reproducibility with fixed random seeds
- Configurable split ratios
- Dataset size logging
"""

import pytest
import numpy as np
import logging

from training.dataset import LNNDataset


class TestLNNDatasetSplit:
    """Test LNNDataset split functionality"""

    @pytest.fixture
    def sample_dataset(self):
        """Create a sample dataset with 100 samples"""
        data = np.arange(100).reshape(-1, 1)
        labels = np.arange(100)
        return LNNDataset(data, labels)

    def test_three_way_split_default_ratios(self, sample_dataset):
        """Test default train/val/test split (70/15/15)"""
        train, val, test = sample_dataset.split(
            train_ratio=0.7,
            val_ratio=0.15,
            test_ratio=0.15,
            shuffle=False,
        )

        assert len(train) == 70
        assert len(val) == 15
        assert len(test) == 15
        assert len(train) + len(val) + len(test) == 100

    def test_split_ratios_sum_must_be_one(self, sample_dataset):
        """Test that split ratios must sum to 1.0"""
        with pytest.raises(AssertionError, match="比例之和必须为1"):
            sample_dataset.split(
                train_ratio=0.7,
                val_ratio=0.2,
                test_ratio=0.2,
            )

    def test_disjoint_datasets_no_overlap(self, sample_dataset):
        """Test that train/val/test datasets have no overlapping samples"""
        train, val, test = sample_dataset.split(
            train_ratio=0.7,
            val_ratio=0.15,
            test_ratio=0.15,
            shuffle=False,
        )

        _train_ids = set(range(len(train)))  # noqa: F841
        _val_ids = set(range(len(val)))  # noqa: F841
        _test_ids = set(range(len(test)))  # noqa: F841

        # Use original indices to verify disjoint
        train_orig = set(train.data.flatten().tolist())
        val_orig = set(val.data.flatten().tolist())
        test_orig = set(test.data.flatten().tolist())

        assert train_orig.isdisjoint(val_orig), "Train and Val overlap"
        assert train_orig.isdisjoint(test_orig), "Train and Test overlap"
        assert val_orig.isdisjoint(test_orig), "Val and Test overlap"

    def test_reproducibility_with_fixed_seed(self, sample_dataset):
        """Test that same random seed produces identical splits"""
        train1, val1, test1 = sample_dataset.split(
            train_ratio=0.7,
            val_ratio=0.15,
            test_ratio=0.15,
            shuffle=True,
            random_seed=42,
        )

        train2, val2, test2 = sample_dataset.split(
            train_ratio=0.7,
            val_ratio=0.15,
            test_ratio=0.15,
            shuffle=True,
            random_seed=42,
        )

        np.testing.assert_array_equal(train1.data, train2.data)
        np.testing.assert_array_equal(val1.data, val2.data)
        np.testing.assert_array_equal(test1.data, test2.data)

    def test_different_seeds_produce_different_splits(self, sample_dataset):
        """Test that different seeds produce different splits"""
        train1, _, _ = sample_dataset.split(
            train_ratio=0.7,
            val_ratio=0.15,
            test_ratio=0.15,
            shuffle=True,
            random_seed=42,
        )

        train2, _, _ = sample_dataset.split(
            train_ratio=0.7,
            val_ratio=0.15,
            test_ratio=0.15,
            shuffle=True,
            random_seed=123,
        )

        assert not np.array_equal(train1.data, train2.data)

    def test_custom_split_ratios(self, sample_dataset):
        """Test custom split ratios (e.g., 60/20/20)"""
        train, val, test = sample_dataset.split(
            train_ratio=0.6,
            val_ratio=0.2,
            test_ratio=0.2,
            shuffle=False,
        )

        assert len(train) == 60
        assert len(val) == 20
        assert len(test) == 20

    def test_split_with_labels(self, sample_dataset):
        """Test that labels are correctly split"""
        train, val, test = sample_dataset.split(
            train_ratio=0.7,
            val_ratio=0.15,
            test_ratio=0.15,
            shuffle=False,
        )

        assert train.labels is not None
        assert val.labels is not None
        assert test.labels is not None
        assert len(train.labels) == len(train.data)
        assert len(val.labels) == len(val.data)
        assert len(test.labels) == len(test.data)

    def test_split_without_labels(self):
        """Test split on dataset without labels"""
        data = np.arange(100).reshape(-1, 1)
        dataset = LNNDataset(data)

        train, val, test = dataset.split(
            train_ratio=0.7,
            val_ratio=0.15,
            test_ratio=0.15,
            shuffle=False,
        )

        assert train.labels is None
        assert val.labels is None
        assert test.labels is None

    def test_split_preserves_transforms(self, sample_dataset):
        """Test that split datasets preserve transform functions"""
        def transform(x):
            return x * 2
        dataset = LNNDataset(
            np.arange(100).reshape(-1, 1),
            np.arange(100),
            transform=transform,
        )

        train, val, test = dataset.split(shuffle=False)

        assert train.transform is transform
        assert val.transform is transform
        assert test.transform is transform

    def test_split_metadata_contains_split_info(self, sample_dataset):
        """Test that split datasets have metadata indicating their split type"""
        train, val, test = sample_dataset.split(shuffle=False)

        assert train.metadata.get("split") == "train"
        assert val.metadata.get("split") == "val"
        assert test.metadata.get("split") == "test"

    def test_split_logging_output(self, sample_dataset, caplog):
        """Test that split logs dataset sizes"""
        with caplog.at_level(logging.INFO):
            sample_dataset.split(
                train_ratio=0.7,
                val_ratio=0.15,
                test_ratio=0.15,
                shuffle=True,
                random_seed=42,
            )

        assert any(
            "Train:" in record.message
            and "Val:" in record.message
            and "Test:" in record.message
            for record in caplog.records
        )

    def test_split_size_consistency(self):
        """Test that total samples are preserved after split"""
        total_samples = 200
        data = np.arange(total_samples).reshape(-1, 1)
        dataset = LNNDataset(data)

        train, val, test = dataset.split(
            train_ratio=0.7,
            val_ratio=0.15,
            test_ratio=0.15,
            shuffle=False,
        )

        assert len(train) + len(val) + len(test) == total_samples

    def test_small_dataset_split(self):
        """Test split on very small dataset"""
        data = np.arange(10).reshape(-1, 1)
        labels = np.arange(10)
        dataset = LNNDataset(data, labels)

        train, val, test = dataset.split(
            train_ratio=0.7,
            val_ratio=0.15,
            test_ratio=0.15,
            shuffle=False,
        )

        assert len(train) + len(val) + len(test) == 10
        # With 10 samples: 7 train, 1 val, 2 test (due to int rounding)
        assert len(train) == 7

    def test_disjoint_with_shuffle(self, sample_dataset):
        """Test disjoint property holds even with shuffling"""
        train, val, test = sample_dataset.split(
            train_ratio=0.7,
            val_ratio=0.15,
            test_ratio=0.15,
            shuffle=True,
            random_seed=99,
        )

        train_ids = set(train.data.flatten().tolist())
        val_ids = set(val.data.flatten().tolist())
        test_ids = set(test.data.flatten().tolist())

        assert train_ids.isdisjoint(val_ids)
        assert train_ids.isdisjoint(test_ids)
        assert val_ids.isdisjoint(test_ids)

    def test_reproducibility_multiple_runs(self, sample_dataset):
        """Test that multiple runs with same seed produce identical results"""
        results = []
        for _ in range(3):
            train, val, test = sample_dataset.split(
                train_ratio=0.7,
                val_ratio=0.15,
                test_ratio=0.15,
                shuffle=True,
                random_seed=42,
            )
            results.append((train.data.copy(), val.data.copy(), test.data.copy()))

        for i in range(1, len(results)):
            np.testing.assert_array_equal(results[0][0], results[i][0])
            np.testing.assert_array_equal(results[0][1], results[i][1])
            np.testing.assert_array_equal(results[0][2], results[i][2])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
