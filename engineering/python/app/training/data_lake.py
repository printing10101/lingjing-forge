"""训练数据存储模块

实现训练数据的结构化存储，支持：
- 训练样本的JSON Lines格式存储
- 基于record_id的去重机制
- 批量写入与增量追加
- 训练数据查询与统计

设计原则：
- 只负责数据搬运，不包含数据分析或决策逻辑
- 使用文件系统存储，便于后续扩展到其他存储后端
- 线程安全的写入操作
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class TrainingDataLake:
    """训练数据湖存储

    负责将加工记录转换为训练样本并持久化存储。

    存储格式：
    - JSON Lines (.jsonl) 格式，每行一个JSON对象
    - 按日期分文件存储：training_data_YYYYMMDD.jsonl
    - 支持追加写入，不覆盖已有数据

    去重机制：
    - 基于record_id进行幂等处理
    - 写入前检查是否已存在相同record_id
    """

    def __init__(self, storage_dir: str | Path | None = None):
        """初始化训练数据湖

        Args:
            storage_dir: 存储目录路径，默认为 data/training_data
        """
        if storage_dir is None:
            # 默认存储路径
            base_dir = Path(__file__).parent.parent.parent
            storage_dir = base_dir / "data" / "training_data"

        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        logger.info("TrainingDataLake initialized: %s", self.storage_dir)

    def _get_today_file(self) -> Path:
        """获取今天的训练数据文件路径"""
        date_str = datetime.now().strftime("%Y%m%d")
        return self.storage_dir / f"training_data_{date_str}.jsonl"

    def _load_existing_record_ids(self, file_path: Path) -> set[str]:
        """加载文件中已有的record_id集合

        Args:
            file_path: JSON Lines文件路径

        Returns:
            record_id集合
        """
        record_ids: set[str] = set()
        if not file_path.exists():
            return record_ids

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        record_id = record.get("record_id")
                        if record_id:
                            record_ids.add(record_id)
                    except json.JSONDecodeError as e:
                        logger.warning("Failed to parse line in %s: %s", file_path, e)
        except (OSError, ValueError, TypeError, KeyError) as e:
            logger.error("Failed to load record IDs from %s: %s", file_path, e, exc_info=True)

        return record_ids

    def write_training_sample(self, sample: dict[str, Any]) -> bool:
        """写入单个训练样本

        Args:
            sample: 训练样本字典，必须包含record_id字段

        Returns:
            是否成功写入（False表示因重复而未写入）

        Raises:
            ValueError: 样本缺少record_id字段
        """
        if "record_id" not in sample:
            raise ValueError("Training sample must contain 'record_id' field")

        record_id = sample["record_id"]
        file_path = self._get_today_file()

        # 检查是否已存在
        existing_ids = self._load_existing_record_ids(file_path)
        if record_id in existing_ids:
            logger.info("Training sample %s already exists, skipping", record_id)
            return False

        # 追加写入
        try:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(sample, ensure_ascii=False) + "\n")
            logger.info("Training sample %s written to %s", record_id, file_path)
            return True
        except (OSError, TypeError, ValueError) as e:
            logger.error("Failed to write training sample %s: %s", record_id, e, exc_info=True)
            raise

    def write_training_samples(self, samples: list[dict[str, Any]]) -> dict[str, int]:
        """批量写入训练样本

        Args:
            samples: 训练样本列表

        Returns:
            统计信息字典，包含written和skipped数量
        """
        written = 0
        skipped = 0

        for sample in samples:
            try:
                if self.write_training_sample(sample):
                    written += 1
                else:
                    skipped += 1
            except (OSError, TypeError, ValueError) as e:
                logger.error("Failed to write sample: %s", e, exc_info=True)
                raise

        logger.info("Batch write completed: %s written, %s skipped", written, skipped)
        return {"written": written, "skipped": skipped}

    def load_training_samples(self, date: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        """加载训练样本

        Args:
            date: 日期字符串(YYYYMMDD)，默认加载所有日期的数据
            limit: 最大返回数量，默认不限制

        Returns:
            训练样本列表
        """
        samples = []

        if date:
            # 加载指定日期的数据
            file_path = self.storage_dir / f"training_data_{date}.jsonl"
            if file_path.exists():
                samples.extend(self._load_samples_from_file(file_path))
        else:
            # 加载所有日期的数据
            for file_path in sorted(self.storage_dir.glob("training_data_*.jsonl")):
                samples.extend(self._load_samples_from_file(file_path))

        # 应用limit
        if limit is not None and limit > 0:
            samples = samples[:limit]

        return samples

    def _load_samples_from_file(self, file_path: Path) -> list[dict[str, Any]]:
        """从文件加载训练样本

        Args:
            file_path: JSON Lines文件路径

        Returns:
            训练样本列表
        """
        samples = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        sample = json.loads(line)
                        samples.append(sample)
                    except json.JSONDecodeError as e:
                        logger.warning("Failed to parse line in %s: %s", file_path, e)
        except (OSError, ValueError, TypeError, KeyError) as e:
            logger.error("Failed to load samples from %s: %s", file_path, e, exc_info=True)

        return samples

    def get_statistics(self) -> dict[str, Any]:
        """获取训练数据统计信息

        Returns:
            统计信息字典，包含：
            - total_samples: 总样本数
            - total_files: 总文件数
            - date_range: 日期范围
            - record_ids: 所有record_id列表
        """
        total_samples = 0
        record_ids = []
        dates = []

        for file_path in self.storage_dir.glob("training_data_*.jsonl"):
            # 提取日期
            date_str = file_path.stem.replace("training_data_", "")
            dates.append(date_str)

            # 统计样本数
            samples = self._load_samples_from_file(file_path)
            total_samples += len(samples)
            record_ids.extend([s.get("record_id") for s in samples if s.get("record_id")])

        dates.sort()

        return {
            "total_samples": total_samples,
            "total_files": len(dates),
            "date_range": {"start": dates[0] if dates else None, "end": dates[-1] if dates else None},
            "record_ids": record_ids,
        }

    def check_record_exists(self, record_id: str) -> bool:
        """检查record_id是否已存在

        Args:
            record_id: 记录ID

        Returns:
            是否存在
        """
        for file_path in self.storage_dir.glob("training_data_*.jsonl"):
            existing_ids = self._load_existing_record_ids(file_path)
            if record_id in existing_ids:
                return True
        return False


__all__ = ["TrainingDataLake"]
