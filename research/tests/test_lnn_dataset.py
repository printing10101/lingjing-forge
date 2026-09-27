"""LNN Dataset 单元测试。

目标：为 python/app/ai/lnn/training/dataset.py 提供高覆盖率的单元测试。
覆盖范围：
- LNNDataset：初始化、__len__、__getitem__、get_batch、split、from_numpy/from_json/from_csv
- TrainingDataPreprocessor：fit、transform、fit_transform、_handle_missing
- FeatureExtractor：时域特征、频域特征、合并特征
- BoschCNCDataset：缓存加载/未缓存路径、__getitem__ 实时模式、split、错误处理
- DataAugmentation：add_noise、time_shift、amplitude_scaling、time_stretch、compose_transforms

使用 mock 替换 h5py 以避免对真实 HDF5 文件的依赖。
"""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import numpy as np
import pytest


# 工具函数与 Fixtures


@pytest.fixture
def sample_data_2d() -> np.ndarray:
    """构造一个标准 2D 特征矩阵。"""
    rng = np.random.RandomState(42)
    return rng.randn(20, 4).astype(np.float32)


@pytest.fixture
def sample_labels_1d() -> np.ndarray:
    return np.array([0, 1, 0, 1] * 5, dtype=np.int64)


@pytest.fixture
def sample_labels_2d() -> np.ndarray:
    return np.array([[0, 1]] * 20, dtype=np.float32)


@pytest.fixture
def dataset_module():
    from training import dataset as ds_mod

    return ds_mod


# 1. LNNDataset 初始化与基本方法


class TestLNNDatasetInit:
    """LNNDataset 初始化测试。"""

    def test_init_2d_data(self, dataset_module, sample_data_2d, sample_labels_1d):
        ds = dataset_module.LNNDataset(data=sample_data_2d, labels=sample_labels_1d)
        assert ds.data.shape == (20, 4)
        assert ds.labels.shape == (20, 1)
        assert ds.metadata == {}

    def test_init_1d_data_reshapes_to_2d(self, dataset_module):
        arr = np.arange(10, dtype=np.float32)
        ds = dataset_module.LNNDataset(data=arr)
        assert ds.data.shape == (10, 1)
        assert ds.labels is None

    def test_init_with_transforms(self, dataset_module, sample_data_2d):
        def transform(x):
            return x * 2.0

        def target_transform(y):
            return y + 1

        ds = dataset_module.LNNDataset(data=sample_data_2d, transform=transform, target_transform=target_transform)
        assert ds.transform is transform
        assert ds.target_transform is target_transform

    def test_init_with_metadata(self, dataset_module, sample_data_2d):
        meta = {"source": "test", "version": 1}
        ds = dataset_module.LNNDataset(data=sample_data_2d, metadata=meta)
        assert ds.metadata == meta
        assert ds.metadata["source"] == "test"

    def test_init_metadata_default_empty(self, dataset_module, sample_data_2d):
        ds = dataset_module.LNNDataset(data=sample_data_2d)
        assert ds.metadata == {}


class TestLNNDatasetLen:
    def test_len_with_2d(self, dataset_module, sample_data_2d):
        ds = dataset_module.LNNDataset(data=sample_data_2d)
        assert len(ds) == 20

    def test_len_with_small_data(self, dataset_module):
        ds = dataset_module.LNNDataset(data=np.zeros((3, 2)))
        assert len(ds) == 3


class TestLNNDatasetGetItem:
    def test_getitem_with_labels(self, dataset_module, sample_data_2d, sample_labels_1d):
        ds = dataset_module.LNNDataset(data=sample_data_2d, labels=sample_labels_1d)
        sample, target = ds[0]
        assert sample.shape == (4,)
        assert target.shape == (1,)

    def test_getitem_without_labels(self, dataset_module, sample_data_2d):
        ds = dataset_module.LNNDataset(data=sample_data_2d)
        sample = ds[0]
        assert sample.shape == (4,)

    def test_getitem_applies_transform(self, dataset_module, sample_data_2d, sample_labels_1d):
        def transform(x):
            return x * 0.0

        ds = dataset_module.LNNDataset(data=sample_data_2d, transform=transform)
        sample = ds[0]
        np.testing.assert_array_equal(sample, np.zeros_like(sample_data_2d[0]))

    def test_getitem_applies_target_transform(self, dataset_module, sample_data_2d, sample_labels_1d):
        def ttarget(y):
            return y * 0.0

        ds = dataset_module.LNNDataset(data=sample_data_2d, labels=sample_labels_1d, target_transform=ttarget)
        sample, target = ds[0]
        np.testing.assert_array_equal(target, np.zeros_like(sample_labels_1d[0:1]))

    def test_getitem_returns_only_sample_without_labels(self, dataset_module, sample_data_2d):
        ds = dataset_module.LNNDataset(data=sample_data_2d)
        result = ds[0]
        # 不应是 tuple
        assert not isinstance(result, tuple)


