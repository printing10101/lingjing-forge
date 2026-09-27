"""
数据预处理器模块

支持五种数据类型的标准化预处理：
- 图像: 尺寸标准化 + 像素归一化
- 时序: 滑动窗口 + 噪声过滤
- 文本: 清洗 + 标准化
- 刀具状态: 数值归一化 + 状态编码 + 异常检测
- G代码: 语法解析 + 语义分割
"""

from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from app.data.pipeline.datatypes import (
    DataSourceType,
    ProcessedData,
    RawInput,
    ImageInput,
    TimeSeriesInput,
    TextInput,
    ToolStateInput,
    GCodeInput,
    DataQualityMetrics,
)
from app.data.pipeline.config import (
    ImageProcessorConfig,
    TimeSeriesProcessorConfig,
    TextProcessorConfig,
    ToolStateProcessorConfig,
    GCodeProcessorConfig,
)

logger = logging.getLogger(__name__)


class BasePreprocessor(ABC):
    """预处理器基类"""

    def __init__(self, config: Any):
        self.config = config
        self.is_fitted = False

    @abstractmethod
    def preprocess(self, raw_input: RawInput) -> ProcessedData:
        pass

    def validate(self, processed: ProcessedData) -> DataQualityMetrics:
        return DataQualityMetrics()


class ImagePreprocessor(BasePreprocessor):
    """图像预处理器 - 尺寸标准化 + 像素归一化"""

    def __init__(self, config: ImageProcessorConfig):
        super().__init__(config)
        self._target_size = (config.image_size, config.image_size)

    def preprocess(self, raw_input: ImageInput) -> ProcessedData:  # type: ignore[override]
        t0 = time.perf_counter()

        img = raw_input.data

        if img.ndim == 2:
            img = np.stack([img] * 3, axis=-1)
        elif img.ndim == 3 and img.shape[0] in (1, 3, 4):
            img = np.transpose(img, (1, 2, 0))

        if img.shape[-1] == 1:
            img = np.repeat(img, 3, axis=-1)
        elif img.shape[-1] == 4:
            img = img[:, :, :3]

        if raw_input.bit_depth not in self.config.supported_bit_depths:
            raise ValueError(f"不支持的图像位深度: {raw_input.bit_depth}-bit。支持: {self.config.supported_bit_depths}")

        # 校验图像尺寸：零维图像无法进行 zoom 缩放，会触发 ZeroDivisionError
        if img.shape[0] == 0 or img.shape[1] == 0:
            raise ValueError(f"图像尺寸无效: {img.shape[:2]}，高度和宽度必须大于 0")

        if raw_input.bit_depth == 16:
            img = (img / 65535.0).astype(np.float32)
        else:
            img = (img / 255.0).astype(np.float32)

        from scipy.ndimage import zoom

        h, w = img.shape[:2]
        zoom_h = self.config.image_size / h
        zoom_w = self.config.image_size / w
        if img.ndim == 3:
            img = zoom(img, (zoom_h, zoom_w, 1), order=1)
        else:
            img = zoom(img, (zoom_h, zoom_w), order=1)

        img = np.clip(img, 0.0, 1.0)

        delay = (time.perf_counter() - t0) * 1000

        return ProcessedData(
            source_type=DataSourceType.IMAGE,
            original_data=raw_input.data,
            processed_data=img,
            feature_dim=img.size,
            processing_time_ms=delay,
            metadata={
                "original_shape": raw_input.data.shape,
                "processed_shape": img.shape,
                "bit_depth": raw_input.bit_depth,
                "resize_method": "bilinear",
            },
        )

    def validate(self, processed: ProcessedData) -> DataQualityMetrics:
        img = processed.processed_data
        metrics = DataQualityMetrics(
            feature_dim_expected=self.config.image_size * self.config.image_size * 3,
            feature_dim_actual=img.size,
            value_range=(float(img.min()), float(img.max())),
        )
        if img.min() < 0 or img.max() > 1.0:
            metrics.validation_errors.append("像素值超出[0,1]范围")
            metrics.consistency = 0.8
        if np.isnan(img).any():
            metrics.validation_errors.append("存在NaN值")
            metrics.completeness = 0.0
        return metrics


