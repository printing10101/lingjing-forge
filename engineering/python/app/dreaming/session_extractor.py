"""Session 提取器：从项目历史数据中提取 Dreaming 的"Session"输入。

Session 概念约定：
    - 最多 100 个 Sessions 作为 Dream 的输入
    - 每个 Session 包含完整的对话/工作历史

本地化数据源映射：
    - MLflow runs → 训练/推理实验 Session
    - cam_validation report JSON → CAM 验证 Session
    - audit_log 哈希链 → 决策审计 Session
    - cutting_parameters 任务 → 切削参数推荐 Session

归一化输出 ProjectSession，供 DreamReflector 统一处理。

学术诚信约束（D-2）：
    - AR-02 修复前的 HRC52 数据标记为 ar_02_pre_fix，论文排除
    - 每条 Session 记录原始 artifact 路径，供审稿人复核

本模块为门面：实现已拆分至 _session_models / _sources_mixin。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.dreaming._session_models import (  # noqa: F401
    MAX_SESSIONS_PER_DREAM,
    ProjectSession,
)
from app.dreaming._sources_mixin import _SourcesMixin

logger = logging.getLogger(__name__)


class SessionExtractor(_SourcesMixin):
    """从多个数据源提取并归一化 Session 记录。

    用法：
        extractor = SessionExtractor(
            mlflow_tracking_uri="file://data/mlruns",
            cam_reports_dir="python/outputs/cam_validation",
            audit_log_dir="python/outputs/audit",
            cutting_store=...,
        )
        sessions = extractor.extract_sessions(lookback_days=30)
    """

    def __init__(
        self,
        mlflow_tracking_uri: str | None = None,
        cam_reports_dir: str | None = None,
        audit_log_dir: str | None = None,
        cutting_store: Any | None = None,
    ) -> None:
        # 默认路径与项目实验追踪器保持一致
        self.mlflow_tracking_uri = mlflow_tracking_uri or os.environ.get(
            "MLFLOW_TRACKING_URI",
            f"file://{os.path.abspath('data/mlruns')}",
        )
        self.cam_reports_dir = Path(cam_reports_dir or "python/outputs/cam_validation")
        self.audit_log_dir = Path(audit_log_dir or "python/outputs/audit")
        self.cutting_store = cutting_store

    # 主入口

    def extract_sessions(
        self,
        lookback_days: int = 30,
        max_sessions: int = MAX_SESSIONS_PER_DREAM,
        include_ar_02_pre_fix: bool = False,
    ) -> list[ProjectSession]:
        """提取过去 N 天的所有 Session。

        Args:
            lookback_days: 回溯天数
            max_sessions: 最大 Session 数（上限 100）
            include_ar_02_pre_fix: 是否包含 AR-02 修复前数据
                （默认 False，论文数据集应排除）

        Returns:
            归一化的 ProjectSession 列表，按时间倒序
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        sessions: list[ProjectSession] = []

        # 1. MLflow 实验记录
        try:
            sessions.extend(self._extract_mlflow_sessions(cutoff))
        except Exception as e:
            logger.warning("MLflow session 提取失败: %s", e)

        # 2. CAM 验证 report
        try:
            sessions.extend(self._extract_cam_sessions(cutoff))
        except Exception as e:
            logger.warning("CAM session 提取失败: %s", e)

        # 3. 审计日志
        try:
            sessions.extend(self._extract_audit_sessions(cutoff))
        except Exception as e:
            logger.warning("Audit session 提取失败: %s", e)

        # 4. 切削参数任务
        if self.cutting_store is not None:
            try:
                sessions.extend(self._extract_cutting_sessions(cutoff))
            except Exception as e:
                logger.warning("Cutting session 提取失败: %s", e)

        # 按时间排序，截断到 max_sessions
        sessions.sort(key=lambda s: s.timestamp, reverse=True)
        sessions = sessions[:max_sessions]

        # 学术诚信过滤：默认排除 AR-02 修复前数据
        if not include_ar_02_pre_fix:
            filtered = [s for s in sessions if not s.is_ar_02_pre_fix]
            logger.info(
                "Session 提取完成：total=%d, after_ar02_filter=%d",
                len(sessions),
                len(filtered),
            )
            return filtered

        logger.info("Session 提取完成：%d sessions", len(sessions))
        return sessions
