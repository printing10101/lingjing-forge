"""I-JEPA 3D推理模块。
实现高效的推理流程，支持单样本和批量推理。目标：在NVIDIA RTX 3090 GPU上单样本推理时间<100ms。
Key components:
    - IJEPA3DInference: 推理引擎

Example:
    >>> engine = IJEPA3DInference(model, device="cuda")
    >>> bbox, kp, weights = engine.infer(front, side, top)
"""

import torch
from typing import Dict, List, Optional
import time
import logging
import numpy as np

# Q2 修复：原 `from app.ai.ijepa_3d.xxx` 为悬空 import，改为同包相对导入。
from .model import IJEPA3DModel
from .config import IJEPA3DConfig

logger = logging.getLogger(__name__)


class IJEPA3DInference:
    """I-JEPA 3D推理引擎。
    提供高效的推理接口，包括：
    - 单样本推理
    - 批量推理
    - 推理性能基准测试
    - 结果后处理
    Attributes:
        model: I-JEPA 3D模型
        config: 模型配置
        device: 计算设备
    """

    def __init__(
        self,
        model: IJEPA3DModel,
        config: Optional[IJEPA3DConfig] = None,
        device: str = "cuda",
    ):
        """初始化推理引擎。
        Args:
            model: 训练好的I-JEPA 3D模型
            config: 模型配置（可选，从model获取）
            device: 计算设备
        """
        self.model = model
        self.config = config or model.config
        self.device = torch.device(device)

        self.model = self.model.to(self.device)
        self.model.eval()

        # 预热模型（第一次推理较慢）
        self._warmup()

    def _warmup(self) -> None:
        """模型预热：执行一次虚拟推理以初始化CUDA。"""
        dummy = torch.randn(1, 3, 256, 256, device=self.device)
        with torch.no_grad():
            _ = self.model.forward_inference(dummy, dummy, dummy)
        logger.info("Model warmup complete")

    @torch.no_grad()
    def infer_single(
        self,
        front_image: torch.Tensor,
        side_image: torch.Tensor,
        top_image: torch.Tensor,
    ) -> Dict[str, np.ndarray]:
        """单样本推理。
        Args:
            front_image: 正视视图 (3, 256, 256) 或 (1, 3, 256, 256)
            side_image: 侧视视图 (3, 256, 256) 或 (1, 3, 256, 256)
            top_image: 俯视视图 (3, 256, 256) 或 (1, 3, 256, 256)

        Returns:
            推理结果字典：
            - bbox: 边界框 (6,) [cx, cy, cz, l, w, h] 单位mm
            - keypoints: 关键点 (10, 3) 单位mm
            - view_weights: 视图权重 (3,)
            - inference_time_ms: 推理耗时（ms）
        """
        # 确保批次维度
        if front_image.dim() == 3:
            front_image = front_image.unsqueeze(0)
            side_image = side_image.unsqueeze(0)
            top_image = top_image.unsqueeze(0)

        front_image = front_image.to(self.device)
        side_image = side_image.to(self.device)
        top_image = top_image.to(self.device)

        # 推理
        start_time = time.perf_counter()
        bbox, keypoints, view_weights = self.model.forward_inference(
            front_image, side_image, top_image,
        )
        elapsed = (time.perf_counter() - start_time) * 1000

        return {
            "bbox": bbox.squeeze(0).cpu().numpy(),
            "keypoints": keypoints.squeeze(0).cpu().numpy(),
            "view_weights": view_weights.cpu().numpy(),
            "inference_time_ms": elapsed,
        }

    @torch.no_grad()
    def infer_batch(
        self,
        front_images: torch.Tensor,
        side_images: torch.Tensor,
        top_images: torch.Tensor,
        batch_size: int = 16,
    ) -> Dict[str, np.ndarray]:
        """批量推理。
        Args:
            front_images: 正视视图 (N, 3, 256, 256)
            side_images: 侧视视图 (N, 3, 256, 256)
            top_images: 俯视视图 (N, 3, 256, 256)
            batch_size: 推理批次大小

        Returns:
            推理结果字典：
            - bboxes: 边界框 (N, 6)
            - keypoints_list: 关键点 (N, 10, 3)
            - view_weights_list: 视图权重 (N, 3)
            - total_time_ms: 总推理耗时
            - avg_time_ms: 平均每样本耗时
        """
        N = front_images.shape[0]
        all_bboxes = []
        all_keypoints = []
        all_weights = []

        start_time = time.perf_counter()

        for i in range(0, N, batch_size):
            end_idx = min(i + batch_size, N)
            batch_front = front_images[i:end_idx].to(self.device)
            batch_side = side_images[i:end_idx].to(self.device)
            batch_top = top_images[i:end_idx].to(self.device)

            bbox, kp, weights = self.model.forward_inference(
                batch_front, batch_side, batch_top,
            )

            all_bboxes.append(bbox.cpu().numpy())
            all_keypoints.append(kp.cpu().numpy())
            all_weights.append(weights.cpu().numpy())

        total_time = (time.perf_counter() - start_time) * 1000

        return {
            "bboxes": np.concatenate(all_bboxes, axis=0),
            "keypoints_list": np.concatenate(all_keypoints, axis=0),
            "view_weights_list": np.concatenate(all_weights, axis=0),
            "total_time_ms": total_time,
            "avg_time_ms": total_time / N,
        }

    def benchmark_inference(
        self,
        num_samples: int = 100,
        batch_sizes: Optional[List[int]] = None,
    ) -> Dict[str, float]:
        """推理性能基准测试。
        测试不同批次大小下的推理速度。
        Args:
            num_samples: 测试样本数
            batch_sizes: 要测试的批次大小列表

        Returns:
            基准测试结果字典
        """
        if batch_sizes is None:
            batch_sizes = [1, 4, 8, 16, 32]

        results = {}

        for bs in batch_sizes:
            # 生成测试数据
            front = torch.randn(bs, 3, 256, 256, device=self.device)
            side = torch.randn(bs, 3, 256, 256, device=self.device)
            top = torch.randn(bs, 3, 256, 256, device=self.device)

            # 预热
            for _ in range(5):
                _ = self.model.forward_inference(front, side, top)

            # 计时
            if self.device.type == "cuda":
                torch.cuda.synchronize()

            times = []
            for _ in range(num_samples):
                start = time.perf_counter()
                _ = self.model.forward_inference(front, side, top)
                if self.device.type == "cuda":
                    torch.cuda.synchronize()
                times.append((time.perf_counter() - start) * 1000)

            avg_time = np.mean(times)
            std_time = np.std(times)
            per_sample = avg_time / bs

            results[f"batch_{bs}"] = {
                "avg_total_ms": round(avg_time, 2),
                "std_ms": round(std_time, 2),
                "avg_per_sample_ms": round(per_sample, 2),
                "throughput_per_sec": round(1000.0 / per_sample, 2),
            }

            logger.info(
                f"Batch {bs}: {avg_time:.2f}ms total, "
                f"{per_sample:.2f}ms/sample, "
                f"{1000 / per_sample:.1f} samples/sec"
            )

        return results

    def postprocess_results(
        self,
        bbox: np.ndarray,
        keypoints: np.ndarray,
        min_size_mm: float = 0.1,
    ) -> Dict[str, np.ndarray]:
        """后处理推理结果。
        对预测结果进行合理性检查和修正。
        Args:
            bbox: 边界框预测 (..., 6)
            keypoints: 关键点预测 (..., N, 3)
            min_size_mm: 最小尺寸阈值（mm）
        Returns:
            后处理后的结果
        """
        # 确保尺寸为正
        bbox[:, 3:] = np.maximum(np.abs(bbox[:, 3:]), min_size_mm)

        # 关键点裁剪到边界框附近
        for i in range(bbox.shape[0]):
            cx, cy, cz = bbox[i, 0], bbox[i, 1], bbox[i, 2]
            length, w, h = bbox[i, 3], bbox[i, 4], bbox[i, 5]

            kp = keypoints[i]
            kp[:, 0] = np.clip(kp[:, 0], cx - length, cx + length)
            kp[:, 1] = np.clip(kp[:, 1], cy - w, cy + w)
            kp[:, 2] = np.clip(kp[:, 2], cz - h, cz + h)

        return {"bbox": bbox, "keypoints": keypoints}

    def get_feature_relations(
        self,
        keypoints: np.ndarray,
        bbox: np.ndarray,
        distance_threshold_mm: float = 5.0,
    ) -> Dict[str, list]:
        """分析关键特征点间的拓扑关系。
        Args:
            keypoints: 关键点坐标 (N_kp, 3)
            bbox: 边界框 (6,)
            distance_threshold_mm: 同平面判定距离阈值
        Returns:
            拓扑关系字典：
            - same_plane: 同平面特征点对列表
            - relative_positions: 特征点相对位置关系
        """
        N = keypoints.shape[0]

        # 计算所有点对间的欧氏距离
        same_plane_pairs = []
        relative_positions = []

        for i in range(N):
            for j in range(i + 1, N):
                dist = np.linalg.norm(keypoints[i] - keypoints[j])

                # 检查是否在同一平面（z坐标接近）
                z_diff = abs(keypoints[i, 2] - keypoints[j, 2])
                if z_diff < distance_threshold_mm:
                    same_plane_pairs.append([int(i), int(j)])

                # 记录相对位置
                relative_positions.append({
                    "pair": [int(i), int(j)],
                    "distance_mm": float(dist),
                    "dx_mm": float(keypoints[i, 0] - keypoints[j, 0]),
                    "dy_mm": float(keypoints[i, 1] - keypoints[j, 1]),
                    "dz_mm": float(keypoints[i, 2] - keypoints[j, 2]),
                })

        return {
            "same_plane": same_plane_pairs,
            "relative_positions": relative_positions,
        }
