"""Bosch Dataset Processing Module.

Provides comprehensive data loading, cleaning, preprocessing, and feature
engineering for Bosch-format datasets (CSV/Excel). Supports missing value
handling, outlier detection, standardization, temporal feature extraction,
and data augmentation through noise injection and sliding windows.

Key components:
    - DatasetConfig: Configuration for dataset loading and processing.
    - DataInfo: Dataset metadata (shape, missing values, data types).
    - BoschDatasetProcessor: Full-featured dataset processor.

Example:
    >>> processor = BoschDatasetProcessor(
    ...     dataset_path="data/bosch.csv",
    ...     target_column="quality",
    ...     task_type="classification",
    ... )
    >>> X_train, y_train = processor.load_and_prepare(split="train")
"""

import logging
import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field
from pathlib import Path

try:
    import torch
    from torch.utils.data import Dataset, DataLoader, TensorDataset

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    Dataset = object

from sklearn.preprocessing import StandardScaler, MinMaxScaler, RobustScaler
from sklearn.impute import SimpleImputer
from sklearn.model_selection import train_test_split


# 合成数据生成参数（用于 generate_cutting_force_data 方法）
FEATURE_NOISE_SCALE: float = 0.5  # 特征噪声标准差
FEATURE_OFFSET_RANGE: float = 2.0  # 特征偏移范围
DEFAULT_MISSING_RATE: float = 0.02  # 缺失值注入率（2%）

# 切削力合成系数（线性组合权重，总和应为 1.0）
CUTTING_FORCE_WEIGHTS = {
    "spindle_speed": 0.3,
    "feed_rate": 0.25,
    "depth_of_cut": 0.2,
    "vibration_x": 0.15,
    "temperature": 0.1,
}

# 刀具磨损合成系数
TOOL_WEAR_WEIGHTS = {
    "cutting_force": 0.4,
    "spindle_speed": 0.2,
    "temperature": 0.15,
    "vibration_x": 0.1,
}

logger = logging.getLogger(__name__)


@dataclass
class BoschDataConfig:
    """Bosch数据集配置"""

    target_columns: List[str] = field(default_factory=lambda: ["cutting_force"])
    feature_columns: Optional[List[str]] = None
    time_column: str = "timestamp"
    test_size: float = 0.2
    val_size: float = 0.1
    random_state: int = 42
    normalization_method: str = "standard"
    imputation_strategy: str = "mean"
    outlier_method: str = "iqr"
    outlier_threshold: float = 1.5
    window_size: int = 50
    window_step: int = 10
    batch_size: int = 32
    num_workers: int = 0