class TimeSeriesPreprocessor(BasePreprocessor):
    """时序数据预处理器 - 滑动窗口 + 噪声过滤"""

    def __init__(self, config: TimeSeriesProcessorConfig):
        super().__init__(config)
        self._filter_coeff = None

    def _design_butterworth_filter(self):
        if self.config.denoising_algorithm != "butterworth":
            return
        try:
            from scipy.signal import butter

            params = self.config.denoise_params
            nyq = 0.5 * self.config.sample_rate
            cutoff = params.get("cutoff_ratio", 0.1) * nyq
            order = min(params.get("order", 4), 8)
            self._filter_coeff = butter(order, cutoff / nyq, btype=params.get("btype", "low"))
        except ImportError:
            self._filter_coeff = None

    def _apply_denoising(self, data: np.ndarray) -> np.ndarray:
        algorithm = self.config.denoising_algorithm
        if algorithm == "butterworth":
            if self._filter_coeff is None:
                self._design_butterworth_filter()
            if self._filter_coeff is not None:
                from scipy.signal import filtfilt

                b, a = self._filter_coeff
                return filtfilt(b, a, data, axis=0)
        elif algorithm == "moving_average":
            window = 5
            kernel = np.ones(window) / window
            result = np.apply_along_axis(lambda x: np.convolve(x, kernel, mode="same"), 0, data)
            return result
        elif algorithm == "median":
            from scipy.signal import medfilt

            return np.apply_along_axis(lambda x: medfilt(x, kernel_size=5), 0, data)
        return data

    def preprocess(self, raw_input: TimeSeriesInput) -> ProcessedData:  # type: ignore[override]
        t0 = time.perf_counter()

        data = raw_input.data.astype(np.float32)
        if data.ndim == 1:
            data = data.reshape(-1, 1)

        window_size = self.config.window_size
        overlap = self.config.overlap_ratio
        step = int(window_size * (1 - overlap))

        if data.shape[0] < window_size:
            pad_size = window_size - data.shape[0]
            data = np.pad(data, ((0, pad_size), (0, 0)), mode="edge")

        n_windows = max(1, (data.shape[0] - window_size) // step + 1)
        windows = np.zeros((n_windows, window_size, data.shape[1]), dtype=np.float32)
        for i in range(n_windows):
            start = i * step
            windows[i] = data[start : start + window_size]

        windows = self._apply_denoising(windows.reshape(-1, data.shape[1])).reshape(
            n_windows, window_size, data.shape[1]
        )

        delay = (time.perf_counter() - t0) * 1000

        return ProcessedData(
            source_type=DataSourceType.TIME_SERIES,
            original_data=raw_input.data,
            processed_data=windows,
            feature_dim=window_size * data.shape[1],
            processing_time_ms=delay,
            metadata={
                "n_windows": n_windows,
                "window_size": window_size,
                "step": step,
                "overlap_ratio": overlap,
                "original_channels": data.shape[1],
                "denoising_algorithm": self.config.denoising_algorithm,
            },
        )

    def validate(self, processed: ProcessedData) -> DataQualityMetrics:
        data = processed.processed_data
        metrics = DataQualityMetrics(
            feature_dim_expected=self.config.window_size * processed.processed_data.shape[-1],
            feature_dim_actual=data[0].size,
            value_range=(float(np.min(data)), float(np.max(data))),
        )
        if np.any(np.isnan(data)):
            metrics.completeness = 0.0
            metrics.validation_errors.append("时序数据包含NaN值")
        if np.any(np.isinf(data)):
            metrics.completeness = 0.0
            metrics.validation_errors.append("时序数据包含Inf值")
        return metrics


class TextPreprocessor(BasePreprocessor):
    """文本预处理器 - 清洗与标准化"""

    def __init__(self, config: TextProcessorConfig):
        super().__init__(config)
        self._cleaning_patterns = [
            (re.compile(r"\s+"), " "),
            (re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"), ""),
        ]

    def _clean_text(self, text: str) -> str:
        if self.config.normalize_whitespace:
            text = re.sub(r"\s+", " ", text)
        if self.config.clean_special_chars:
            text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", "", text)
        text = text.strip()
        if len(text) > self.config.max_text_length:
            text = text[: self.config.max_text_length]
        return text

    def preprocess(self, raw_input: TextInput) -> ProcessedData:  # type: ignore[override]
        t0 = time.perf_counter()

        # 校验文本数据：None 不应被 str() 转换为 "None"，否则会错误产生特征
        if raw_input.data is None:
            raise ValueError("文本数据不能为 None")

        if isinstance(raw_input.data, dict):
            text = json.dumps(raw_input.data, ensure_ascii=False, indent=2)
        else:
            text = str(raw_input.data)

        cleaned = self._clean_text(text)

        delay = (time.perf_counter() - t0) * 1000

        return ProcessedData(
            source_type=DataSourceType.TEXT,
            original_data=raw_input.data,
            processed_data=np.array(
                [ord(c) / 65535.0 for c in cleaned[: self.config.max_text_length]],
                dtype=np.float32,
            ),
            feature_dim=self.config.bge_embedding_dim,
            processing_time_ms=delay,
            metadata={
                "original_length": len(text),
                "cleaned_length": len(cleaned),
                "text_format": raw_input.text_format,
            },
        )

    def validate(self, processed: ProcessedData) -> DataQualityMetrics:
        metrics = DataQualityMetrics(
            feature_dim_expected=self.config.bge_embedding_dim,
            feature_dim_actual=processed.feature_dim,
        )
        if processed.processed_data.size == 0:
            metrics.completeness = 0.0
            metrics.validation_errors.append("文本预处理后为空")
        return metrics


class ToolStatePreprocessor(BasePreprocessor):
    """刀具状态预处理器 - 数值归一化 + 状态编码 + 异常检测"""

    def __init__(self, config: ToolStateProcessorConfig):
        super().__init__(config)
        self._min_max_params: dict[str, tuple[float, float]] = {}

    def _fit_min_max(self, data: dict[str, Any]) -> None:
        for field in self.config.state_fields:
            if field in data and isinstance(data[field], (int, float)):
                self._min_max_params[field] = (0.0, max(abs(float(data[field])) * 2, 1.0))

    def _normalize(self, data: dict[str, Any]) -> np.ndarray:
        features = []
        for field in self.config.state_fields:
            val = data.get(field, 0.0)
            if isinstance(val, (int, float, np.integer, np.floating)):
                val = float(val)
            elif isinstance(val, str):
                val = float(val) if val.replace(".", "", 1).replace("-", "", 1).isdigit() else 0.0
            else:
                val = 0.0

            min_v, max_v = self._min_max_params.get(field, (0.0, 1.0))
            if max_v - min_v > 0:
                val = (val - min_v) / (max_v - min_v)
            features.append(np.clip(val, 0.0, 1.0))
        return np.array(features, dtype=np.float32)

    def _detect_anomaly(self, features: np.ndarray) -> tuple[np.ndarray, int]:
        method = self.config.anomaly_detection_method
        threshold = self.config.anomaly_threshold
        anomaly_count = 0

        if method == "iqr":
            q1 = np.percentile(features, 25)
            q3 = np.percentile(features, 75)
            iqr = q3 - q1
            lower = q1 - threshold * iqr
            upper = q3 + threshold * iqr
            mask = (features < lower) | (features > upper)
            anomaly_count = int(np.sum(mask))
            features = np.clip(features, lower, upper)
        elif method == "z_score":
            mean = np.mean(features)
            std = np.std(features) + 1e-10
            z_scores = np.abs((features - mean) / std)
            mask = z_scores > threshold
            anomaly_count = int(np.sum(mask))
            features[mask] = np.clip(features[mask], mean - threshold * std, mean + threshold * std)

        return features, anomaly_count

    def preprocess(self, raw_input: ToolStateInput) -> ProcessedData:  # type: ignore[override]
        t0 = time.perf_counter()

        if not self._min_max_params:
            self._fit_min_max(raw_input.data)

        features = self._normalize(raw_input.data)
        features, anomaly_count = self._detect_anomaly(features)

        if self.config.encoding_method == "one_hot":
            binary_features = (features > 0.5).astype(np.float32)
            features = np.concatenate([features, binary_features])

        delay = (time.perf_counter() - t0) * 1000

        return ProcessedData(
            source_type=DataSourceType.TOOL_STATE,
            original_data=raw_input.data,
            processed_data=features,
            feature_dim=self.config.tool_state_dim,
            processing_time_ms=delay,
            anomaly_count=anomaly_count,
            metadata={
                "state_fields": self.config.state_fields,
                "encoding": self.config.encoding_method,
                "anomaly_count": anomaly_count,
            },
        )

    def validate(self, processed: ProcessedData) -> DataQualityMetrics:
        metrics = DataQualityMetrics(
            feature_dim_expected=self.config.tool_state_dim,
            feature_dim_actual=processed.processed_data.size,
            value_range=(float(np.min(processed.processed_data)), float(np.max(processed.processed_data))),
            outlier_ratio=processed.anomaly_count / max(processed.processed_data.size, 1),
            missing_ratio=float(np.sum(np.isnan(processed.processed_data))) / max(processed.processed_data.size, 1),
        )
        if np.any(np.isnan(processed.processed_data)):
            metrics.validation_errors.append("刀具状态数据包含NaN")
        if processed.anomaly_count > 0:
            metrics.validation_errors.append(f"检测到{processed.anomaly_count}个异常值")
        return metrics


class GCodePreprocessor(BasePreprocessor):
    """G代码预处理器 - 语法解析 + 语义分割"""

    def __init__(self, config: GCodeProcessorConfig):
        super().__init__(config)
        self._instruction_pattern = re.compile(
            r"(G\d+|M\d+|T\d+|S\d+|F[\d.]+|X[\d.\-]+|Y[\d.\-]+|Z[\d.\-]+|I[\d.\-]+|J[\d.\-]+|K[\d.\-]+|"
            r"N\d+|O\d+|L\d+|P\d+|D\d+|H\d+|A[\d.\-]+|B[\d.\-]+|C[\d.\-]+|R[\d.\-]+)",
            re.IGNORECASE,
        )
        self._operation_markers = {
            "G00": "rapid",
            "G01": "linear",
            "G02": "arc_cw",
            "G03": "arc_ccw",
            "G17": "plane_xy",
            "G18": "plane_xz",
            "G19": "plane_yz",
            "G20": "units_inch",
            "G21": "units_mm",
            "G40": "comp_off",
            "G41": "comp_left",
            "G42": "comp_right",
            "G43": "tool_length_comp",
            "G54": "work_offset_1",
            "G80": "cycle_cancel",
            "G81": "drill",
            "G83": "peck_drill",
            "G90": "absolute",
            "G91": "incremental",
            "M03": "spindle_cw",
            "M04": "spindle_ccw",
            "M05": "spindle_stop",
            "M06": "tool_change",
            "M08": "coolant_on",
            "M09": "coolant_off",
            "M30": "program_end",
        }

    def _parse_instructions(self, gcode: str) -> list[dict[str, Any]]:
        instructions = []
        for line_num, line in enumerate(gcode.strip().split("\n"), 1):
            line = line.strip()
            if not line or line.startswith("(") or line.startswith(";"):
                continue
            tokens = self._instruction_pattern.findall(line)
            if tokens:
                instructions.append(
                    {
                        "line": line_num,
                        "tokens": tokens,
                        "text": line,
                    }
                )
        return instructions

    def _segment_by_operation(self, instructions: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        segments = []
        current_segment: list[dict[str, Any]] = []
        current_op = None

        for inst in instructions:
            op_found = None
            for token in inst["tokens"]:
                if token.upper() in self._operation_markers:
                    op_found = self._operation_markers[token.upper()]
                    break

            if op_found is not None and op_found != current_op and current_segment:
                segments.append(current_segment)
                current_segment = []
            current_op = op_found
            current_segment.append(inst)

        if current_segment:
            segments.append(current_segment)
        return segments or [instructions]

    def _encode_instructions(self, instructions: list[dict[str, Any]]) -> np.ndarray:
        vocab = {
            "G": 0,
            "M": 1,
            "T": 2,
            "S": 3,
            "F": 4,
            "X": 5,
            "Y": 6,
            "Z": 7,
            "I": 8,
            "J": 9,
            "K": 10,
            "A": 11,
            "B": 12,
            "C": 13,
            "R": 14,
            "N": 15,
            "O": 16,
            "L": 17,
            "P": 18,
            "D": 19,
            "H": 20,
        }
        encoded = np.zeros((len(instructions), len(vocab)), dtype=np.float32)
        for i, inst in enumerate(instructions):
            for token in inst["tokens"]:
                prefix = token[0].upper()
                if prefix in vocab:
                    try:
                        val = float(token[1:])
                    except ValueError as e:
                        # P2-批次2 修复：改用 %s 懒求值。此处在嵌套 for 循环内，
                        # G 代码 token 编码热路径，debug 关闭时避免字符串插值开销。
                        logger.debug("G代码 token %r 数值解析失败，使用默认值 0.0: %s", token, e)
                        val = 0.0
                    encoded[i, vocab[prefix]] = val
        return encoded

    def preprocess(self, raw_input: GCodeInput) -> ProcessedData:  # type: ignore[override]
        t0 = time.perf_counter()

        if raw_input.controller_type not in self.config.supported_controllers:
            raise ValueError(
                f"不支持的控制器类型: {raw_input.controller_type}。支持: {self.config.supported_controllers}"
            )

        instructions = self._parse_instructions(raw_input.data)

        if self.config.segment_by_operation:
            segments = self._segment_by_operation(instructions)
        else:
            segments = [instructions]

        encoded_segments = []
        for seg in segments:
            if len(seg) > self.config.max_instructions_per_segment:
                seg = seg[: self.config.max_instructions_per_segment]
            encoded_segments.append(self._encode_instructions(seg))

        delay = (time.perf_counter() - t0) * 1000

        if encoded_segments:
            combined = np.vstack(encoded_segments)
        else:
            combined = np.zeros((1, 21), dtype=np.float32)

        return ProcessedData(
            source_type=DataSourceType.GCODE,
            original_data=raw_input.data,
            processed_data=combined,
            feature_dim=self.config.gcode_embedding_dim,
            processing_time_ms=delay,
            metadata={
                "n_instructions": len(instructions),
                "n_segments": len(encoded_segments),
                "controller_type": raw_input.controller_type,
                "segment_by_operation": self.config.segment_by_operation,
            },
        )

    def validate(self, processed: ProcessedData) -> DataQualityMetrics:
        metrics = DataQualityMetrics(
            feature_dim_expected=self.config.gcode_embedding_dim,
            feature_dim_actual=processed.feature_dim,
        )
        if processed.processed_data.size == 0:
            metrics.completeness = 0.0
            metrics.validation_errors.append("G代码解析后为空")
        if processed.metadata["n_instructions"] == 0:
            metrics.validation_errors.append("未提取到有效指令")
        return metrics
