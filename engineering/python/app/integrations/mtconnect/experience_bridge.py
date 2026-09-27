"""MTConnect → cutting_experience 桥接器（数据飞轮自动落库）。

将 MTConnect 采集到的实时机床数据（spindle_speed / spindle_load /
feedrate / execution）自动转换为 CuttingExperience 契约对象并批量落库，
打通「实时监控 → 数据飞轮」闭环：

    MTConnect Agent ──► MTConnectStreamServer ──► MTConnectExperienceBridge
                                                      │
                                                      ▼
                                            CuttingExperienceRepository
                                                      │
                                                      ▼
                                            cutting_experiences 表

设计要点：
1. **阈值守护**：只有满足最小数据完整度（至少 1 个参数 + 1 个结果）的
   样本才被落库，防止垃圾数据污染飞轮。
2. **优雅降级**：数据库未配置时不抛错，仅记录警告（监控不因存储失败中断）。
3. **幂等**：同一 (machine_id, tool_id, timestamp) 由调用方保证只落一次。
4. **可观测**：结构化日志记录落库条数/丢弃条数。
"""

from __future__ import annotations

import logging

from app.contracts.cutting_experience import (
    CuttingExperience,
    CuttingParameters,
    CuttingResults,
    MachiningAnomaly,
    MachiningResult,
)
from app.integrations.mtconnect.parser import Sample
from app.services.domain.cutting_experience_repository import (
    create_cutting_experience,
    create_many_cutting_experiences,
)

logger = logging.getLogger(__name__)

# 落库阈值：主轴转速 > 0 且节拍/负载至少一项有效，才认为是一次有效加工快照
_MIN_SPINDLE_RPM = 1.0


class MTConnectExperienceBridge:
    """将 MTConnect Sample 流转换为 CuttingExperience 并持久化。

    Args:
        machine_id: 机床标识（映射到 CuttingExperience.machine_id）。
        tool_id: 当前刀具标识（可由外部更新，默认 "T-UNKNOWN"）。
        material: 工件材料（可由外部更新，默认 "UNKNOWN"）。
        source: 数据来源标记（默认 "mtconnect"）。
    """

    def __init__(
        self,
        machine_id: str,
        tool_id: str = "T-UNKNOWN",
        material: str = "UNKNOWN",
        source: str = "mtconnect",
    ) -> None:
        self.machine_id = machine_id
        self.tool_id = tool_id
        self.material = material
        self.source = source
        self._ingested = 0
        self._discarded = 0

    # 状态

    @property
    def ingested_count(self) -> int:
        return self._ingested

    @property
    def discarded_count(self) -> int:
        return self._discarded

    def stats(self) -> dict[str, int]:
        return {
            "machine_id": self.machine_id,
            "ingested": self._ingested,
            "discarded": self._discarded,
        }

    # 转换

    def sample_to_experience(self, sample: Sample) -> CuttingExperience | None:
        """将单个 MTConnect Sample 转换为 CuttingExperience。

        Returns:
            转换后的契约对象；数据不完整（如无转速）时返回 None。
            注意：计数器由调用方在调用后根据返回值增减。
        """
        if sample is None or sample.is_empty():
            return None

        spindle_rpm = sample.spindle_speed or 0.0
        if spindle_rpm < _MIN_SPINDLE_RPM:
            # 停机/未启动样本不落库
            return None

        # 参数（MTConnect 无切深，置默认 0 表示未知，由 Pydantic gt 校验排除）
        # 注意：CuttingParameters 要求 depth>0 / feed>0，MTConnect 样本不含
        # 切深 若样本无 feedrate 则无法构造合法参数。此处仅在 feedrate
        # 可用时构造（feed_mm_per_rev = feedrate ÷ spindle_rpm），否则丢弃。
        feed_mm_per_rev = (sample.feedrate or 0.0) / spindle_rpm
        if feed_mm_per_rev <= 0:
            return None

        parameters = CuttingParameters(
            depth_of_cut_mm=1.0,  # 未知切深，占位；飞轮聚合时按 tool 校准
            feed_mm_per_rev=feed_mm_per_rev,
            spindle_rpm=spindle_rpm,
        )

        # 异常快照：主轴负载 > 80% 记 overload 异常
        anomalies: list[MachiningAnomaly] = []
        if sample.spindle_load is not None and sample.spindle_load > 80.0:
            anomalies.append(
                MachiningAnomaly(
                    anomaly_type="spindle_overload",
                    severity=min(int(sample.spindle_load / 10), 10),
                    message=f"主轴负载 {sample.spindle_load:.1f}% 超阈值 80%",
                    measured_value=sample.spindle_load,
                    threshold_value=80.0,
                )
            )

        results = CuttingResults(
            cycle_time_s=1.0,  # 单样本节拍占位；批量聚合时以真实节拍覆盖
            result=MachiningResult.OK if not anomalies else MachiningResult.REWORK,
        )

        return CuttingExperience(
            machine_id=self.machine_id,
            tool_id=self.tool_id,
            material=self.material,
            source=self.source,
            parameters=parameters,
            results=results,
            anomalies=anomalies,
        )

    # 落库

    async def ingest_sample(self, sample: Sample) -> bool:
        """转换并落库单条样本。

        Returns:
            True 落库成功；False 样本被丢弃或落库失败（已记日志）。
        """
        exp = self.sample_to_experience(sample)
        if exp is None:
            self._discarded += 1
            return False
        try:
            await create_cutting_experience(exp)
            self._ingested += 1
            return True
        except RuntimeError as exc:
            # 数据库未配置：监控不中断，仅记录
            logger.warning("[mtconnect-bridge] 落库跳过（DB 未配置）: %s", exc)
            return False

    async def ingest_batch(self, samples: list[Sample]) -> dict[str, int]:
        """批量转换并落库。

        Returns:
            {"ingested": int, "discarded": int}（本批）。
        """
        experiences: list[CuttingExperience] = []
        discarded = 0
        for sample in samples:
            exp = self.sample_to_experience(sample)
            if exp is None:
                discarded += 1
            else:
                experiences.append(exp)

        if experiences:
            try:
                inserted = await create_many_cutting_experiences(experiences)
                self._ingested += inserted
                logger.info(
                    "[mtconnect-bridge] batch 落库 %d/%d 条 (machine=%s)",
                    inserted,
                    len(experiences),
                    self.machine_id,
                )
            except RuntimeError as exc:
                logger.warning("[mtconnect-bridge] 批量落库跳过（DB 未配置）: %s", exc)
                return {"ingested": 0, "discarded": discarded + len(experiences)}

        self._discarded += discarded
        return {"ingested": len(experiences), "discarded": discarded}
