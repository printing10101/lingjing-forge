"""I-JEPA 3D数据集模块。
实现机械零件三视图数据集加载、预处理和数据增强。支持训练/验证/测试集划分和批次数据生成。
数据格式要求：- 图像：256×256×3 RGB格式，像素值[0,1]
- 标注：json文件包含边界框和特征点。
Key components:
    - IJEPA3DDataset: PyTorch Dataset实现
    - DataAugmentation: 数据增强变换

Example:
    >>> dataset = IJEPA3DDataset(data_dir="data/ijepa_3d/", split="train")
    >>> sample = dataset[0]
"""

import torch
from torch.utils.data import Dataset, DataLoader
import numpy as np
from typing import Dict, Tuple
import json
import os
import logging
import random

logger = logging.getLogger(__name__)


class DataAugmentation:
    """三视图数据增强变换。
    实现以下增强操作：
    - 随机旋转（±15°）
    - 随机缩放（0.8-1.2倍）
    - 亮度调整（±20%）
    - 高斯噪声（±0.05）
    Attributes:
        rotation_range: 旋转角度范围（度）
        scale_range: 缩放范围
        brightness_range: 亮度调整范围
        gaussian_noise_std: 高斯噪声标准差
        image_size: 图像尺寸
    """

    def __init__(
        self,
        rotation_range: float = 15.0,
        scale_range: Tuple[float, float] = (0.8, 1.2),
        brightness_range: float = 0.20,
        gaussian_noise_std: float = 0.05,
        image_size: int = 256,
    ):
        """初始化数据增强。
        Args:
            rotation_range: 随机旋转角度范围（度）
            scale_range: 随机缩放范围
            brightness_range: 亮度调整范围
            gaussian_noise_std: 高斯噪声标准差
            image_size: 输出图像尺寸
        """
        self.rotation_range = rotation_range
        self.scale_range = scale_range
        self.brightness_range = brightness_range
        self.gaussian_noise_std = gaussian_noise_std
        self.image_size = image_size

    def _random_rotation(
        self,
        image: torch.Tensor,
    ) -> torch.Tensor:
        """对图像应用随机旋转。
        通过仿射变换实现图像旋转，使用双线性插值。
        Args:
            image: 输入图像 (C, H, W)

        Returns:
            旋转后的图像
        """
        angle = random.uniform(-self.rotation_range, self.rotation_range)
        theta = np.radians(angle)

        # 构建旋转矩阵
        cos_t = np.cos(theta)
        sin_t = np.sin(theta)
        rot_matrix = torch.tensor(
            [[cos_t, -sin_t, 0], [sin_t, cos_t, 0]],
            dtype=torch.float32,
        ).unsqueeze(0)

        grid = torch.nn.functional.affine_grid(
            rot_matrix,
            image.unsqueeze(0).size(),
            align_corners=False,
        )
        return torch.nn.functional.grid_sample(
            image.unsqueeze(0), grid,
            mode="bilinear", padding_mode="reflection",
            align_corners=False,
        ).squeeze(0)

    def _random_scale(
        self,
        image: torch.Tensor,
    ) -> torch.Tensor:
        """对图像应用随机缩放。
        Args:
            image: 输入图像 (C, H, W)

        Returns:
            缩放后的图像
        """
        scale = random.uniform(*self.scale_range)
        h, w = image.shape[-2:]

        new_h, new_w = int(h * scale), int(w * scale)
        scaled = torch.nn.functional.interpolate(
            image.unsqueeze(0), size=(new_h, new_w),
            mode="bilinear", align_corners=False,
        ).squeeze(0)

        if scale < 1.0:
            # 缩小时填充
            pad_h = (h - new_h) // 2
            pad_w = (w - new_w) // 2
            padded = torch.zeros(image.shape, dtype=image.dtype)
            padded[
                :,
                pad_h:pad_h + new_h,
                pad_w:pad_w + new_w,
            ] = scaled
            return padded
        else:
            # 放大时裁剪
            start_h = (new_h - h) // 2
            start_w = (new_w - w) // 2
            return scaled[:, start_h:start_h + h, start_w:start_w + w]

    def _random_brightness(
        self,
        image: torch.Tensor,
    ) -> torch.Tensor:
        """对图像应用随机亮度调整。
        Args:
            image: 输入图像 (C, H, W)

        Returns:
            亮度调整后的图像
        """
        factor = 1.0 + random.uniform(
            -self.brightness_range, self.brightness_range,
        )
        return torch.clamp(image * factor, 0.0, 1.0)

    def _random_gaussian_noise(
        self,
        image: torch.Tensor,
    ) -> torch.Tensor:
        """对图像添加高斯噪声。
        Args:
            image: 输入图像 (C, H, W)

        Returns:
            加噪后的图像
        """
        noise_std = random.uniform(0.0, self.gaussian_noise_std)
        noise = torch.randn_like(image) * noise_std
        return torch.clamp(image + noise, 0.0, 1.0)

    def __call__(
        self,
        image: torch.Tensor,
        apply_prob: float = 0.5,
    ) -> torch.Tensor:
        """应用数据增强。
        每种增强以50%概率随机应用。
        Args:
            image: 输入图像 (C, H, W)
            apply_prob: 每种增强的应用概率
        Returns:
            增强后的图像
        """
        augmented = image.clone()

        if random.random() < apply_prob:
            augmented = self._random_rotation(augmented)

        if random.random() < apply_prob:
            augmented = self._random_scale(augmented)

        if random.random() < apply_prob:
            augmented = self._random_brightness(augmented)

        if random.random() < apply_prob:
            augmented = self._random_gaussian_noise(augmented)

        return augmented.clamp(0.0, 1.0)


