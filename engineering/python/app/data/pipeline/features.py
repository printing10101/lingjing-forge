"""
特征提取模块

对不同类型的数据执行特征提取：
- 图像: 预训练CNN特征提取
- 时序: 时域/频域特征工程
- 工艺知识: BGE模型嵌入
- G代码: 指令序列嵌入
"""

from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np

from app.data.pipeline.datatypes import ProcessedData
from app.data.pipeline.config import (
    ImageProcessorConfig,
    TimeSeriesProcessorConfig,
    TextProcessorConfig,
    GCodeProcessorConfig,
)

logger = logging.getLogger(__name__)


class CNNFeatureExtractor:
    """基于预训练CNN模型的图像特征提取器"""

    def __init__(
        self,
        config: ImageProcessorConfig,
        device: str = "cpu",
        use_torch: bool = True,
    ):
        self.config = config
        self.device = device
        self.use_torch = use_torch
        self._model: Any = None
        self._is_loaded = False

    def load_model(self):
        """延迟加载模型"""
        if self._is_loaded:
            return
        if self.use_torch:
            try:
                import torch
                import torchvision.models as models
                from torchvision import transforms

                if self.config.pretrained_model == "resnet50":
                    self._model = models.resnet50(weights="ResNet50_Weights.DEFAULT")
                elif self.config.pretrained_model == "resnet18":
                    self._model = models.resnet18(weights="ResNet18_Weights.DEFAULT")
                else:
                    self._model = models.resnet50(weights="ResNet50_Weights.DEFAULT")

                if self.config.cnn_feature_dim == 512:
                    self._model.fc = torch.nn.Identity()
                self._model.to(self.device)
                self._model.eval()

                self._transform = transforms.Normalize(
                    mean=self.config.mean,
                    std=self.config.std,
                )

                logger.info(
                    "CNN模型加载完成: %s, 输出维度: %d",
                    self.config.pretrained_model,
                    self.config.cnn_feature_dim,
                )
            except ImportError:
                logger.warning("PyTorch不可用，使用随机权重降级")
                self._model = None
        self._is_loaded = True

    def extract(self, processed_image: ProcessedData) -> np.ndarray:
        """提取CNN特征"""
        self.load_model()
        img = processed_image.processed_data

        if self._model is not None and self.use_torch:
            import torch

            if img.ndim == 3:
                img = np.transpose(img, (2, 0, 1))
                img_tensor = torch.tensor(img, dtype=torch.float32).unsqueeze(0)
                img_tensor = self._transform(img_tensor)
                img_tensor = img_tensor.to(self.device)
                with torch.no_grad():
                    features = self._model(img_tensor)
                return features.cpu().numpy()[0]
            elif img.ndim == 4:
                img = np.transpose(img, (0, 3, 1, 2))
                img_tensor = torch.tensor(img, dtype=torch.float32)
                img_tensor = self._transform(img_tensor)
                img_tensor = img_tensor.to(self.device)
                with torch.no_grad():
                    features = self._model(img_tensor)
                return features.cpu().numpy()

        feat_dim = self.config.cnn_feature_dim
        if img.ndim >= 2:
            avg = np.mean(img, axis=(0, 1))
            if len(avg) == 3:
                padded = np.pad(avg, (0, feat_dim - 3), mode="constant")
                return padded
        return np.random.randn(feat_dim) / (feat_dim**0.5)


