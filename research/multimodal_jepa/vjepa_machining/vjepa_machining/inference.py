"""V-JEPA实时推理引擎。

优化模型推理速度，确保实时性。
目标：平均推理时间 < 100ms，满足实时加工监控需求。

功能：
- 视频流处理模块：实时视频帧捕获与预处理
- 模型推理模块：优化的推理管线
- 异常决策模块：整合多模态特征的异常判定

Key components:
    - VJEPAInference: 推理引擎
    - VideoStreamProcessor: 视频流处理器
"""

import torch
import numpy as np
from typing import Dict, List, Optional, Tuple
import time
import logging
from collections import deque

from app.ai.vjepa_machining.config import VJEPAMachiningConfig
from app.ai.vjepa_machining.model import VJEPAMachiningModel

logger = logging.getLogger(__name__)


class VideoStreamProcessor:
    """实时视频流处理模块。

    维护滑动窗口缓冲区，持续捕获和预处理视频帧。

    Attributes:
        num_frames: 缓冲区大小（16帧）
        frame_size: 目标帧尺寸
        buffer: 帧缓冲区
        fps: 目标帧率
    """

    def __init__(
        self,
        num_frames: int = 16,
        frame_size: Tuple[int, int] = (224, 224),
        fps: int = 30,
    ):
        self.num_frames = num_frames
        self.frame_size = frame_size
        self.fps = fps
        self.buffer = deque(maxlen=num_frames)
        self.frame_count = 0
        self.buffer_ready = False

    def add_frame(self, frame: np.ndarray) -> None:
        """添加新帧到缓冲区。

        Args:
            frame: (H, W, 3) uint8 numpy数组
        """
        import cv2
        if frame.shape[:2] != self.frame_size:
            frame = cv2.resize(frame, (self.frame_size[1], self.frame_size[0]))
        self.buffer.append(frame.astype(np.float32) / 255.0)
        self.frame_count += 1
        if len(self.buffer) >= self.num_frames:
            self.buffer_ready = True

    def get_clip(self) -> Optional[torch.Tensor]:
        """获取当前缓冲区的视频片段。

        Returns:
            (1, T, C, H, W) 张量，缓冲区未满时返回None
        """
        if not self.buffer_ready:
            return None
        frames = np.stack(list(self.buffer))  # (T, H, W, C)
        tensor = torch.from_numpy(frames).permute(0, 3, 1, 2)  # (T, C, H, W)
        return tensor.unsqueeze(0)  # (1, T, C, H, W)

    def clear(self):
        """清空缓冲区。"""
        self.buffer.clear()
        self.buffer_ready = False
        self.frame_count = 0

    @property
    def ready(self) -> bool:
        return self.buffer_ready