class IJEPA3DDataset(Dataset):
    """机械零件三视图数据集。
    数据格式要求：
    数据目录结构：
    data_dir/
      annotations.json     # 标注文件
      images/
        001_front.png       # 正视视图
        001_side.png        # 侧视视图
        001_top.png         # 俯视视图
        ...

    标注格式（annotations.json）：
    [
      {
        "id": "001",
        "part_type": "bracket",
        "bbox": {"cx": 100.0, "cy": 50.0, "cz": 30.0,
                 "length": 200.0, "width": 100.0, "height": 60.0},
        "keypoints": [
          {"x": 10.0, "y": 20.0, "z": 30.0},
          ...
        ],
        "topology": {"same_plane": [[0,1], [2,3]], "relations": [...]}
      },
      ...
    ]

    Attributes:
        data_dir: 数据根目录
        split: 数据集划分（train/val/test）
        config: 模型配置
        augment: 是否启用数据增强
    """

    PART_TYPES = [
        "bracket",          # 支架类
        "flange",           # 法兰类
        "stepped_shaft",    # 阶梯轴类
        "gear_blank",       # 齿轮毛坯类
        "housing",          # 壳体类
    ]

    def __init__(
        self,
        data_dir: str,
        split: str = "train",
        augment: bool = True,
        rotation_range: float = 15.0,
        scale_range: Tuple[float, float] = (0.8, 1.2),
        brightness_range: float = 0.20,
        gaussian_noise_std: float = 0.05,
        image_size: int = 256,
        num_keypoints: int = 10,
        seed: int = 42,
    ):
        """初始化数据集。
        Args:
            data_dir: 数据根目录
            split: 数据集划分（train/val/test）
            augment: 是否启用数据增强
            rotation_range: 旋转角度范围
            scale_range: 缩放范围
            brightness_range: 亮度调整范围
            gaussian_noise_std: 高斯噪声标准差
            image_size: 图像尺寸
            num_keypoints: 关键点数量
            seed: 随机种子
        """
        super().__init__()
        self.data_dir = data_dir
        self.split = split
        self.augment = augment and (split == "train")
        self.image_size = image_size
        self.num_keypoints = num_keypoints

        # 加载标注
        annotations_path = os.path.join(data_dir, "annotations.json")
        if not os.path.exists(annotations_path):
            raise FileNotFoundError(
                f"Annotations file not found: {annotations_path}",
            )

        with open(annotations_path, "r", encoding="utf-8") as f:
            all_annotations = json.load(f)

        # 数据集划分
        random.seed(seed)
        indices = list(range(len(all_annotations)))
        random.shuffle(indices)

        n_total = len(indices)
        n_train = int(n_total * 0.7)
        n_val = int(n_total * 0.15)

        if split == "train":
            selected = indices[:n_train]
        elif split == "val":
            selected = indices[n_train:n_train + n_val]
        else:
            selected = indices[n_train + n_val:]

        self.annotations = [all_annotations[i] for i in selected]
        self.num_samples = len(self.annotations)

        # 数据增强
        if self.augment:
            self.transform = DataAugmentation(
                rotation_range=rotation_range,
                scale_range=scale_range,
                brightness_range=brightness_range,
                gaussian_noise_std=gaussian_noise_std,
                image_size=image_size,
            )
        else:
            self.transform = None

        logger.info(
            f"Loaded IJEPA3DDataset [{split}]: {self.num_samples} samples",
        )

    def __len__(self) -> int:
        """返回数据集大小。"""
        return self.num_samples

    def _load_image(self, sample_id: str, view: str) -> torch.Tensor:
        """加载单张视图图像。
        Args:
            sample_id: 样本ID
            view: 视图名称（front/side/top）
        Returns:
            归一化图像张量 (3, 256, 256)，值域[0,1]
        """
        image_path = os.path.join(
            self.data_dir, "images", f"{sample_id}_{view}.png",
        )

        if not os.path.exists(image_path):
            # 如果图像不存在，生成占位符（纯黑图像）
            logger.warning(f"Image not found: {image_path}, using placeholder")
            return torch.zeros(3, self.image_size, self.image_size)

        try:
            from PIL import Image
            img = Image.open(image_path).convert("RGB")
            img = img.resize((self.image_size, self.image_size), Image.BILINEAR)
            img_array = np.array(img, dtype=np.float32) / 255.0
            return torch.from_numpy(img_array).permute(2, 0, 1)
        except ImportError:
            # PIL不可用时使用简单方法
            return torch.zeros(3, self.image_size, self.image_size)

    def _get_bbox_tensor(self, annotation: dict) -> torch.Tensor:
        """从标注提取边界框张量。
        Args:
            annotation: 样本标注

        Returns:
            边界框张量 (6,)：[cx, cy, cz, length, width, height]
        """
        bbox = annotation["bbox"]
        return torch.tensor([
            bbox["cx"], bbox["cy"], bbox["cz"],
            bbox["length"], bbox["width"], bbox["height"],
        ], dtype=torch.float32)

    def _get_keypoints_tensor(self, annotation: dict) -> torch.Tensor:
        """从标注提取关键点张量。
        Args:
            annotation: 样本标注

        Returns:
            关键点张量 (num_keypoints, 3)
        """
        keypoints = annotation.get("keypoints", [])
        kp_array = np.zeros((self.num_keypoints, 3), dtype=np.float32)

        for i, kp in enumerate(keypoints[:self.num_keypoints]):
            kp_array[i] = [kp["x"], kp["y"], kp["z"]]

        return torch.from_numpy(kp_array)

    def _get_part_type_idx(self, annotation: dict) -> int:
        """获取零件类型索引。
        Args:
            annotation: 样本标注

        Returns:
            类型索引（0-4）
        """
        part_type = annotation.get("part_type", "bracket")
        try:
            return self.PART_TYPES.index(part_type)
        except ValueError:
            return 0

    def __getitem__(self, idx: int) -> Dict[str, torch.Tensor]:
        """获取单个样本。
        Args:
            idx: 样本索引

        Returns:
            sample: 包含以下键的字典：
                - front_image: 正视视图 (3, 256, 256)
                - side_image: 侧视视图 (3, 256, 256)
                - top_image: 俯视视图 (3, 256, 256)
                - bbox: 边界框 (6,)
                - keypoints: 关键点 (num_keypoints, 3)
                - part_type: 零件类型索引
                - sample_id: 样本ID
        """
        annotation = self.annotations[idx]
        sample_id = annotation["id"]

        # 加载三视图
        front_img = self._load_image(sample_id, "front")
        side_img = self._load_image(sample_id, "side")
        top_img = self._load_image(sample_id, "top")

        # 数据增强
        if self.transform is not None:
            front_img = self.transform(front_img)
            side_img = self.transform(side_img)
            top_img = self.transform(top_img)

        # 提取标注
        bbox = self._get_bbox_tensor(annotation)
        keypoints = self._get_keypoints_tensor(annotation)
        part_type = self._get_part_type_idx(annotation)

        return {
            "front_image": front_img,
            "side_image": side_img,
            "top_image": top_img,
            "bbox": bbox,
            "keypoints": keypoints,
            "part_type": torch.tensor(part_type, dtype=torch.long),
            "sample_id": sample_id,
        }

    def get_dataloader(
        self,
        batch_size: int = 32,
        shuffle: bool = True,
        num_workers: int = 4,
        pin_memory: bool = True,
    ) -> DataLoader:
        """创建PyTorch DataLoader。
        Args:
            batch_size: 批次大小
            shuffle: 是否打乱
            num_workers: 数据加载线程数
            pin_memory: 是否固定内存

        Returns:
            DataLoader实例
        """
        return DataLoader(
            self,
            batch_size=batch_size,
            shuffle=shuffle,
            num_workers=num_workers,
            pin_memory=pin_memory,
            drop_last=(self.split == "train"),
        )

    def get_part_type_distribution(self) -> Dict[str, int]:
        """获取零件类型分布统计。
        Returns:
            类型计数字典
        """
        distribution = {pt: 0 for pt in self.PART_TYPES}
        for ann in self.annotations:
            pt = ann.get("part_type", "bracket")
            if pt in distribution:
                distribution[pt] += 1
            else:
                distribution[pt] = 1
        return distribution

    @staticmethod
    def generate_dummy_annotations(
        output_path: str,
        num_samples: int = 500,
        seed: int = 42,
    ) -> None:
        """生成虚拟标注数据（用于测试和开发）。
        Args:
            output_path: 输出JSON路径
            num_samples: 样本数量
            seed: 随机种子
        """
        random.seed(seed)
        np.random.seed(seed)

        annotations = []
        part_types = ["bracket", "flange", "stepped_shaft", "gear_blank", "housing"]

        for i in range(num_samples):
            part_type = part_types[i % len(part_types)]

            # 根据零件类型生成合理的几何参数范围
            if part_type == "bracket":
                cx, cy, cz = np.random.uniform(0, 200, 3)
                length, w, h = np.random.uniform(50, 300, 3)
            elif part_type == "flange":
                cx, cy, cz = np.random.uniform(0, 150, 3)
                length, w, h = np.random.uniform(30, 200, 3)
                h = np.random.uniform(10, 50)  # 法兰较薄
            elif part_type == "stepped_shaft":
                cx, cy, cz = np.random.uniform(0, 100, 3)
                length, w, h = np.random.uniform(20, 400, 3)
                w = np.random.uniform(20, 80)  # 轴较细
            elif part_type == "gear_blank":
                cx, cy, cz = np.random.uniform(0, 150, 3)
                length, w = np.random.uniform(30, 250, 2)
                h = np.random.uniform(10, 60)  # 齿轮较薄
            else:  # housing
                cx, cy, cz = np.random.uniform(0, 200, 3)
                length, w, h = np.random.uniform(40, 350, 3)

            # 关键点生成
            keypoints = []
            for j in range(10):
                kx = np.random.uniform(cx - length / 2, cx + length / 2)
                ky = np.random.uniform(cy - w / 2, cy + w / 2)
                kz = np.random.uniform(cz - h / 2, cz + h / 2)
                keypoints.append({"x": float(kx), "y": float(ky), "z": float(kz)})

            annotations.append({
                "id": f"{i + 1:03d}",
                "part_type": part_type,
                "bbox": {
                    "cx": float(cx), "cy": float(cy), "cz": float(cz),
                    "length": float(length), "width": float(w), "height": float(h),
                },
                "keypoints": keypoints,
            })

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(annotations, f, indent=2, ensure_ascii=False)

        logger.info(
            f"Generated {num_samples} dummy annotations at {output_path}",
        )