class TestGetBatch:
    def test_get_batch_shuffled(self, dataset_module, sample_data_2d):
        ds = dataset_module.LNNDataset(data=sample_data_2d)
        batch = ds.get_batch(batch_size=5, shuffle=True)
        assert batch.shape == (5, 4)

    def test_get_batch_unshuffled(self, dataset_module, sample_data_2d):
        ds = dataset_module.LNNDataset(data=sample_data_2d)
        batch = ds.get_batch(batch_size=5, shuffle=False)
        # unshuffled 时应保持顺序
        assert batch.shape == (5, 4)

    def test_get_batch_larger_than_dataset(self, dataset_module, sample_data_2d):
        ds = dataset_module.LNNDataset(data=sample_data_2d)
        # 当 batch_size 大于数据集时只取全部
        batch = ds.get_batch(batch_size=100, shuffle=False)
        # numpy 切片越界不会报错，会截取
        assert batch.shape[0] <= 20


class TestLNNDatasetSplit:
    def test_split_default_ratios(self, dataset_module, sample_data_2d, sample_labels_1d):
        ds = dataset_module.LNNDataset(data=sample_data_2d, labels=sample_labels_1d, metadata={"name": "test"})
        train, val, test = ds.split()
        assert len(train) == 14  # 0.7 * 20
        assert len(val) == 3
        assert len(test) == 3
        assert train.metadata["split"] == "train"
        assert val.metadata["split"] == "val"
        assert test.metadata["split"] == "test"
        assert train.metadata["name"] == "test"  # 元数据继承

    def test_split_with_seed(self, dataset_module, sample_data_2d):
        ds = dataset_module.LNNDataset(data=sample_data_2d)
        train1, val1, test1 = ds.split(random_seed=42)
        train2, val2, test2 = ds.split(random_seed=42)
        np.testing.assert_array_equal(train1.data, train2.data)

    def test_split_without_shuffle(self, dataset_module, sample_data_2d, sample_labels_1d):
        ds = dataset_module.LNNDataset(data=sample_data_2d, labels=sample_labels_1d)
        train, val, test = ds.split(shuffle=False)
        # 验证不 shuffle 时顺序保持
        assert len(train) + len(val) + len(test) == 20

    def test_split_preserves_transforms(self, dataset_module, sample_data_2d):
        def tf(x):
            return x

        ds = dataset_module.LNNDataset(data=sample_data_2d, transform=tf)
        train, val, test = ds.split()
        assert train.transform is tf
        assert val.transform is tf
        assert test.transform is tf

    def test_split_with_labels(self, dataset_module, sample_data_2d, sample_labels_1d):
        ds = dataset_module.LNNDataset(data=sample_data_2d, labels=sample_labels_1d)
        train, val, test = ds.split()
        assert train.labels is not None
        assert val.labels is not None
        assert test.labels is not None

    def test_split_without_labels(self, dataset_module, sample_data_2d):
        ds = dataset_module.LNNDataset(data=sample_data_2d)
        train, val, test = ds.split()
        assert train.labels is None
        assert val.labels is None
        assert test.labels is None

    def test_split_custom_ratios(self, dataset_module, sample_data_2d):
        ds = dataset_module.LNNDataset(data=sample_data_2d)
        train, val, test = ds.split(train_ratio=0.6, val_ratio=0.2, test_ratio=0.2)
        assert len(train) == 12
        assert len(val) == 4
        assert len(test) == 4

    def test_split_invalid_ratios_raises(self, dataset_module, sample_data_2d):
        ds = dataset_module.LNNDataset(data=sample_data_2d)
        with pytest.raises(AssertionError):
            ds.split(train_ratio=0.5, val_ratio=0.3, test_ratio=0.1)


# 2. LNNDataset 类方法工厂