class VJEPAInference:
    """V-JEPA实时推理引擎。

    提供高效的推理接口：
    - 单片段推理
    - 视频流推理
    - 性能基准测试
    - 结构化结果输出
    """

    def __init__(
        self,
        model: VJEPAMachiningModel,
        config: Optional[VJEPAMachiningConfig] = None,
        device: str = "cuda",
    ):
        self.model = model
        self.config = config or model.config
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")

        self.model = self.model.to(self.device)
        self.model.eval()

        self.stream_processor = VideoStreamProcessor(
            num_frames=self.config.num_frames,
            frame_size=self.config.frame_size,
        )

        # 推理统计
        self.inference_count = 0
        self.total_inference_time = 0.0
        self.inference_times = deque(maxlen=100)

        self._warmup()

    def _warmup(self):
        """模型预热。"""
        dummy = torch.randn(1, 3, self.config.num_frames, *self.config.frame_size, device=self.device)
        dummy_action = torch.zeros(1, dtype=torch.long, device=self.device)
        with torch.no_grad():
            _ = self.model.infer(dummy, dummy_action)
        logger.info("Inference engine warmup complete")

    @torch.no_grad()
    def infer_clip(
        self,
        video: torch.Tensor,
        action_id: int = 0,
        sensor_data: Optional[torch.Tensor] = None,
        format_results: bool = True,
    ) -> Dict:
        """单片段推理。

        Args:
            video: (1, T, C, H, W)
            action_id: 动作类型ID
            sensor_data: 传感器数据
            format_results: 是否格式化结果

        Returns:
            检测结果字典
        """
        video = video.to(self.device)
        action_ids = torch.tensor([action_id], dtype=torch.long, device=self.device)
        if sensor_data is not None:
            sensor_data = sensor_data.to(self.device)

        start_time = time.perf_counter()
        output = self.model.infer(video, action_ids, sensor_data)
        elapsed = (time.perf_counter() - start_time) * 1000

        self.inference_count += 1
        self.total_inference_time += elapsed
        self.inference_times.append(elapsed)

        if format_results:
            return self._format_output(output, elapsed)
        return {**output, "inference_time_ms": elapsed}

    def process_frame(
        self,
        frame: np.ndarray,
        action_id: int = 0,
        sensor_data: Optional[torch.Tensor] = None,
    ) -> Optional[Dict]:
        """处理单帧并进行异常检测。

        Args:
            frame: (H, W, 3) uint8 numpy数组
            action_id: 当前动作类型
            sensor_data: 传感器数据

        Returns:
            检测结果，缓冲区未满时返回None
        """
        self.stream_processor.add_frame(frame)
        clip = self.stream_processor.get_clip()
        if clip is None:
            return None
        return self.infer_clip(clip, action_id, sensor_data)

    def process_video_file(
        self,
        video_path: str,
        action_id: int = 0,
        max_frames: int = -1,
    ) -> List[Dict]:
        """处理整个视频文件。

        Args:
            video_path: 视频文件路径
            action_id: 动作类型
            max_frames: 最大处理帧数（-1=全部）

        Returns:
            每帧的检测结果列表
        """
        import cv2
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")

        results = []
        frame_idx = 0
        self.stream_processor.clear()

        while True:
            if max_frames > 0 and frame_idx >= max_frames:
                break

            ret, frame = cap.read()
            if not ret:
                break

            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = self.process_frame(frame, action_id)
            if result is not None:
                result["frame_idx"] = frame_idx - self.config.num_frames + 1
                results.append(result)

            frame_idx += 1

        cap.release()
        return results

    def _format_output(self, output: Dict, inference_time_ms: float) -> Dict:
        """格式化为结构化输出。

        Returns:
            - 帧级异常概率: 0-1, 3位小数
            - 异常类型分类: 断刀/振动异常/过切/撞刀/正常
            - 异常严重程度: 轻微/中等/严重/危险
            - 建议措施: 具体操作建议
        """
        anomaly_prob = round(float(output["anomaly_prob"].squeeze()), 3)
        type_idx = int(output["anomaly_type_pred"].squeeze())
        severity_idx = int(output["severity_pred"].squeeze())
        cosine_sim = round(float(output["cosine_similarity"].squeeze()), 3)
        euclidean_dist = round(float(output["euclidean_distance"].squeeze()), 3)

        is_anomaly = anomaly_prob > 0.5 or cosine_sim < 0.92

        if not is_anomaly:
            anomaly_type = "正常"
            severity = "正常"
        else:
            from app.ai.vjepa_machining.anomaly_head import AnomalyDetectionHead
            anomaly_type = AnomalyDetectionHead.ANOMALY_TYPES_CN[type_idx + 1]
            from app.ai.vjepa_machining.anomaly_head import SeverityAssessor
            severity = SeverityAssessor.SEVERITY_LEVELS[severity_idx]

        recommendation = AnomalyDetectionHead._get_recommendation(anomaly_type, severity)

        anomaly_type_probs = output["anomaly_type_probs"].squeeze().cpu().tolist()
        severity_probs = output["severity_probs"].squeeze().cpu().tolist()

        return {
            "帧级异常概率": anomaly_prob,
            "余弦相似度": cosine_sim,
            "欧氏距离": euclidean_dist,
            "异常类型": anomaly_type,
            "异常类型概率分布": dict(zip(
                ["正常", "断刀", "振动异常", "过切", "撞刀"],
                [1 - anomaly_prob] + anomaly_type_probs,
            )),
            "严重程度": severity,
            "严重程度概率分布": dict(zip(
                ["正常", "轻微", "中等", "严重", "危险"], severity_probs,
            )),
            "建议措施": recommendation,
            "推理耗时_ms": round(inference_time_ms, 2),
        }

    def benchmark(self, num_samples: int = 100) -> Dict[str, float]:
        """推理性能基准测试。

        Returns:
            性能指标字典
        """
        video = torch.randn(1, 3, self.config.num_frames, *self.config.frame_size, device=self.device)
        action = torch.zeros(1, dtype=torch.long, device=self.device)

        # 预热
        for _ in range(10):
            _ = self.model.infer(video, action)

        if self.device.type == "cuda":
            torch.cuda.synchronize()

        times = []
        for _ in range(num_samples):
            start = time.perf_counter()
            _ = self.model.infer(video, action)
            if self.device.type == "cuda":
                torch.cuda.synchronize()
            times.append((time.perf_counter() - start) * 1000)

        avg_time = np.mean(times)
        std_time = np.std(times)
        p95_time = np.percentile(times, 95)
        p99_time = np.percentile(times, 99)

        logger.info(
            f"Benchmark: avg={avg_time:.2f}ms, std={std_time:.2f}ms, "
            f"p95={p95_time:.2f}ms, p99={p99_time:.2f}ms, "
            f"throughput={1000/avg_time:.1f} clips/s"
        )

        return {
            "avg_time_ms": round(avg_time, 2),
            "std_time_ms": round(std_time, 2),
            "p95_time_ms": round(p95_time, 2),
            "p99_time_ms": round(p99_time, 2),
            "throughput_clips_per_sec": round(1000.0 / avg_time, 1),
        }

    def get_statistics(self) -> Dict[str, float]:
        """获取推理统计。

        Returns:
            统计信息字典
        """
        recent_times = list(self.inference_times)
        return {
            "total_inferences": self.inference_count,
            "total_time_ms": round(self.total_inference_time, 2),
            "avg_time_ms": round(np.mean(recent_times), 2) if recent_times else 0,
            "std_time_ms": round(np.std(recent_times), 2) if recent_times else 0,
            "min_time_ms": round(np.min(recent_times), 2) if recent_times else 0,
            "max_time_ms": round(np.max(recent_times), 2) if recent_times else 0,
        }