class TimeSeriesFeatureEngineer:
    """时序特征工程 - 提取时域/频域特征"""

    def __init__(self, config: TimeSeriesProcessorConfig):
        self.config = config
        self.window_size = config.window_size
        self.n_features = config.ts_feature_count

    def extract_time_domain(self, window: np.ndarray) -> np.ndarray:
        """提取时域特征"""
        if window.ndim > 1:
            window = window.flatten()
        features = []
        mean = np.mean(window)
        std = np.std(window)
        # M4 bug 修复：numpy 没有 skew/kurtosis（在 scipy.stats），
        # hasattr(np, "skew") 永远为 False，导致这两个特征永远为 0，
        # 模型训练时实际上静默丢失了这两个特征。
        # 改为：优先用 scipy.stats，scipy 不可用时用 numpy 手动实现。
        try:
            from scipy.stats import skew as _scipy_skew, kurtosis as _scipy_kurtosis

            _skew_val = float(_scipy_skew(window))
            _kurt_val = float(_scipy_kurtosis(window))
        except ImportError:
            # numpy 手动实现（无 scipy 依赖时的降级方案）
            if std > 0:
                _norm = (window - mean) / std
                _skew_val = float(np.mean(_norm**3))
                _kurt_val = float(np.mean(_norm**4) - 3.0)
            else:
                _skew_val = 0.0
                _kurt_val = 0.0
        features.extend(
            [
                mean,
                std,
                np.min(window),
                np.max(window),
                np.median(window),
                np.var(window),
                np.percentile(window, 25),
                np.percentile(window, 75),
                _skew_val,
                _kurt_val,
                np.max(window) - np.min(window),
                np.sum(np.abs(window - mean)),
                np.sum((window - mean) ** 2),
                np.sqrt(np.mean(window**2)),
                np.max(np.abs(window)),
                np.mean(np.abs(np.diff(window))),
                np.std(np.abs(np.diff(window))),
            ]
        )
        return np.array(features[: self.n_features], dtype=np.float32)

    def extract_frequency_domain(self, window: np.ndarray) -> np.ndarray:
        """提取频域特征"""
        if window.ndim > 1:
            window = window.flatten()
        features = []
        try:
            from scipy.fftpack import fft

            yf = fft(window)
            # M5 bug 修复：删除 np.linspace(...) 死代码（结果未赋值给任何变量）。
            amplitudes = 2.0 / len(window) * np.abs(yf[0 : len(window) // 2])

            if len(amplitudes) > 0:
                features.extend(
                    [
                        np.mean(amplitudes),
                        np.std(amplitudes),
                        np.max(amplitudes),
                        np.argmax(amplitudes),
                        np.sum(amplitudes),
                        np.percentile(amplitudes, 50),
                        np.percentile(amplitudes, 90),
                        np.var(amplitudes),
                    ]
                )
        except ImportError as imp_err:
            # numpy 高级统计不可用时仅使用基础特征，记录以便排查
            logger.debug(
                "Advanced numpy features unavailable, using basic stats only: %s",
                imp_err,
                exc_info=True,
            )
        return np.array(features, dtype=np.float32)

    def extract(self, processed_ts: ProcessedData) -> np.ndarray:
        """提取特征从所有窗口拼接"""
        windows = processed_ts.processed_data
        if windows.ndim == 3:
            all_features = []
            for i in range(windows.shape[0]):
                window = windows[i]
                time_feat = self.extract_time_domain(window)
                freq_feat = self.extract_frequency_domain(window)
                combined = np.concatenate([time_feat, freq_feat])
                if len(combined) > self.config.ts_feature_count:
                    combined = combined[: self.config.ts_feature_count]
                elif len(combined) < self.config.ts_feature_count:
                    pad = self.config.ts_feature_count - len(combined)
                    combined = np.pad(combined, (0, pad), mode="constant")
                all_features.append(combined)
            return np.array(all_features, dtype=np.float32)
        window = windows
        time_feat = self.extract_time_domain(window)
        freq_feat = self.extract_frequency_domain(window)
        combined = np.concatenate([time_feat, freq_feat])
        if len(combined) > self.config.ts_feature_count:
            combined = combined[: self.config.ts_feature_count]
        elif len(combined) < self.config.ts_feature_count:
            pad = self.config.ts_feature_count - len(combined)
            combined = np.pad(combined, (0, pad), mode="constant")
        return combined.astype(np.float32)


class BGEEmbedder:
    """基于BGE模型的文本嵌入"""

    def __init__(self, config: TextProcessorConfig):
        self.config = config
        self._model = None
        self._embedding_service = None
        self._is_loaded = False

    def load_model(self):
        """加载模型，使用现有 embedding service 如果可用"""
        if self._is_loaded:
            return
        try:
            from app.dependencies import get_embedding_service

            self._embedding_service = get_embedding_service()
            logger.info("使用全局 embedding service")
        except ImportError:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.config.bge_model_name)
            logger.info("BGE模型加载完成: %s", self.config.bge_model_name)
        self._is_loaded = True

    def embed(self, text: str) -> list[float]:
        """嵌入单文本"""
        self.load_model()
        if self._embedding_service is not None:
            return self._embedding_service.embed(text)
        elif self._model is not None:
            return self._model.encode(text, normalize_embeddings=True).tolist()
        else:
            dim = self.config.bge_embedding_dim
            return [0.0] * dim

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入"""
        self.load_model()
        if self._embedding_service is not None:
            return self._embedding_service.embed_batch(texts)
        elif self._model is not None:
            return self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()
        else:
            dim = self.config.bge_embedding_dim
            return [[0.0] * dim for _ in texts]

    def extract(self, processed_text: ProcessedData) -> np.ndarray:
        """从预处理文本提取特征"""
        original = processed_text.original_data
        if isinstance(original, dict):
            text = json.dumps(original, ensure_ascii=False)
        else:
            text = str(original)
        vector = np.array(self.embed(text), dtype=np.float32)
        return vector


class GCodeEmbedder:
    """G代码指令序列嵌入（占位实现，未经训练）。

    P1 学术诚信警告：
        本嵌入矩阵为随机初始化的占位实现，未经过训练，不具备语义表示能力。
        下游 RAG 检索/相似度计算基于此伪嵌入的结果不可信，禁止用于：
        - 学术论文实验（项目目标期刊：Journal of Intelligent Manufacturing）
        - 生产环境工艺决策
        - 任何需要真实语义相似度的场景

        生产环境必须替换为预训练 G-code 嵌入模型（如 sentence-transformers
        或自定义训练的 embedding 模型），从磁盘加载预训练权重。
    """

    def __init__(self, config: GCodeProcessorConfig, output_dim: int | None = None):
        self.config = config
        self.output_dim = output_dim or config.gcode_embedding_dim
        self._vocab_size = 21
        self._projection = None

        # P1 学术诚信修复：移除 np.random.seed(42) 全局污染（影响其他模块的随机性），
        # 改用局部 Generator。注意：此嵌入矩阵仍为随机占位实现，见类 docstring 警告。
        rng = np.random.default_rng(42)
        self._embeddings = rng.standard_normal((self._vocab_size, self.output_dim)).astype(np.float32) / np.sqrt(
            self.output_dim
        )
        logger.warning(
            "GCodeEmbedder 使用随机占位嵌入矩阵（未经训练），下游 RAG 检索结果不可信，禁止用于生产或学术论文实验"
        )

    def embed(self, encoded: np.ndarray) -> np.ndarray:
        """对解析后的G代码指令进行池化嵌入"""
        if encoded.shape[0] == 0:
            return np.zeros(self.output_dim, dtype=np.float32)

        token_embeds = np.zeros((encoded.shape[0], self.output_dim), dtype=np.float32)
        for i in range(encoded.shape[0]):
            for j in range(encoded.shape[1]):
                val = encoded[i, j]
                if val != 0:
                    token_embeds[i] += val * self._embeddings[j]

        embedding = np.mean(token_embeds, axis=0)
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        return embedding

    def extract(self, processed_gcode: ProcessedData) -> np.ndarray:
        """提取G代码特征"""
        encoded = processed_gcode.processed_data
        return self.embed(encoded)