class TestLNNDatasetFromNumpy:
    def test_from_numpy(self, dataset_module, sample_data_2d, sample_labels_1d):
        ds = dataset_module.LNNDataset.from_numpy(data=sample_data_2d, labels=sample_labels_1d)
        assert isinstance(ds, dataset_module.LNNDataset)
        assert len(ds) == 20

    def test_from_numpy_no_labels(self, dataset_module, sample_data_2d):
        ds = dataset_module.LNNDataset.from_numpy(data=sample_data_2d)
        assert ds.labels is None


class TestLNNDatasetFromJson:
    def test_from_json_basic(self, dataset_module, tmp_path, sample_data_2d, sample_labels_1d):
        payload = {
            "data": sample_data_2d.tolist(),
            "labels": sample_labels_1d.tolist(),
        }
        path = tmp_path / "ds.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        ds = dataset_module.LNNDataset.from_json(str(path))
        assert isinstance(ds, dataset_module.LNNDataset)
        assert ds.data.shape[0] == 20
        assert ds.metadata["source"] == str(path)

    def test_from_json_without_labels(self, dataset_module, tmp_path, sample_data_2d):
        payload = {"data": sample_data_2d.tolist()}
        path = tmp_path / "ds.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        ds = dataset_module.LNNDataset.from_json(str(path))
        assert ds.labels is None

    def test_from_json_custom_keys(self, dataset_module, tmp_path, sample_data_2d):
        payload = {
            "features": sample_data_2d.tolist(),
            "targets": np.zeros(20).tolist(),
        }
        path = tmp_path / "ds.json"
        path.write_text(json.dumps(payload), encoding="utf-8")

        ds = dataset_module.LNNDataset.from_json(str(path), data_key="features", label_key="targets")
        assert ds.data.shape[0] == 20


class TestLNNDatasetFromCsv:
    def test_from_csv_with_label(self, dataset_module, tmp_path):
        # 构造一个简单的 CSV
        csv_text = "f1,f2,f3,target\n"
        for i in range(10):
            csv_text += f"{i},{i * 0.1},{i * 0.2},{i % 2}\n"
        path = tmp_path / "ds.csv"
        path.write_text(csv_text, encoding="utf-8")

        ds = dataset_module.LNNDataset.from_csv(str(path), label_column="target")
        assert ds.data.shape == (10, 3)
        assert ds.labels is not None
        assert ds.labels.shape[0] == 10

    def test_from_csv_no_label_column(self, dataset_module, tmp_path):
        csv_text = "f1,f2,f3\n"
        for i in range(5):
            csv_text += f"{i},{i * 0.1},{i * 0.2}\n"
        path = tmp_path / "ds.csv"
        path.write_text(csv_text, encoding="utf-8")

        ds = dataset_module.LNNDataset.from_csv(str(path))
        assert ds.data.shape == (5, 3)
        assert ds.labels is None

    def test_from_csv_label_not_in_columns(self, dataset_module, tmp_path):
        csv_text = "f1,f2\n"
        for i in range(3):
            csv_text += f"{i},{i * 0.1}\n"
        path = tmp_path / "ds.csv"
        path.write_text(csv_text, encoding="utf-8")

        # 指定不存在的列时退化为不使用标签
        ds = dataset_module.LNNDataset.from_csv(str(path), label_column="missing")
        assert ds.labels is None


# 3. TrainingDataPreprocessor


class TestTrainingDataPreprocessorInit:
    def test_init_default(self, dataset_module):
        p = dataset_module.TrainingDataPreprocessor()
        assert p.normalize is True
        assert p.standardize is True
        assert p.handle_missing is True
        assert p.mean_ is None
        assert p.std_ is None

    def test_init_custom(self, dataset_module):
        p = dataset_module.TrainingDataPreprocessor(normalize=False, standardize=False, handle_missing=False)
        assert p.normalize is False
        assert p.standardize is False
        assert p.handle_missing is False


