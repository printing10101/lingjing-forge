"""加工视频数据集模块。

实现加工过程视频数据加载、预处理和数据增强。
支持训练/验证/测试集划分（70%/15%/15%）。

数据格式：
- 正常加工视频：100小时，30fps，涵盖多种材料
- 异常加工视频：10小时+，包含4种异常类型
- 标注：帧级异常区间标注（开始帧、结束帧、异常类型）

Key components:
    - MachiningVideoDataset: PyTorch Dataset
    - VideoAugmentation: 视频数据增强
"""

import torch
from torch.utils.data import Dataset, DataLoader
import numpy as np
from typing import Dict, List
import json
import os
import random
import logging

logger = logging.getLogger(__name__)


class VideoAugmentation:
    """视频数据增强。

    对视频帧序列应用随机变换：
    - 随机旋转
    - 亮度调整
    - 高斯噪声
    """

    def __init__(
        self,
        rotation_range: float = 10.0,
        brightness_range: float = 0.15,
        gaussian_noise_std: float = 0.03,
    ):
        self.rotation_range = rotation_range
        self.brightness_range = brightness_range
        self.gaussian_noise_std = gaussian_noise_std

    def _random_rotation(self, frames: torch.Tensor) -> torch.Tensor:
        """随机旋转整个视频帧序列。

        Args:
            frames: (T, C, H, W)

        Returns:
            旋转后的帧序列
        """
        angle = random.uniform(-self.rotation_range, self.rotation_range)
        theta = np.radians(angle)
        cos_t, sin_t = np.cos(theta), np.sin(theta)
        rot_matrix = torch.tensor(
            [[cos_t, -sin_t, 0], [sin_t, cos_t, 0]], dtype=torch.float32
        ).unsqueeze(0)

        T, C, H, W = frames.shape
        grid = torch.nn.functional.affine_grid(
            rot_matrix.expand(T, -1, -1), (T, C, H, W), align_corners=False,
        )
        return torch.nn.functional.grid_sample(frames, grid, mode="bilinear",
                                               padding_mode="reflection", align_corners=False)

    def _random_brightness(self, frames: torch.Tensor) -> torch.Tensor:
        factor = 1.0 + random.uniform(-self.brightness_range, self.brightness_range)
        return torch.clamp(frames * factor, 0.0, 1.0)

    def _random_noise(self, frames: torch.Tensor) -> torch.Tensor:
        noise_std = random.uniform(0.0, self.gaussian_noise_std)
        noise = torch.randn_like(frames) * noise_std
        return torch.clamp(frames + noise, 0.0, 1.0)

    def __call__(self, frames: torch.Tensor, apply_prob: float = 0.5) -> torch.Tensor:
        """应用数据增强。

        Args:
            frames: (T, C, H, W)
            apply_prob: 每种增强的应用概率

        Returns:
            增强后的帧序列
        """
        augmented = frames.clone()
        if random.random() < apply_prob:
            augmented = self._random_rotation(augmented)
        if random.random() < apply_prob:
            augmented = self._random_brightness(augmented)
        if random.random() < apply_prob:
            augmented = self._random_noise(augmented)
        return augmented.clamp(0.0, 1.0)