class BoschDatasetProcessor:
    """
    Bosch数据集处理器

    功能：
    - 加载Bosch切削力/磨损数据集
    - 数据清洗与预处理
    - 特征工程
    - 数据集划分与加载
    """

    def __init__(self, config: Optional[BoschDataConfig] = None):
        self.config = config or BoschDataConfig()
        self.raw_data: Optional[pd.DataFrame] = None
        self.processed_data: Optional[pd.DataFrame] = None
        self.feature_data: Optional[np.ndarray] = None
        self.target_data: Optional[np.ndarray] = None
        self.scaler = None
        self.imputer = None
        self.feature_names: List[str] = []
        self._stats: Dict[str, Any] = {}

    def load_data(self, data_path: str, **read_kwargs) -> pd.DataFrame:
        """
        加载Bosch数据集

        Args:
            data_path: 数据文件路径（支持CSV、Excel、Parquet）
            **read_kwargs: 传递给pd.read_csv/pd.read_excel的额外参数

        Returns:
            原始数据DataFrame
        """
        path = Path(data_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Bosch 数据集加载失败：找不到数据文件 '{data_path}'。可能原因：1) 文件路径配置错误；2) Bosch 数据集未下载或已删除。请检查配置文件中的数据路径，或运行 Bosch 数据集下载脚本获取数据文件。"  # noqa: E501
            )

        suffix = path.suffix.lower()
        if suffix == ".csv":
            self.raw_data = pd.read_csv(data_path, **read_kwargs)
        elif suffix in (".xls", ".xlsx"):
            self.raw_data = pd.read_excel(data_path, **read_kwargs)
        elif suffix == ".parquet":
            self.raw_data = pd.read_parquet(data_path)
        else:
            raise ValueError(
                f"Bosch 数据集加载失败：不支持的文件格式 '{suffix}'。支持的文件格式包括：'.csv'（CSV 文本文件）、'.xls'/.xlsx（Excel 文件）、'.parquet'（Parquet 列式存储文件）。请将数据转换为支持的格式，或检查文件扩展名是否正确。"  # noqa: E501
            )

        logger.info("Loaded data from %s: %s", data_path, self.raw_data.shape)
        self._stats["raw_shape"] = self.raw_data.shape
        self._stats["raw_columns"] = list(self.raw_data.columns)

        return self.raw_data

    def load_data_from_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        直接从DataFrame对象加载数据

        Args:
            df: pandas DataFrame

        Returns:
            原始数据DataFrame
        """
        self.raw_data = df.copy()
        logger.info("Loaded data from DataFrame: %s", self.raw_data.shape)
        self._stats["raw_shape"] = self.raw_data.shape
        self._stats["raw_columns"] = list(self.raw_data.columns)

        return self.raw_data

    def clean_data(
        self,
        drop_duplicates: bool = True,
        handle_missing: bool = True,
        handle_outliers: bool = True,
    ) -> pd.DataFrame:
        """数据清洗（无数据泄漏版）。

        缺失值处理使用非统计方法（fillna(0)），不拟合 imputer。
        imputer 的拟合延迟到 ``split_data`` 后仅用训练集进行。

        Args:
            drop_duplicates: 是否删除重复行
            handle_missing: 是否处理缺失值
            handle_outliers: 是否处理异常值

        Returns:
            清洗后的DataFrame
        """
        if self.raw_data is None:
            raise ValueError(
                "Bosch 数据集清洗失败：尚未加载原始数据。请先调用 load_data() 方法加载数据文件，再进行清洗操作。"
            )

        df = self.raw_data.copy()
        initial_rows = len(df)

        if drop_duplicates:
            df = df.drop_duplicates()
            dropped = initial_rows - len(df)
            self._stats["duplicates_removed"] = dropped
            logger.info("Removed %s duplicate rows", dropped)

        if handle_missing:
            missing_before = df.isnull().sum().sum()
            df = self._handle_missing_values(df)
            missing_after = df.isnull().sum().sum()
            self._stats["missing_values_filled"] = missing_before - missing_after

        if handle_outliers:
            df = self._handle_outliers(df)

        self.processed_data = df
        self._stats["cleaned_shape"] = df.shape
        logger.info("Data cleaning complete: %s", df.shape)

        return df

    def engineer_features(
        self,
        add_lag_features: bool = True,
        lag_steps: Optional[List[int]] = None,
        add_rolling_stats: bool = True,
        rolling_windows: Optional[List[int]] = None,
        add_diff_features: bool = False,
        add_interaction: bool = False,
    ) -> np.ndarray:
        """特征工程（无数据泄漏版）。

        构建滞后/滚动/差分/交互特征，返回**未标准化**的原始特征矩阵。
        标准化已从本方法移除，改由 ``split_data`` 在数据划分后仅用训练集
        拟合 scaler，避免测试集统计量泄漏。

        Args:
            add_lag_features: 是否添加滞后特征
            lag_steps: 滞后步数列表
            add_rolling_stats: 是否添加滚动统计特征
            rolling_windows: 滚动窗口大小列表
            add_diff_features: 是否添加差分特征
            add_interaction: 是否添加交互特征

        Returns:
            未标准化的特征矩阵 numpy.ndarray（标准化在 split_data 中进行）
        """
        if self.processed_data is None:
            raise ValueError(
                "Bosch 数据集特征工程失败：尚未完成数据清洗。请先调用 clean_data() 方法清洗数据，再进行特征工程操作。"
            )

        df = self.processed_data.copy()
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()

        target_cols = self.config.target_columns
        feature_cols = self.config.feature_columns or [c for c in numeric_cols if c not in target_cols]

        lag_steps = lag_steps or [1, 3, 5]
        rolling_windows = rolling_windows or [5, 10, 20]

        new_features = []

        if add_lag_features:
            for col in feature_cols:
                for lag in lag_steps:
                    lag_col = f"{col}_lag_{lag}"
                    df[lag_col] = df[col].shift(lag)
                    new_features.append(lag_col)

        if add_rolling_stats:
            for col in feature_cols:
                for window in rolling_windows:
                    df[f"{col}_roll_mean_{window}"] = df[col].rolling(window).mean()
                    df[f"{col}_roll_std_{window}"] = df[col].rolling(window).std()
                    new_features.extend(
                        [
                            f"{col}_roll_mean_{window}",
                            f"{col}_roll_std_{window}",
                        ]
                    )

        if add_diff_features:
            for col in feature_cols:
                df[f"{col}_diff"] = df[col].diff()
                new_features.append(f"{col}_diff")

        if add_interaction:
            for i, col1 in enumerate(feature_cols[:5]):
                for col2 in feature_cols[i + 1 : 5]:
                    interaction_name = f"{col1}_x_{col2}"
                    df[interaction_name] = df[col1] * df[col2]
                    new_features.append(interaction_name)

        all_feature_cols = feature_cols + new_features
        df = df.dropna()

        self.processed_data = df
        self.feature_names = all_feature_cols

        X = df[all_feature_cols].values.astype(np.float32)
        y = df[target_cols].values.astype(np.float32) if target_cols else None

        # 注意：此处不对 X 进行标准化，避免在数据划分前使用全量数据统计量
        # 造成测试集信息泄漏。scaler 将在 split_data 后仅用 X_train 拟合。
        logger.warning(
            "engineer_features: 已跳过特征标准化（防止数据泄漏），"
            "返回未标准化的原始特征矩阵。scaler 将在 split_data 后仅用训练集拟合。"
        )

        self.feature_data = X
        self.target_data = y

        self._stats["feature_count"] = X.shape[1]
        self._stats["sample_count"] = X.shape[0]

        logger.info(f"Feature engineering complete: X={X.shape}, y={y.shape if y is not None else None}")

        return X

    def split_data(
        self,
        return_tensors: bool = False,
    ) -> Union[
        Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray],
        Tuple[Any, Any, Any],
    ]:
        """划分训练/验证/测试集，并在划分后仅用训练集拟合 imputer 和 scaler。

        无数据泄漏流程：
            1. 先按 ``random_state`` 将 ``feature_data`` 划分为 train/val/test
            2. 仅用 ``X_train`` 拟合 ``SimpleImputer``，再 transform 三个子集
            3. 仅用 ``X_train`` 拟合 scaler（Standard/MinMax/Robust），再 transform
               三个子集

        拟合完成后 ``self.imputer`` 和 ``self.scaler`` 可用于推理时 transform。

        Args:
            return_tensors: 是否返回PyTorch张量

        Returns:
            (X_train, X_val, X_test, y_train, y_val, y_test) 或 DataLoader。
            X 子集均已用训练集统计量完成插补与标准化。
        """
        if self.feature_data is None:
            raise ValueError(
                "Bosch 数据集划分失败：尚未完成特征工程。请先调用 engineer_features() 方法提取特征，再进行数据集划分操作。"
            )

        X = self.feature_data
        y = self.target_data

        test_size = self.config.test_size
        val_size = self.config.val_size / (1 - test_size)

        # 先划分，再拟合预处理器，避免测试集统计信息泄漏
        X_temp, X_test, y_temp, y_test = train_test_split(
            X, y, test_size=test_size, random_state=self.config.random_state
        )

        X_train, X_val, y_train, y_val = train_test_split(
            X_temp, y_temp, test_size=val_size, random_state=self.config.random_state
        )

        # 仅用训练集拟合 imputer，防止 val/test 统计量泄漏
        strategy = self.config.imputation_strategy
        if strategy in ("median", "most_frequent"):
            self.imputer = SimpleImputer(strategy=strategy)
        else:
            self.imputer = SimpleImputer(strategy="mean")
        X_train = self.imputer.fit_transform(X_train)
        X_val = self.imputer.transform(X_val)
        X_test = self.imputer.transform(X_test)

        # 仅用训练集拟合 scaler，防止 val/test 统计量泄漏
        method = self.config.normalization_method
        if method == "minmax":
            self.scaler = MinMaxScaler()
        elif method == "robust":
            self.scaler = RobustScaler()
        else:
            self.scaler = StandardScaler()
        X_train = self.scaler.fit_transform(X_train)
        X_val = self.scaler.transform(X_val)
        X_test = self.scaler.transform(X_test)

        # 转回 float32 以兼容下游 PyTorch 张量
        X_train = X_train.astype(np.float32)
        X_val = X_val.astype(np.float32)
        X_test = X_test.astype(np.float32)

        self._stats["train_size"] = len(X_train)
        self._stats["val_size"] = len(X_val)
        self._stats["test_size"] = len(X_test)

        logger.info(
            "split_data: imputer 和 scaler 已仅用训练集拟合（无数据泄漏）。"
            f"train={X_train.shape}, val={X_val.shape}, test={X_test.shape}"
        )

        if return_tensors and HAS_TORCH:
            train_dataset = TensorDataset(torch.FloatTensor(X_train), torch.FloatTensor(y_train))
            val_dataset = TensorDataset(torch.FloatTensor(X_val), torch.FloatTensor(y_val))
            test_dataset = TensorDataset(torch.FloatTensor(X_test), torch.FloatTensor(y_test))
            return train_dataset, val_dataset, test_dataset

        return X_train, X_val, X_test, y_train, y_val, y_test

    def create_windowed_dataset(
        self,
        return_tensors: bool = False,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """创建滑动窗口数据集（适用于时序预测）。

        .. note::
            返回的窗口数据来自 ``self.feature_data``（未标准化的原始特征）。
            如需标准化窗口数据，请先调用 ``split_data`` 拟合 scaler，
            再使用 ``self.scaler.transform`` 对窗口数据变换。

        Args:
            return_tensors: 是否返回PyTorch张量

        Returns:
            (X_windows, y_windows) 窗口化后的特征和标签
        """
        if self.feature_data is None:
            raise ValueError(
                "Bosch 数据集窗口化失败：尚未完成特征工程。请先调用 engineer_features() 方法提取特征，再进行窗口化操作。"
            )

        window_size = self.config.window_size
        window_step = self.config.window_step

        X_windows = []
        y_windows = []

        for start in range(0, len(self.feature_data) - window_size, window_step):
            end = start + window_size
            X_windows.append(self.feature_data[start:end])
            y_windows.append(self.target_data[end - 1])

        X_windows = np.array(X_windows)
        y_windows = np.array(y_windows)

        self._stats["window_count"] = len(X_windows)
        self._stats["window_size"] = window_size

        logger.info("Windowed dataset created: %s", X_windows.shape)

        if return_tensors and HAS_TORCH:
            return torch.FloatTensor(X_windows), torch.FloatTensor(y_windows)

        return X_windows, y_windows

    def create_dataloaders(
        self,
        batch_size: Optional[int] = None,
        num_workers: Optional[int] = None,
    ) -> Tuple["DataLoader", "DataLoader", "DataLoader"]:
        """
        创建DataLoader

        Args:
            batch_size: 批次大小
            num_workers: 数据加载线程数

        Returns:
            (train_loader, val_loader, test_loader)
        """
        if not HAS_TORCH:
            raise ImportError(
                "Bosch 数据集 DataLoader 创建失败：需要安装 PyTorch 库。DataLoader 用于创建训练和验证数据加载器。请安装 PyTorch（pip install torch）后重试。"
            )

        batch_size = batch_size or self.config.batch_size
        num_workers = num_workers or self.config.num_workers

        train_dataset, val_dataset, test_dataset = self.split_data(return_tensors=True)

        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=num_workers)
        val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False, num_workers=num_workers)
        test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False, num_workers=num_workers)

        return train_loader, val_loader, test_loader

    def _handle_missing_values(self, df: pd.DataFrame) -> pd.DataFrame:
        """处理缺失值（无数据泄漏版）。

        .. note::
            此处仅使用非统计方法（fillna(0)）进行初步填充，避免在全量数据上
            拟合 imputer 造成测试集统计量泄漏。真正的 imputer 拟合在
            ``split_data`` 完成后仅用 ``X_train`` 进行，详见 ``split_data``。
        """
        numeric_cols = df.select_dtypes(include=[np.number]).columns

        # 注意：此处不拟合 imputer，避免在数据划分前使用全量数据统计量造成泄漏。
        # imputer 将在 split_data 后仅用训练集拟合。此处用 0 填充仅作为占位，
        # 防止 lag/rolling 特征工程时 NaN 过度传播导致 dropna 丢失过多样本。
        df[numeric_cols] = df[numeric_cols].fillna(0)
        logger.warning(
            "_handle_missing_values: 已跳过 imputer 拟合（防止数据泄漏），"
            "仅用 0 填充缺失值。imputer 将在 split_data 后仅用训练集拟合。"
        )

        return df

    def _handle_outliers(self, df: pd.DataFrame) -> pd.DataFrame:
        """处理异常值"""
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        method = self.config.outlier_method
        threshold = self.config.outlier_threshold

        outlier_count = 0

        for col in numeric_cols:
            if method == "iqr":
                Q1 = df[col].quantile(0.25)
                Q3 = df[col].quantile(0.75)
                IQR = Q3 - Q1
                lower = Q1 - threshold * IQR
                upper = Q3 + threshold * IQR
                outliers = ((df[col] < lower) | (df[col] > upper)).sum()
                df[col] = df[col].clip(lower, upper)
                outlier_count += outliers

            elif method == "zscore":
                mean = df[col].mean()
                std = df[col].std()
                lower = mean - threshold * std
                upper = mean + threshold * std
                outliers = ((df[col] < lower) | (df[col] > upper)).sum()
                df[col] = df[col].clip(lower, upper)
                outlier_count += outliers

        self._stats["outliers_handled"] = outlier_count
        logger.info("Handled %s outliers", outlier_count)

        return df

    def _normalize_features(self, X: np.ndarray) -> np.ndarray:
        """特征标准化（已废弃 - 为防止数据泄漏，标准化已延迟到 split_data 后）。

        .. warning::
            此方法现在为 no-op，直接返回未变换的 X。scaler 的拟合已移至
            ``split_data`` 方法中，仅使用训练集拟合，避免测试集统计量泄漏。
            如需在推理时对数据标准化，请先调用 ``split_data`` 或
            ``load_preprocessors`` 加载已拟合的 scaler，再使用
            ``self.scaler.transform(X)``。
        """
        logger.warning(
            "_normalize_features 已废弃（no-op）：为防止数据泄漏，"
            "标准化已在 split_data 中仅用训练集拟合。此调用不执行任何变换。"
        )
        return X

    def inverse_transform(self, y_pred: np.ndarray) -> np.ndarray:
        """
        逆变换预测结果

        Args:
            y_pred: 标准化后的预测值

        Returns:
            原始尺度的预测值
        """
        if self.scaler is None:
            raise ValueError(
                "Bosch 数据集逆变换失败：尚未完成数据标准化。scaler 对象在进行逆变换前必须通过 split_data() 或 load_preprocessors() 加载。请先调用 split_data() 完成数据划分与标准化拟合，或调用 load_preprocessors() 加载已保存的预处理器。"
            )
        return self.scaler.inverse_transform(y_pred)

    def get_stats(self) -> Dict[str, Any]:
        """获取处理统计信息"""
        return self._stats.copy()

    def save_processed_data(self, output_path: str) -> None:
        """
        保存处理后的数据

        Args:
            output_path: 输出文件路径
        """
        if self.processed_data is None:
            raise ValueError(
                "Bosch 数据集保存失败：没有可保存的处理后数据。请先调用 clean_data() 或 engineer_features() 方法处理数据，再进行保存操作。"
            )

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.suffix == ".csv":
            self.processed_data.to_csv(output_path, index=False)
        elif path.suffix == ".parquet":
            self.processed_data.to_parquet(output_path, index=False)
        else:
            self.processed_data.to_csv(output_path, index=False)

        logger.info("Processed data saved to %s", output_path)

    def export_feature_names(self, output_path: str) -> None:
        """导出特征名称列表"""
        with open(output_path, "w", encoding="utf-8") as f:
            for name in self.feature_names:
                f.write(f"{name}\n")
        logger.info("Feature names exported to %s", output_path)

    def save_preprocessors(self, path: str) -> None:
        """持久化预处理器（imputer + scaler）到磁盘，供推理时加载。

        在 ``split_data`` 完成后调用，将仅用训练集拟合的 imputer 和 scaler
        序列化为 joblib 文件。推理时可通过 ``load_preprocessors`` 加载。

        Args:
            path: joblib 文件路径（如 ``models/lnn/preprocessors.pkl``）
        """
        import joblib

        if self.scaler is None or self.imputer is None:
            raise ValueError(
                "Bosch 数据集预处理器保存失败：scaler 或 imputer 尚未拟合。"
                "请先调用 split_data() 完成数据划分与预处理器拟合，再进行保存。"
            )

        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "scaler": self.scaler,
            "imputer": self.imputer,
            "feature_names": self.feature_names,
            "normalization_method": self.config.normalization_method,
            "imputation_strategy": self.config.imputation_strategy,
        }
        joblib.dump(payload, path)
        logger.info("Preprocessors saved to %s", path)

    def load_preprocessors(self, path: str) -> None:
        """从磁盘加载预处理器（imputer + scaler），供推理时使用。

        Args:
            path: joblib 文件路径
        """
        import joblib

        path_obj = Path(path)
        if not path_obj.exists():
            raise FileNotFoundError(
                f"Bosch 数据集预处理器加载失败：找不到文件 '{path}'。"
                "请确认路径正确，或先调用 save_preprocessors() 保存预处理器。"
            )

        payload = joblib.load(path)
        self.scaler = payload["scaler"]
        self.imputer = payload["imputer"]
        self.feature_names = payload.get("feature_names", [])
        logger.info("Preprocessors loaded from %s", path)


class BoschDataGenerator:
    """
    Bosch模拟数据生成器

    用于测试和原型开发，生成符合Bosch切削/磨损数据分布的模拟数据
    """

    @staticmethod
    def generate_cutting_force_data(
        n_samples: int = 10000,
        n_features: int = 20,
        noise_level: float = 0.1,
        seed: int = 42,
    ) -> pd.DataFrame:
        """
        生成模拟切削力数据集

        Args:
            n_samples: 样本数
            n_features: 特征数
            noise_level: 噪声水平
            seed: 随机种子

        Returns:
            模拟数据DataFrame
        """
        rng = np.random.RandomState(seed)

        data = {
            "timestamp": pd.date_range("2024-01-01", periods=n_samples, freq="1s"),
        }

        feature_names = [
            "spindle_speed",
            "feed_rate",
            "depth_of_cut",
            "tool_diameter",
            "vibration_x",
            "vibration_y",
            "vibration_z",
            "temperature",
            "coolant_pressure",
            "coolant_flow_rate",
            "power_consumption",
            "acoustic_emission",
            "motor_current",
            "chatter_index",
            "surface_roughness",
            "tool_wear",
            "material_hardness",
            "cutting_angle",
            "rake_angle",
            "clearance_angle",
        ][:n_features]

        for feat in feature_names:
            data[feat] = rng.randn(n_samples) * FEATURE_NOISE_SCALE + rng.rand() * FEATURE_OFFSET_RANGE

        data["cutting_force"] = (
            CUTTING_FORCE_WEIGHTS["spindle_speed"] * data["spindle_speed"]
            + CUTTING_FORCE_WEIGHTS["feed_rate"] * data["feed_rate"]
            + CUTTING_FORCE_WEIGHTS["depth_of_cut"] * data["depth_of_cut"]
            + CUTTING_FORCE_WEIGHTS["vibration_x"] * data["vibration_x"]
            + CUTTING_FORCE_WEIGHTS["temperature"] * data["temperature"]
            + rng.randn(n_samples) * noise_level
        )

        data["tool_wear"] = (
            TOOL_WEAR_WEIGHTS["cutting_force"] * data["cutting_force"]
            + TOOL_WEAR_WEIGHTS["spindle_speed"] * data["spindle_speed"]
            + TOOL_WEAR_WEIGHTS["temperature"] * data["temperature"]
            + TOOL_WEAR_WEIGHTS["vibration_x"] * data["vibration_x"]
            + rng.randn(n_samples) * noise_level * 0.5
        )

        df = pd.DataFrame(data)

        missing_mask = rng.rand(n_samples, n_features) < DEFAULT_MISSING_RATE
        for i, col in enumerate(feature_names):
            df.loc[missing_mask[:, i], col] = np.nan

        return df