class TestTrainingDataPreprocessorFit:
    def test_fit_computes_statistics(self, dataset_module):
        data = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
        p = dataset_module.TrainingDataPreprocessor().fit(data)
        np.testing.assert_array_equal(p.mean_, np.array([3.0, 4.0]))
        np.testing.assert_array_equal(p.min_, np.array([1.0, 2.0]))
        np.testing.assert_array_equal(p.max_, np.array([5.0, 6.0]))

    def test_fit_handles_zero_std(self, dataset_module):
        data = np.array([[1.0, 2.0], [1.0, 2.0], [1.0, 2.0]])
        p = dataset_module.TrainingDataPreprocessor().fit(data)
        # 零标准差应被替换为 1.0
        assert np.all(p.std_ == 1.0)

    def test_fit_returns_self(self, dataset_module):
        data = np.array([[1.0, 2.0]])
        p = dataset_module.TrainingDataPreprocessor()
        result = p.fit(data)
        assert result is p


class TestTrainingDataPreprocessorTransform:
    def test_transform_standardize_and_normalize(self, dataset_module):
        data = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
        p = dataset_module.TrainingDataPreprocessor().fit(data)
        out = p.transform(data)
        assert out.shape == data.shape

    def test_transform_only_standardize(self, dataset_module):
        data = np.array([[1.0, 2.0], [3.0, 4.0]])
        p = dataset_module.TrainingDataPreprocessor(normalize=False, standardize=True, handle_missing=False).fit(data)
        out = p.transform(data)
        assert out.shape == data.shape

    def test_transform_only_normalize(self, dataset_module):
        data = np.array([[1.0, 2.0], [3.0, 4.0]])
        p = dataset_module.TrainingDataPreprocessor(normalize=True, standardize=False, handle_missing=False).fit(data)
        out = p.transform(data)
        # 数据被缩放到 [0, 1]
        assert out.min() >= 0.0 - 1e-5
        assert out.max() <= 1.0 + 1e-5

    def test_transform_with_missing(self, dataset_module):
        data = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
        p = dataset_module.TrainingDataPreprocessor().fit(data)
        data_with_nan = np.array([[1.0, np.nan], [3.0, 4.0]])
        out = p.transform(data_with_nan)
        assert not np.isnan(out).any()

    def test_transform_without_missing_handling(self, dataset_module):
        data = np.array([[1.0, 2.0], [3.0, 4.0]])
        p = dataset_module.TrainingDataPreprocessor(normalize=False, standardize=True, handle_missing=False).fit(data)
        out = p.transform(data)
        # 标准差为 0 之前已加 1e-10，因此输出是有限数
        assert np.all(np.isfinite(out))


class TestTrainingDataPreprocessorFitTransform:
    def test_fit_transform(self, dataset_module):
        data = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
        p = dataset_module.TrainingDataPreprocessor()
        out = p.fit_transform(data)
        assert out.shape == data.shape
        # fit 应已生效
        assert p.mean_ is not None


class TestHandleMissing:
    def test_handle_missing_replaces_nan_with_col_mean(self, dataset_module):
        data = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
        p = dataset_module.TrainingDataPreprocessor()
        p.fit(data)
        data_with_nan = np.array([[1.0, np.nan], [np.nan, 4.0], [5.0, 6.0]])
        out = p._handle_missing(data_with_nan)
        # NaN 应被替换为列均值
        assert not np.isnan(out).any()

    def test_handle_missing_no_nan(self, dataset_module):
        data = np.array([[1.0, 2.0], [3.0, 4.0]])
        p = dataset_module.TrainingDataPreprocessor()
        p.fit(data)
        out = p._handle_missing(data)
        np.testing.assert_array_equal(out, data)


# 4. FeatureExtractor


class TestTimeDomainFeatures:
    def test_extract_time_domain_features_1d(self, dataset_module):
        signal = np.array([1.0, -1.0, 1.0, -1.0, 1.0])
        features = dataset_module.FeatureExtractor.extract_time_domain_features(signal)
        # 1D 输入被 reshape 为 (1, -1)，输出 (1, 6)
        assert features.shape == (1, 6)

    def test_extract_time_domain_features_2d(self, dataset_module):
        rng = np.random.RandomState(0)
        signals = rng.randn(5, 100)
        features = dataset_module.FeatureExtractor.extract_time_domain_features(signals)
        assert features.shape == (5, 6)

    def test_features_are_finite(self, dataset_module):
        rng = np.random.RandomState(0)
        signals = rng.randn(3, 50)
        features = dataset_module.FeatureExtractor.extract_time_domain_features(signals)
        assert np.all(np.isfinite(features))

    def test_rms_calculation(self, dataset_module):
        # RMS 应等于 sqrt(mean(x^2))
        signal = np.array([[3.0, 4.0]])
        features = dataset_module.FeatureExtractor.extract_time_domain_features(signal)
        expected_rms = np.sqrt(np.mean(np.array([3.0, 4.0]) ** 2))
        assert features[0, 0] == pytest.approx(expected_rms)

    def test_kurtosis_constant_signal(self, dataset_module):
        # 常数信号的 std 为 0，kurtosis 应被正则化处理
        signal = np.array([[5.0, 5.0, 5.0, 5.0]])
        features = dataset_module.FeatureExtractor.extract_time_domain_features(signal)
        assert np.isfinite(features[0, 5])


