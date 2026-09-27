"""LNN 数据集处理模块。

提供多源数据加载（CSV、JSON、HDF5、NumPy）、预处理、变换及缓存支持。
"""

import time
import logging
import numpy as np
from typing import Any, Callable, Dict, Optional, Tuple
from torch.utils.data import Dataset
import json
import os
import h5py

from .dataset_cache import DatasetCache
from app.core.safe_errors import safe_error_message

logger = logging.getLogger(__name__)


class LNNDataset(Dataset):
    """LNN 数据集类，支持多源数据加载与自定义预处理。"""

    def __init__(
        self,
        data: np.ndarray,
        labels: Optional[np.ndarray] = None,
        transform: Optional[Callable] = None,
        target_transform: Optional[Callable] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化数据集

        Args:
            data: 输入数据 (n_samples, features)
            labels: 标签数据 (n_samples,) 或 (n_samples, n_classes)
            transform: 数据变换函数
            target_transform: 标签变换函数
            metadata: 附加元数据
        """
        self.data = data
        self.labels = labels
        self.transform = transform
        self.target_transform = target_transform
        self.metadata = metadata or {}

        if data.ndim == 1:
            self.data = data.reshape(-1, 1)

        if labels is not None and labels.ndim == 1:
            self.labels = labels.reshape(-1, 1)

    def __len__(self) -> int:
        """返回数据集大小"""
        return len(self.data)

    def __getitem__(self, idx: int) -> Tuple[np.ndarray, ...]:
        """获取单个样本"""
        sample = self.data[idx]
        target = self.labels[idx] if self.labels is not None else None

        if self.transform:
            sample = self.transform(sample)

        if target is not None and self.target_transform:
            target = self.target_transform(target)

        if target is not None:
            return sample, target
        return sample

    def get_batch(self, batch_size: int, shuffle: bool = True) -> np.ndarray:
        """获取随机批次"""
        indices = np.random.permutation(len(self)) if shuffle else np.arange(len(self))
        return self.data[indices[:batch_size]]

    def split(
        self,
        train_ratio: float = 0.7,
        val_ratio: float = 0.15,
        test_ratio: float = 0.15,
        shuffle: bool = True,
        random_seed: Optional[int] = None,
    ) -> Tuple["LNNDataset", "LNNDataset", "LNNDataset"]:
        """
        划分训练集/验证集/测试集

        Args:
            train_ratio: 训练集比例 (默认0.7)
            val_ratio: 验证集比例 (默认0.15)
            test_ratio: 测试集比例 (默认0.15)
            shuffle: 是否打乱
            random_seed: 随机种子，确保划分可复现

        Returns:
            (训练集, 验证集, 测试集)
        """
        assert abs(train_ratio + val_ratio + test_ratio - 1.0) < 1e-6, "比例之和必须为1"

        n = len(self)
        rng = np.random.RandomState(random_seed)
        indices = rng.permutation(n) if shuffle else np.arange(n)

        train_end = int(n * train_ratio)
        val_end = int(n * (train_ratio + val_ratio))

        train_idx = indices[:train_end]
        val_idx = indices[train_end:val_end]
        test_idx = indices[val_end:]

        train_data = self.data[train_idx]
        train_labels = self.labels[train_idx] if self.labels is not None else None

        val_data = self.data[val_idx]
        val_labels = self.labels[val_idx] if self.labels is not None else None

        test_data = self.data[test_idx]
        test_labels = self.labels[test_idx] if self.labels is not None else None

        train_dataset = LNNDataset(
            train_data,
            train_labels,
            self.transform,
            self.target_transform,
            {**self.metadata, "split": "train"},
        )
        val_dataset = LNNDataset(
            val_data,
            val_labels,
            self.transform,
            self.target_transform,
            {**self.metadata, "split": "val"},
        )
        test_dataset = LNNDataset(
            test_data,
            test_labels,
            self.transform,
            self.target_transform,
            {**self.metadata, "split": "test"},
        )

        logger.info(
            "Train: %d samples, Val: %d samples, Test: %d samples",
            len(train_dataset),
            len(val_dataset),
            len(test_dataset),
        )

        return train_dataset, val_dataset, test_dataset

    @classmethod
    def from_numpy(
        cls, data: np.ndarray, labels: Optional[np.ndarray] = None
    ) -> "LNNDataset":
        """从numpy数组创建数据集"""
        return cls(data, labels)

    @classmethod
    def from_json(
        cls, json_path: str, data_key: str = "data", label_key: str = "labels"
    ) -> "LNNDataset":
        """从JSON文件创建数据集"""
        with open(json_path, "r") as f:
            json_data = json.load(f)

        data = np.array(json_data[data_key])
        labels = np.array(json_data[label_key]) if label_key in json_data else None

        return cls(data, labels, metadata={"source": json_path})

    @classmethod
    def from_csv(
        cls, csv_path: str, label_column: Optional[str] = None
    ) -> "LNNDataset":
        """从CSV文件创建数据集"""
        import pandas as pd

        df = pd.read_csv(csv_path)

        if label_column and label_column in df.columns:
            labels = df[label_column].values
            data = df.drop(columns=[label_column]).values
        else:
            labels = None
            data = df.values

        return cls(data, labels, metadata={"source": csv_path})


class TrainingDataPreprocessor:
    """训练数据预处理器

    注意：这是一个轻量级的训练专用预处理器，返回 ``np.ndarray``。
    生产推理路径使用 ``app.ai.lnn.preprocessing.DataPreprocessor``，
    后者返回 ``PreprocessingResult`` 并支持异常值检测、多模态特征等
    更丰富的功能。两者 API 不同，请勿混用。
    """

    def __init__(
        self,
        normalize: bool = True,
        standardize: bool = True,
        handle_missing: bool = True,
    ):
        self.normalize = normalize
        self.standardize = standardize
        self.handle_missing = handle_missing

        self.mean_: Optional[np.ndarray] = None
        self.std_: Optional[np.ndarray] = None
        self.min_: Optional[np.ndarray] = None
        self.max_: Optional[np.ndarray] = None

    def fit(self, data: np.ndarray) -> "TrainingDataPreprocessor":
        """拟合预处理器"""
        self.mean_ = np.mean(data, axis=0)
        self.std_ = np.std(data, axis=0)
        self.min_ = np.min(data, axis=0)
        self.max_ = np.max(data, axis=0)

        # 防止除零
        self.std_[self.std_ == 0] = 1.0

        return self

    def transform(self, data: np.ndarray) -> np.ndarray:
        """转换数据"""
        if self.handle_missing:
            data = self._handle_missing(data)

        if self.standardize:
            data = (data - self.mean_) / (self.std_ + 1e-10)

        if self.normalize:
            data = (data - self.min_) / ((self.max_ - self.min_) + 1e-10)

        return data

    def fit_transform(self, data: np.ndarray) -> np.ndarray:
        """拟合并转换"""
        self.fit(data)
        return self.transform(data)

    def _handle_missing(self, data: np.ndarray) -> np.ndarray:
        """处理缺失值"""
        col_mean = np.nanmean(data, axis=0)
        inds = np.where(np.isnan(data))
        data_copy = data.copy()
        data_copy[inds] = np.take(col_mean, inds[1])
        return data_copy


class FeatureExtractor:
    """特征提取器：时域和频域特征计算"""

    @staticmethod
    def extract_time_domain_features(signal: np.ndarray) -> np.ndarray:
        """
        提取时域特征

        Args:
            signal: 输入信号 (n_samples,) 或 (n_samples, signal_length)

        Returns:
            时域特征数组 (n_samples, 6)
            包含: RMS, 峰值, 峰峰值, 波形因子, 脉冲因子, 峭度
        """
        if signal.ndim == 1:
            signal = signal.reshape(1, -1)

        n_samples = signal.shape[0]
        features = np.zeros((n_samples, 6))

        for i in range(n_samples):
            s = signal[i]

            # RMS (均方根)
            rms = np.sqrt(np.mean(s**2))

            # 峰值
            peak = np.max(np.abs(s))

            # 峰峰值
            peak_to_peak = np.max(s) - np.min(s)

            # 波形因子 = RMS / 平均值(绝对值)
            mean_abs = np.mean(np.abs(s))
            waveform_factor = rms / (mean_abs + 1e-10)

            # 脉冲因子 = 峰值 / RMS
            impulse_factor = peak / (rms + 1e-10)

            # 峭度 (Kurtosis)
            std = np.std(s)
            kurtosis = np.mean(((s - np.mean(s)) / (std + 1e-10)) ** 4) - 3

            features[i] = [
                rms,
                peak,
                peak_to_peak,
                waveform_factor,
                impulse_factor,
                kurtosis,
            ]

        return features

    @staticmethod
    def extract_frequency_domain_features(
        signal: np.ndarray, fs: float = 1000.0
    ) -> np.ndarray:
        """
        提取频域特征

        Args:
            signal: 输入信号 (n_samples,) 或 (n_samples, signal_length)
            fs: 采样频率 (Hz)

        Returns:
            频域特征数组 (n_samples, 3)
            包含: 主频率, 频谱重心, 频谱能量
        """
        if signal.ndim == 1:
            signal = signal.reshape(1, -1)

        n_samples = signal.shape[0]
        features = np.zeros((n_samples, 3))

        for i in range(n_samples):
            s = signal[i]
            n = len(s)

            # FFT变换
            fft_vals = np.fft.rfft(s)
            fft_magnitude = np.abs(fft_vals)
            freqs = np.fft.rfftfreq(n, d=1.0 / fs)

            # 主频率 (能量最大的频率分量)
            dominant_freq = freqs[np.argmax(fft_magnitude)]

            # 频谱重心 (Spectral Centroid)
            total_magnitude = np.sum(fft_magnitude) + 1e-10
            spectral_centroid = np.sum(freqs * fft_magnitude) / total_magnitude

            # 频谱能量
            spectral_energy = np.sum(fft_magnitude**2)

            features[i] = [dominant_freq, spectral_centroid, spectral_energy]

        return features

    @staticmethod
    def extract_all_features(signal: np.ndarray, fs: float = 1000.0) -> np.ndarray:
        """
        提取所有特征（时域+频域）

        Args:
            signal: 输入信号
            fs: 采样频率

        Returns:
            特征数组 (n_samples, 9)
        """
        time_features = FeatureExtractor.extract_time_domain_features(signal)
        freq_features = FeatureExtractor.extract_frequency_domain_features(signal, fs)
        return np.hstack([time_features, freq_features])


class BoschCNCDataset(Dataset):
    """
    Bosch CNC数据集类

    处理HDF5格式文件，包含振动信号数据、良品/不良品标签及OP00至OP14多个工序数据

    集成数据集缓存机制：
    - 缓存优先策略：检查缓存→命中则直接返回→未命中则加载并缓存
    - 支持强制刷新缓存
    - 自动检测文件变更使缓存失效
    """

    def __init__(
        self,
        hdf5_path: str,
        operation: Optional[str] = None,
        extract_features: bool = True,
        fs: float = 1000.0,
        transform: Optional[Callable] = None,
        cache_data: bool = True,
        force_refresh: bool = False,
        dataset_cache: Optional[DatasetCache] = None,
    ):
        """
        初始化Bosch CNC数据集

        Args:
            hdf5_path: HDF5文件路径
            operation: 指定工序 (如'OP00', 'OP01'等), None表示使用所有工序
            extract_features: 是否提取特征
            fs: 采样频率 (Hz)
            transform: 数据变换函数（数据增强）
            cache_data: 是否缓存数据到内存
            force_refresh: 是否强制刷新缓存（跳过缓存检查）
            dataset_cache: 数据集缓存实例，None则使用默认配置
        """
        self.hdf5_path = hdf5_path
        self.operation = operation
        self.extract_features = extract_features
        self.fs = fs
        self.transform = transform
        self.cache_data = cache_data
        self.force_refresh = force_refresh
        self._dataset_cache = dataset_cache

        if not os.path.exists(hdf5_path):
            raise FileNotFoundError(
                f"数据集加载失败：HDF5 数据文件不存在: '{hdf5_path}'。可能原因：1) 文件路径配置错误；2) 数据集文件未下载或已删除。请检查配置文件中的数据路径，或运行数据下载脚本获取 HDF5 数据集文件。"  # noqa: E501
            )

        self._data: Optional[np.ndarray] = None
        self._labels: Optional[np.ndarray] = None
        self._signals: Optional[np.ndarray] = None
        self._labels_raw: Optional[np.ndarray] = None
        self._feature_extractor = FeatureExtractor()

        start_time = time.time()

        if cache_data:
            self._load_with_cache()
        else:
            self._load_dataset_info()

        load_time = time.time() - start_time
        logger.info(
            f"BoschCNCDataset loaded in {load_time * 1000:.2f}ms from {hdf5_path}"
        )

    def _load_with_cache(self):
        """
        使用缓存机制加载数据

        流程：
        1. 检查缓存（内存→磁盘）
        2. 缓存命中则直接返回
        3. 缓存未命中则执行完整加载流程：
           - HDF5文件解析
           - 特征提取
           - 数据预处理
           - 缓存存储
           - 数据返回
        """
        cache = self._get_or_create_cache()

        cached_result = cache.get(self.hdf5_path, force_refresh=self.force_refresh)

        if cached_result is not None:
            data, labels, metadata = cached_result
            self._data = data
            self._labels = labels
            self._signals = metadata.get("signals")
            self._labels_raw = labels
            logger.info("Cache hit for %s", self.hdf5_path)
            return

        logger.info("Cache miss for %s, loading from HDF5...", self.hdf5_path)
        self._load_from_hdf5(cache)

    def _get_or_create_cache(self) -> DatasetCache:
        """获取或创建数据集缓存实例"""
        if self._dataset_cache is None:
            self._dataset_cache = DatasetCache()
        return self._dataset_cache

    def _load_from_hdf5(self, cache: DatasetCache):
        """
        从HDF5文件加载数据并缓存

        Args:
            cache: 数据集缓存实例
        """
        try:
            with h5py.File(self.hdf5_path, "r") as f:
                if self.operation is not None:
                    group = f.get(self.operation)
                    if group is None:
                        raise ValueError(f"工序 {self.operation} 不存在于HDF5文件中")
                    signals = group["signals"][:]
                    labels = group["labels"][:]
                else:
                    all_signals = []
                    all_labels = []
                    for key in f.keys():
                        if key.startswith("OP"):
                            group = f[key]
                            if "signals" in group:
                                all_signals.append(group["signals"][:])
                                all_labels.append(group["labels"][:])

                    if not all_signals:
                        raise ValueError(
                            "数据集解析失败：HDF5 文件中未找到有效的工序数据。可能原因：1) HDF5 文件结构不符合预期格式；2) 文件已损坏。请检查 HDF5 文件结构，或重新下载数据集。"
                        )

                    signals = np.concatenate(all_signals, axis=0)
                    labels = np.concatenate(all_labels, axis=0)

                self._signals = signals
                self._labels_raw = labels

                if self.extract_features:
                    self._data = self._feature_extractor.extract_all_features(
                        signals, self.fs
                    )
                else:
                    self._data = signals

                self._labels = labels

                metadata = {
                    "signals": signals if not self.extract_features else None,
                    "operation": self.operation,
                    "extract_features": self.extract_features,
                    "fs": self.fs,
                }

                cache.put(self.hdf5_path, self._data, self._labels, metadata)
                logger.info("Data cached: %s", self.hdf5_path)

        except (OSError, KeyError, ValueError, TypeError, AttributeError) as e:
            # HDF5 文件读取可能因文件 IO、键访问、类型转换等失败
            # 异常链通过 from e 保留；面向调用者的 detail 使用 safe_error_message 脱敏
            raise RuntimeError(
                f"加载HDF5文件失败: {safe_error_message(e)}"
            ) from e

    def _load_and_cache_data(self):
        """兼容旧接口：加载并缓存HDF5数据到内存（不使用持久化缓存）"""
        cache = self._get_or_create_cache()
        self._load_from_hdf5(cache)

    def _load_dataset_info(self):
        """仅加载数据集信息（不缓存数据）"""
        with h5py.File(self.hdf5_path, "r") as f:
            self._available_operations = [k for k in f.keys() if k.startswith("OP")]

    def __len__(self) -> int:
        """返回数据集大小"""
        if self._data is None:
            with h5py.File(self.hdf5_path, "r") as f:
                if self.operation:
                    return f[self.operation]["signals"].shape[0]
                else:
                    total = 0
                    for key in f.keys():
                        if key.startswith("OP") and "signals" in f[key]:
                            total += f[key]["signals"].shape[0]
                    return total
        return len(self._data)

    def __getitem__(self, idx: int) -> Tuple[np.ndarray, np.ndarray]:
        """获取单个样本"""
        if self._data is None:
            # 实时读取模式
            with h5py.File(self.hdf5_path, "r") as f:
                if self.operation:
                    signal = f[self.operation]["signals"][idx]
                    label = f[self.operation]["labels"][idx]
                else:
                    # 跨工序索引映射：将全局索引映射到具体工序和局部索引
                    signal, label = self._map_global_index_to_operation(f, idx)
        else:
            signal = self._data[idx]
            label = self._labels[idx]

        # 数据增强
        if self.transform is not None:
            signal = self.transform(signal)

        # 如果未提取特征，则在此提取
        if self.extract_features and signal.ndim <= 2:
            if signal.ndim == 1:
                signal = signal.reshape(1, -1)
            signal = self._feature_extractor.extract_all_features(signal, self.fs)[0]

        return signal.astype(np.float32), label.astype(np.float32)

    def _map_global_index_to_operation(self, f: h5py.File, global_idx: int) -> Tuple[np.ndarray, np.ndarray]:
        """
        将全局索引映射到具体工序和局部索引

        Args:
            f: HDF5 文件对象
            global_idx: 全局索引

        Returns:
            (signal, label) 元组
        """
        cumulative = 0
        for key in sorted(f.keys()):
            if key.startswith("OP") and "signals" in f[key]:
                op_size = f[key]["signals"].shape[0]
                if global_idx < cumulative + op_size:
                    local_idx = global_idx - cumulative
                    return f[key]["signals"][local_idx], f[key]["labels"][local_idx]
                cumulative += op_size
        raise IndexError(f"索引 {global_idx} 超出数据集范围 (总样本数: {cumulative})")

    def get_signals(self) -> np.ndarray:
        """获取原始振动信号数据"""
        if not self.cache_data:
            raise RuntimeError("未缓存数据，无法获取原始信号")
        return self._signals

    def get_labels(self) -> np.ndarray:
        """获取标签数据"""
        if not self.cache_data:
            raise RuntimeError(
                "数据访问失败：尚未缓存数据集，无法获取标签数据。请先调用 load() 方法加载数据集到缓存，或直接访问数据集文件获取标签。"
            )
        return self._labels

    def split(
        self,
        train_ratio: float = 0.7,
        val_ratio: float = 0.15,
        test_ratio: float = 0.15,
        shuffle: bool = True,
        random_seed: Optional[int] = None,
    ) -> Tuple["BoschCNCDataset", "BoschCNCDataset", "BoschCNCDataset"]:
        """
        划分train/validation/test数据集

        Args:
            train_ratio: 训练集比例 (默认0.7)
            val_ratio: 验证集比例 (默认0.15)
            test_ratio: 测试集比例 (默认0.15)
            shuffle: 是否打乱
            random_seed: 随机种子

        Returns:
            (训练集, 验证集, 测试集)
        """
        assert abs(train_ratio + val_ratio + test_ratio - 1.0) < 1e-6, "比例之和必须为1"

        n = len(self)
        rng = np.random.RandomState(random_seed)
        indices = rng.permutation(n) if shuffle else np.arange(n)

        train_end = int(n * train_ratio)
        val_end = int(n * (train_ratio + val_ratio))

        train_idx = indices[:train_end]
        val_idx = indices[train_end:val_end]
        test_idx = indices[val_end:]

        # 创建子数据集
        train_data = self._data[train_idx]
        train_labels = self._labels[train_idx]
        val_data = self._data[val_idx]
        val_labels = self._labels[val_idx]
        test_data = self._data[test_idx]
        test_labels = self._labels[test_idx]

        train_dataset = BoschCNCDataset(
            hdf5_path=self.hdf5_path,
            operation=self.operation,
            extract_features=self.extract_features,
            fs=self.fs,
            transform=self.transform,
            cache_data=True,
        )
        train_dataset._data = train_data
        train_dataset._labels = train_labels

        val_dataset = BoschCNCDataset(
            hdf5_path=self.hdf5_path,
            operation=self.operation,
            extract_features=self.extract_features,
            fs=self.fs,
            transform=self.transform,
            cache_data=True,
        )
        val_dataset._data = val_data
        val_dataset._labels = val_labels

        test_dataset = BoschCNCDataset(
            hdf5_path=self.hdf5_path,
            operation=self.operation,
            extract_features=self.extract_features,
            fs=self.fs,
            transform=self.transform,
            cache_data=True,
        )
        test_dataset._data = test_data
        test_dataset._labels = test_labels

        return train_dataset, val_dataset, test_dataset


class DataAugmentation:
    """数据增强工具类"""

    @staticmethod
    def add_noise(signal: np.ndarray, noise_level: float = 0.01) -> np.ndarray:
        """
        信号加噪

        Args:
            signal: 原始信号
            noise_level: 噪声水平（相对于信号标准差的比例）

        Returns:
            加噪后的信号
        """
        noise = np.random.randn(*signal.shape) * noise_level * np.std(signal)
        return signal + noise

    @staticmethod
    def time_shift(signal: np.ndarray, max_shift: int = 10) -> np.ndarray:
        """
        时移变换

        Args:
            signal: 原始信号
            max_shift: 最大位移量（样本点数量）

        Returns:
            时移后的信号
        """
        shift = np.random.randint(-max_shift, max_shift)
        return np.roll(signal, shift)

    @staticmethod
    def amplitude_scaling(
        signal: np.ndarray, scale_range: Tuple[float, float] = (0.8, 1.2)
    ) -> np.ndarray:
        """
        幅度缩放

        Args:
            signal: 原始信号
            scale_range: 缩放范围

        Returns:
            缩放后的信号
        """
        scale = np.random.uniform(scale_range[0], scale_range[1])
        return signal * scale

    @staticmethod
    def time_stretch(
        signal: np.ndarray, stretch_range: Tuple[float, float] = (0.9, 1.1)
    ) -> np.ndarray:
        """
        时间伸缩（简化版）

        Args:
            signal: 原始信号
            stretch_range: 伸缩范围

        Returns:
            伸缩后的信号
        """
        factor = np.random.uniform(stretch_range[0], stretch_range[1])
        # 简化实现：使用插值
        original_length = len(signal)
        new_length = int(original_length * factor)
        stretched = np.interp(
            np.linspace(0, original_length - 1, new_length),
            np.arange(original_length),
            signal,
        )
        # 恢复原始长度
        return np.interp(
            np.linspace(0, new_length - 1, original_length),
            np.arange(new_length),
            stretched,
        )

    @staticmethod
    def compose_transforms(*transforms) -> Callable:
        """
        组合多个变换

        Args:
            *transforms: 变换函数列表

        Returns:
            组合变换函数
        """

        def composed(signal):
            for transform in transforms:
                signal = transform(signal)
            return signal

        return composed