class MachiningVideoDataset(Dataset):
    """加工过程视频数据集。

    支持正常/异常视频的统一加载和标注管理。

    标注格式（annotations.json）：
    {
      "videos": [
        {
          "video_id": "normal_001",
          "video_path": "videos/normal/normal_001.mp4",
          "material": "aluminum",
          "process": "milling",
          "duration_seconds": 3600,
          "fps": 30,
          "is_anomaly": false,
          "anomaly_intervals": []
        },
        {
          "video_id": "anomaly_001",
          "video_path": "videos/anomaly/anomaly_001.mp4",
          "material": "steel",
          "process": "milling",
          "duration_seconds": 60,
          "fps": 30,
          "is_anomaly": true,
          "anomaly_intervals": [
            {
              "start_frame": 450,
              "end_frame": 600,
              "anomaly_type": "tool_breakage",
              "severity": "severe"
            }
          ]
        }
      ]
    }

    Attributes:
        data_dir: 数据根目录
        split: 数据集划分
        config: 模型配置
        augment: 是否启用数据增强
    """

    ACTION_TYPES = ["tool_moving", "tool_change", "pause"]
    ANOMALY_TYPES = ["tool_breakage", "vibration_anomaly", "overcut", "collision"]
    SEVERITY_LEVELS = ["normal", "mild", "moderate", "severe", "danger"]
    MATERIALS = ["aluminum", "steel", "titanium", "plastic", "composite"]

    def __init__(
        self,
        data_dir: str,
        split: str = "train",
        config=None,
        augment: bool = True,
        seed: int = 42,
        lazy_load: bool = False,
    ):
        super().__init__()
        self.data_dir = data_dir
        self.split = split
        self.augment = augment and (split == "train")
        self.lazy_load = lazy_load

        from app.ai.vjepa_machining.config import VJEPAMachiningConfig
        self.config = config or VJEPAMachiningConfig()
        self.num_frames = self.config.num_frames
        self.frame_size = self.config.frame_size

        # 加载标注
        annotations_path = os.path.join(data_dir, "annotations.json")
        if not os.path.exists(annotations_path):
            logger.warning(f"Annotations not found: {annotations_path}, using dummy data")
            self.video_annotations = self._generate_dummy_annotations()
        else:
            with open(annotations_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.video_annotations = data.get("videos", [])

        # 数据集划分
        random.seed(seed)
        indices = list(range(len(self.video_annotations)))
        random.shuffle(indices)

        n_total = len(indices)
        n_train = int(n_total * 0.70)
        n_val = int(n_total * 0.15)

        if split == "train":
            selected = indices[:n_train]
        elif split == "val":
            selected = indices[n_train:n_train + n_val]
        else:
            selected = indices[n_train + n_val:]

        self.annotations = [self.video_annotations[i] for i in selected]

        # 构建片段索引（支持延迟加载）
        self._clips = None
        if not lazy_load:
            self._build_clip_index()

        # 数据增强
        self.transform = VideoAugmentation(
            self.config.rotation_range,
            self.config.brightness_range,
            self.config.gaussian_noise_std,
        ) if self.augment else None

        if not lazy_load:
            logger.info(f"Loaded MachiningVideoDataset [{split}]: {len(self.clips)} clips")
        else:
            logger.info(f"Loaded MachiningVideoDataset [{split}] in lazy mode: {len(self.annotations)} annotations")

    def _build_clip_index(self):
        """构建片段索引（可延迟调用）"""
        if self._clips is not None:
            return

        self._clips = []
        for ann in self.annotations:
            video_path = os.path.join(self.data_dir, ann["video_path"])
            total_frames = int(ann.get("duration_seconds", 10) * ann.get("fps", 30))
            num_clips = max(1, total_frames // self.num_frames)

            for clip_idx in range(num_clips):
                start_frame = clip_idx * self.num_frames
                end_frame = start_frame + self.num_frames
                if end_frame > total_frames:
                    start_frame = total_frames - self.num_frames
                    end_frame = total_frames

                # 检查是否在异常区间内
                anomaly_info = None
                for interval in ann.get("anomaly_intervals", []):
                    sf, ef = interval["start_frame"], interval["end_frame"]
                    if start_frame < ef and end_frame > sf:
                        anomaly_info = interval
                        break

                self._clips.append({
                    "video_path": video_path,
                    "start_frame": start_frame,
                    "end_frame": end_frame,
                    "material": ann.get("material", "aluminum"),
                    "process": ann.get("process", "milling"),
                    "anomaly_info": anomaly_info,
                })

    @property
    def clips(self):
        """延迟加载片段索引"""
        if self._clips is None:
            self._build_clip_index()
        return self._clips

    def __len__(self) -> int:
        return len(self.clips)

    def _load_video_clip(self, clip_info: dict) -> torch.Tensor:
        """加载视频片段并预处理。

        Args:
            clip_info: 片段信息

        Returns:
            (T, C, H, W)
        """
        video_path = clip_info["video_path"]
        start_frame = clip_info["start_frame"]

        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                raise RuntimeError(f"Cannot open video: {video_path}")

            cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
            frames = []
            for _ in range(self.num_frames):
                ret, frame = cap.read()
                if not ret:
                    break
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frame = cv2.resize(frame, (self.frame_size[1], self.frame_size[0]))
                frames.append(frame)
            cap.release()

            while len(frames) < self.num_frames:
                frames.append(frames[-1] if frames else np.zeros((*self.frame_size, 3)))

            frames = np.stack(frames).astype(np.float32) / 255.0
            return torch.from_numpy(frames).permute(0, 3, 1, 2)

        except ImportError:
            logger.warning("cv2 not available, generating dummy frames")
            return torch.randn(self.num_frames, 3, *self.frame_size).clamp(0, 1)

    def __getitem__(self, idx: int) -> Dict[str, torch.Tensor]:
        clip_info = self.clips[idx]

        # 加载视频片段
        frames = self._load_video_clip(clip_info)

        # 数据增强
        if self.transform is not None:
            frames = self.transform(frames)

        # 动作类型（简化：默认刀具移动）
        action_id = 0  # tool_moving
        if clip_info.get("anomaly_info"):
            action_id = 1  # 异常时假设为换刀相关

        # 异常标签
        anomaly_info = clip_info["anomaly_info"]
        if anomaly_info:
            is_anomaly = 1.0
            anomaly_type_idx = self.ANOMALY_TYPES.index(
                anomaly_info["anomaly_type"]
            ) if anomaly_info["anomaly_type"] in self.ANOMALY_TYPES else 0
            severity_idx = self.SEVERITY_LEVELS.index(
                anomaly_info.get("severity", "moderate")
            ) if anomaly_info.get("severity", "moderate") in self.SEVERITY_LEVELS else 2
        else:
            is_anomaly = 0.0
            anomaly_type_idx = 0
            severity_idx = 0

        # 材料索引
        material_idx = self.MATERIALS.index(
            clip_info["material"]
        ) if clip_info["material"] in self.MATERIALS else 0

        return {
            "video": frames,  # (T, C, H, W)
            "action_id": torch.tensor(action_id, dtype=torch.long),
            "is_anomaly": torch.tensor(is_anomaly, dtype=torch.float32),
            "anomaly_type": torch.tensor(anomaly_type_idx, dtype=torch.long),
            "severity": torch.tensor(severity_idx, dtype=torch.long),
            "material_id": torch.tensor(material_idx, dtype=torch.long),
            "clip_info": str(clip_info),
        }

    def get_dataloader(
        self,
        batch_size: int = 16,
        shuffle: bool = True,
        num_workers: int = 4,
        pin_memory: bool = True,
    ) -> DataLoader:
        return DataLoader(
            self,
            batch_size=batch_size,
            shuffle=shuffle,
            num_workers=num_workers,
            pin_memory=pin_memory,
            drop_last=(self.split == "train"),
        )

    def _generate_dummy_annotations(self) -> List[dict]:
        """生成虚拟标注数据用于测试开发。"""
        annotations = []

        # 正常视频
        materials = ["aluminum", "steel", "titanium", "plastic", "composite"]
        for i in range(50):
            annotations.append({
                "video_id": f"normal_{i:03d}",
                "video_path": f"videos/normal/normal_{i:03d}.mp4",
                "material": materials[i % len(materials)],
                "process": "milling",
                "duration_seconds": 7200,
                "fps": 30,
                "is_anomaly": False,
                "anomaly_intervals": [],
            })

        # 异常视频（每种异常类型25%）
        anomaly_configs = [
            {"type": "tool_breakage", "severity": "severe"},
            {"type": "vibration_anomaly", "severity": "moderate"},
            {"type": "overcut", "severity": "severe"},
            {"type": "collision", "severity": "danger"},
        ]

        for i in range(20):
            cfg = anomaly_configs[i % 4]
            annotations.append({
                "video_id": f"anomaly_{i:03d}",
                "video_path": f"videos/anomaly/anomaly_{i:03d}.mp4",
                "material": materials[i % len(materials)],
                "process": "milling",
                "duration_seconds": 180,
                "fps": 30,
                "is_anomaly": True,
                "anomaly_intervals": [{
                    "start_frame": 150,
                    "end_frame": 500,
                    "anomaly_type": cfg["type"],
                    "severity": cfg["severity"],
                }],
            })

        # 保存虚拟标注
        os.makedirs(os.path.join(self.data_dir), exist_ok=True)
        output_path = os.path.join(self.data_dir, "annotations.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({"videos": annotations}, f, indent=2, ensure_ascii=False)
        logger.info(f"Generated dummy annotations: {output_path}")

        return annotations