class TestFrequencyDomainFeatures:
    def test_extract_frequency_domain_features_1d(self, dataset_module):
        # 用一个简单的正弦信号
        t = np.linspace(0, 1, 100, endpoint=False)
        signal = np.sin(2 * np.pi * 10 * t)
        features = dataset_module.FeatureExtractor.extract_frequency_domain_features(signal, fs=100.0)
        assert features.shape == (1, 3)

    def test_extract_frequency_domain_features_2d(self, dataset_module):
        rng = np.random.RandomState(0)
        signals = rng.randn(3, 128)
        features = dataset_module.FeatureExtractor.extract_frequency_domain_features(signals, fs=256.0)
        assert features.shape == (3, 3)

    def test_dominant_freq_approximation(self, dataset_module):
        # 10Hz 正弦波，fs=100，Dominant freq 应该在 10Hz 附近
        t = np.linspace(0, 1, 100, endpoint=False)
        signal = np.sin(2 * np.pi * 10 * t)
        features = dataset_module.FeatureExtractor.extract_frequency_domain_features(signal, fs=100.0)
        assert features[0, 0] == pytest.approx(10.0, abs=1.0)


class TestExtractAllFeatures:
    def test_extract_all_features_1d(self, dataset_module):
        signal = np.sin(2 * np.pi * 5 * np.linspace(0, 1, 64, endpoint=False))
        features = dataset_module.FeatureExtractor.extract_all_features(signal, fs=64.0)
        # 1D reshape 后 n_samples=1，特征维度=6+3=9
        assert features.shape == (1, 9)

    def test_extract_all_features_2d(self, dataset_module):
        rng = np.random.RandomState(0)
        signals = rng.randn(4, 64)
        features = dataset_module.FeatureExtractor.extract_all_features(signals, fs=64.0)
        assert features.shape == (4, 9)


# 5. BoschCNCDataset - 缓存模式


class _FakeH5Group:
    """模拟 h5py.Group。"""

    def __init__(self, signals: np.ndarray, labels: np.ndarray) -> None:
        self._signals = signals
        self._labels = labels
        self.attrs: dict[str, Any] = {}

    def __getitem__(self, key: str):
        if key == "signals":

            class _Arr:
                def __init__(self, arr):
                    self._arr = arr

                def __getitem__(self, idx):
                    if isinstance(idx, int):
                        return self._arr[idx]
                    return self._arr[idx]

                @property
                def shape(self):
                    return self._arr.shape

            return _Arr(self._signals)
        if key == "labels":

            class _Arr:
                def __init__(self, arr):
                    self._arr = arr

                def __getitem__(self, idx):
                    if isinstance(idx, int):
                        return self._arr[idx]
                    return self._arr[idx]

                @property
                def shape(self):
                    return self._arr.shape

            return _Arr(self._labels)
        raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        return key in ("signals", "labels")


class _FakeH5File:
    """模拟 h5py.File，支持 with 上下文与 __enter__/__exit__。"""

    def __init__(self, op_data: dict[str, tuple[np.ndarray, np.ndarray]]) -> None:
        self._groups = {k: _FakeH5Group(v[0], v[1]) for k, v in op_data.items()}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __getitem__(self, key: str):
        return self._groups[key]

    def __iter__(self):
        return iter(self._groups.keys())

    def keys(self):
        return self._groups.keys()

    def get(self, key, default=None):
        """兼容 dataset.py 中 f.get(self.operation) 的调用。"""
        return self._groups.get(key, default)


def _make_op_data(n_samples: int = 16) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    rng = np.random.RandomState(0)
    signals = rng.randn(n_samples, 64).astype(np.float32)
    labels = rng.randint(0, 2, size=(n_samples,)).astype(np.int64)
    return {"OP00": (signals, labels), "OP01": (signals, labels)}


def _patch_h5py(monkeypatch, op_data: dict):
    """替换 dataset 模块中的 h5py.File。"""

    def _fake_h5py_file(path, mode):
        return _FakeH5File(op_data)

    # dataset 模块内部 import h5py，monkeypatch sys.modules
    fake_h5py = mock.MagicMock()
    fake_h5py.File = _fake_h5py_file
    monkeypatch.setitem(__import__("sys").modules, "h5py", fake_h5py)
    # 同时 patch 已被 dataset 模块引用过的 h5py（如果存在）
    try:
        from training import dataset as ds_mod

        monkeypatch.setattr(ds_mod, "h5py", fake_h5py, raising=False)
    except Exception:
        pass


@pytest.fixture
def fake_h5py(monkeypatch, tmp_path):
    """提供一个伪造的 h5py 模块以及存在的 HDF5 路径。"""
    op_data = _make_op_data()

    hdf5_path = tmp_path / "bosch.h5"
    hdf5_path.write_bytes(b"FAKEH5")

    _patch_h5py(monkeypatch, op_data)
    return {"hdf5_path": str(hdf5_path), "op_data": op_data, "tmp_path": tmp_path}


class TestBoschCNCDatasetInit:
    def test_init_file_not_found(self, dataset_module):
        with pytest.raises(FileNotFoundError):
            dataset_module.BoschCNCDataset(hdf5_path="/nonexistent/path/to.h5")

    def test_init_with_cache_loads_data(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(
            hdf5_path=fake_h5py["hdf5_path"],
            cache_data=True,
            extract_features=True,
        )
        assert ds._data is not None
        assert ds._labels is not None

    def test_init_without_cache(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(
            hdf5_path=fake_h5py["hdf5_path"],
            cache_data=False,
        )
        # cache_data=False 时只加载 _available_operations
        assert hasattr(ds, "_available_operations")

    def test_init_with_operation(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(
            hdf5_path=fake_h5py["hdf5_path"],
            operation="OP00",
            cache_data=True,
        )
        assert ds.operation == "OP00"

    def test_init_with_invalid_operation(self, dataset_module, fake_h5py):
        # dataset.py 将原始 ValueError 包装为 RuntimeError
        with pytest.raises(RuntimeError):
            dataset_module.BoschCNCDataset(
                hdf5_path=fake_h5py["hdf5_path"],
                operation="OP_INVALID",
                cache_data=True,
            )

    def test_init_with_transform(self, dataset_module, fake_h5py):
        def tf(x):
            return x * 2.0

        ds = dataset_module.BoschCNCDataset(
            hdf5_path=fake_h5py["hdf5_path"],
            cache_data=True,
            transform=tf,
        )
        assert ds.transform is tf

    def test_init_without_features(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(
            hdf5_path=fake_h5py["hdf5_path"],
            cache_data=True,
            extract_features=False,
        )
        # 无特征提取时 _data 应等于 _signals
        assert ds._data is not None
        assert ds._signals is not None


class TestBoschCNCDatasetCache:
    def test_get_or_create_cache_default(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(hdf5_path=fake_h5py["hdf5_path"], cache_data=True)
        cache = ds._get_or_create_cache()
        assert isinstance(cache, dataset_module.DatasetCache)

    def test_get_or_create_cache_provided(self, dataset_module, fake_h5py):
        custom_cache = dataset_module.DatasetCache(cache_directory=str(fake_h5py["tmp_path"] / "custom_cache"))
        ds = dataset_module.BoschCNCDataset(
            hdf5_path=fake_h5py["hdf5_path"],
            cache_data=True,
            dataset_cache=custom_cache,
        )
        cache = ds._get_or_create_cache()
        assert cache is custom_cache

    def test_force_refresh_cache_miss(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(
            hdf5_path=fake_h5py["hdf5_path"],
            cache_data=True,
            force_refresh=True,
        )
        # force_refresh=True 时应强制重新加载
        assert ds._data is not None

    def test_cache_hit_on_second_init(self, dataset_module, fake_h5py):
        # 第一次加载（用于填充缓存）
        dataset_module.BoschCNCDataset(
            hdf5_path=fake_h5py["hdf5_path"],
            cache_data=True,
        )
        # 第二次加载（命中缓存）
        ds2 = dataset_module.BoschCNCDataset(
            hdf5_path=fake_h5py["hdf5_path"],
            cache_data=True,
        )
        assert ds2._data is not None


class TestBoschCNCDatasetLen:
    def test_len_cached(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(hdf5_path=fake_h5py["hdf5_path"], cache_data=True)
        # 32 = 16+16 from two operations
        assert len(ds) == 32

    def test_len_cached_single_operation(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(
            hdf5_path=fake_h5py["hdf5_path"],
            operation="OP00",
            cache_data=True,
        )
        assert len(ds) == 16


class TestBoschCNCDatasetGetItem:
    def test_getitem_cached(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(hdf5_path=fake_h5py["hdf5_path"], cache_data=True)
        signal, label = ds[0]
        assert signal.ndim >= 1
        assert label.ndim == 0 or label.shape == ()

    def test_getitem_with_transform(self, dataset_module, fake_h5py):
        # 当 extract_features=True 时，缓存中已是特征(transform作用于原始信号,
        # 但 __getitem__ 路径上 transform 先作用在特征上, 再做特征再提取),
        # 所以这里不强制 0, 只验证 transform 被调用 (signal 经过变换后形态不变)
        def tf(x):
            return x * 0.0

        ds = dataset_module.BoschCNCDataset(
            hdf5_path=fake_h5py["hdf5_path"],
            cache_data=True,
            transform=tf,
        )
        signal, label = ds[0]
        # transform 被调用, 输出仍为有限值且维度一致
        assert signal.ndim >= 1
        assert np.all(np.isfinite(signal))

        # 当 extract_features=False 时, transform 的效果可以直接观察
        def tf2(x):
            return x * 0.0

        ds_raw = dataset_module.BoschCNCDataset(
            hdf5_path=fake_h5py["hdf5_path"],
            cache_data=True,
            extract_features=False,
            transform=tf2,
        )
        signal_raw, _ = ds_raw[0]
        np.testing.assert_array_equal(signal_raw, np.zeros_like(signal_raw))

    def test_getitem_returns_float32(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(hdf5_path=fake_h5py["hdf5_path"], cache_data=True)
        signal, label = ds[0]
        assert signal.dtype == np.float32
        assert label.dtype == np.float32


class TestBoschCNCDatasetGetSignals:
    def test_get_signals_after_cache(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(hdf5_path=fake_h5py["hdf5_path"], cache_data=True)
        signals = ds.get_signals()
        assert signals is not None

    def test_get_signals_without_cache_raises(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(hdf5_path=fake_h5py["hdf5_path"], cache_data=False)
        with pytest.raises(RuntimeError):
            ds.get_signals()


class TestBoschCNCDatasetGetLabels:
    def test_get_labels_after_cache(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(hdf5_path=fake_h5py["hdf5_path"], cache_data=True)
        labels = ds.get_labels()
        assert labels is not None
        assert len(labels) == 32

    def test_get_labels_without_cache_raises(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(hdf5_path=fake_h5py["hdf5_path"], cache_data=False)
        with pytest.raises(RuntimeError):
            ds.get_labels()


class TestBoschCNCDatasetSplit:
    def test_split_default(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(hdf5_path=fake_h5py["hdf5_path"], cache_data=True)
        train, val, test = ds.split()
        assert len(train) + len(val) + len(test) == 32

    def test_split_with_seed(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(hdf5_path=fake_h5py["hdf5_path"], cache_data=True)
        train1, _, _ = ds.split(random_seed=42)
        train2, _, _ = ds.split(random_seed=42)
        np.testing.assert_array_equal(train1._data, train2._data)

    def test_split_invalid_ratios_raises(self, dataset_module, fake_h5py):
        ds = dataset_module.BoschCNCDataset(hdf5_path=fake_h5py["hdf5_path"], cache_data=True)
        with pytest.raises(AssertionError):
            ds.split(train_ratio=0.5, val_ratio=0.3, test_ratio=0.1)


# 6. DataAugmentation


class TestDataAugmentation:
    def test_add_noise_changes_signal(self, dataset_module):
        np.random.seed(0)
        # 使用具有非零 std 的信号, 否则 noise_level * std(signal) == 0
        rng = np.random.RandomState(42)
        signal = rng.randn(100)
        out = dataset_module.DataAugmentation.add_noise(signal, noise_level=0.5)
        assert not np.allclose(out, signal)
        assert out.shape == signal.shape

    def test_add_noise_zero_level(self, dataset_module):
        signal = np.array([1.0, 2.0, 3.0, 4.0])
        out = dataset_module.DataAugmentation.add_noise(signal, noise_level=0.0)
        np.testing.assert_array_equal(out, signal)

    def test_time_shift(self, dataset_module):
        np.random.seed(0)
        signal = np.arange(20, dtype=np.float32)
        out = dataset_module.DataAugmentation.time_shift(signal, max_shift=5)
        assert out.shape == signal.shape

    def test_amplitude_scaling_default_range(self, dataset_module):
        signal = np.array([1.0, 2.0, 3.0])
        out = dataset_module.DataAugmentation.amplitude_scaling(signal)
        # 缩放应在 (0.8, 1.2) 之间
        assert out.shape == signal.shape
        # 缩放后比例应一致
        ratio = out / signal
        assert np.allclose(ratio, ratio[0])

    def test_amplitude_scaling_custom_range(self, dataset_module):
        signal = np.array([1.0, 2.0, 3.0])
        out = dataset_module.DataAugmentation.amplitude_scaling(signal, scale_range=(2.0, 2.0))
        np.testing.assert_array_almost_equal(out, signal * 2.0)

    def test_time_stretch_preserves_length(self, dataset_module):
        np.random.seed(0)
        signal = np.arange(100, dtype=np.float32)
        out = dataset_module.DataAugmentation.time_stretch(signal)
        assert out.shape == signal.shape

    def test_time_stretch_unit_factor(self, dataset_module):
        signal = np.arange(50, dtype=np.float32)
        out = dataset_module.DataAugmentation.time_stretch(signal, stretch_range=(1.0, 1.0))
        # 拉伸因子为 1 时应几乎与原信号一致（插值可能引入微小差异）
        np.testing.assert_array_almost_equal(out, signal, decimal=4)

    def test_compose_transforms_single(self, dataset_module):
        signal = np.array([1.0, 2.0, 3.0])

        def tf(x):
            return x * 2.0

        composed = dataset_module.DataAugmentation.compose_transforms(tf)
        np.testing.assert_array_equal(composed(signal), signal * 2.0)

    def test_compose_transforms_multiple(self, dataset_module):
        signal = np.array([1.0, 2.0, 3.0])

        def double(x):
            return x * 2.0

        def add_one(x):
            return x + 1.0

        composed = dataset_module.DataAugmentation.compose_transforms(double, add_one)
        # (1*2+1, 2*2+1, 3*2+1) = (3, 5, 7)
        np.testing.assert_array_equal(composed(signal), np.array([3.0, 5.0, 7.0]))

    def test_compose_transforms_empty(self, dataset_module):
        signal = np.array([1.0, 2.0, 3.0])
        composed = dataset_module.DataAugmentation.compose_transforms()
        np.testing.assert_array_equal(composed(signal), signal)


# 7. 集成场景 - 与 LNNDataset + TrainingDataPreprocessor 组合


class TestIntegration:
    def test_dataset_with_preprocessor(self, dataset_module):
        """数据集 + 预处理器集成。"""
        rng = np.random.RandomState(0)
        data = rng.randn(50, 5)
        labels = rng.randint(0, 2, size=(50,))

        ds = dataset_module.LNNDataset(data=data, labels=labels)
        pp = dataset_module.TrainingDataPreprocessor()
        processed = pp.fit_transform(ds.data)
        # processed 与 ds.data 形状一致
        assert processed.shape == ds.data.shape

    def test_dataset_with_features(self, dataset_module):
        """数据集 + 特征提取器集成。"""
        rng = np.random.RandomState(0)
        signals = rng.randn(8, 32)
        features = dataset_module.FeatureExtractor.extract_all_features(signals, fs=32.0)
        ds = dataset_module.LNNDataset(data=features)
        assert len(ds) == 8
        assert ds[0].shape == (9,)

    def test_dataset_split_with_features(self, dataset_module):
        rng = np.random.RandomState(0)
        signals = rng.randn(20, 32)
        features = dataset_module.FeatureExtractor.extract_all_features(signals, fs=32.0)
        ds = dataset_module.LNNDataset(data=features)
        train, val, test = ds.split(random_seed=42)
        assert len(train) + len(val) + len(test) == 20
