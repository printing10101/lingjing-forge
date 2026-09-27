"""Dreaming 离线反思模块配置（ADR-021）。

环境变量前缀：LNN_DREAM_*

离线反思（Dreaming）机制的本地化集成。
仿生神经科学「记忆巩固」理论：Agent 在 Session 间隙离线审查 Memory Store，
执行去重合并、过时更新、跨 Session 洞察浮现，并将洞察转化为可执行规则。

三阶段闭环：
    Memory（工作中学习）→ Dreaming（休息时反思）→ Outcomes（自检反馈）

硬约束（__post_init__ 强制对齐项目记忆硬约束）：
    - cam_validation_required 始终 True（不被反思规则绕过）
    - allow_delete_succeeded 始终 False（SUCCEEDED 任务禁删）
    - hrc52_pending_calibration_penalty > 0（HRC52 强制降低置信度）
    - k_s_direct_passthrough 始终 True（K_s → cutting_force_coeff 直接传递，不二次拟合）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.config._utils import (
    _bool_env,
    _env,
    _float_env,
    _int_env,
    _path,
    logger,
)


@dataclass
class DreamingConfig:
    """Dreaming 离线反思模块配置（ADR-021）。

    所有配置项支持环境变量覆盖，遵循 12-Factor App 原则。
    环境变量前缀：LNN_DREAM_*

    P0/P1/P2 阶段已实现模块：
        - P0：LocalMemoryStore / SessionExtractor / DreamReflector /
              RuleSynthesizer / ReportGenerator / DreamingCLI
        - P1：DreamingAuditRecorder / DreamingSchedulerAdapter /
              RuleValidator / RuleApplicator
        - P2：ProgressivePublisher / EffectivenessMetricsCollector /
              RollbackManager / ClosedLoop
    """

    # 总开关
    # 桌面轻量档位下可关闭，避免 GraphStore + ChromaDB 加载开销
    enabled: bool = field(default_factory=lambda: _bool_env("LNN_DREAM_ENABLED", True))

    # 反思调度（P1 DreamingSchedulerAdapter）
    # HeartbeatScheduler cron 表达式：默认每天凌晨 02:00 触发反思
    # 生产环境应避开加工时段，避免与 CAM 校验任务竞争资源
    dream_cron_expression: str = field(default_factory=lambda: _env("LNN_DREAM_CRON", "0 2 * * *"))
    # 单次反思任务超时（秒）：GraphStore 遍历 + 洞察提取 + 规则合成约 5-30 分钟
    dream_task_timeout_seconds: int = field(default_factory=lambda: _int_env("LNN_DREAM_TASK_TIMEOUT", 1800))

    # Memory Store（P0 LocalMemoryStore）
    # GraphStore + 反思历史持久化目录
    memory_store_dir: str = field(
        default_factory=lambda: _path("LNN_DREAM_MEMORY_DIR", os.path.join("output", "dreaming", "memory"))
    )
    # Git 仓库目录（反思产物以分支形式归档）
    git_repo_dir: str = field(
        default_factory=lambda: _path("LNN_DREAM_GIT_DIR", os.path.join("output", "dreaming", "git"))
    )

    # Session 提取（P0 SessionExtractor）
    # MLflow tracking URI（Session 数据源之一）
    mlflow_tracking_uri: str = field(
        default_factory=lambda: _env(
            "LNN_DREAM_MLFLOW_URI",
            os.path.join("output", "mlruns"),
        )
    )
    # audit_log 路径（Session 数据源之二）
    audit_log_path: str = field(
        default_factory=lambda: _env(
            "LNN_DREAM_AUDIT_LOG_PATH",
            os.path.join("output", "logs", "audit_log.jsonl"),
        )
    )
    # cutting_store 路径（Session 数据源之三）
    cutting_store_path: str = field(
        default_factory=lambda: _env(
            "LNN_DREAM_CUTTING_STORE_PATH",
            os.path.join("output", "cutting_store.json"),
        )
    )
    # CAM 校验报告目录（Session 数据源之四）
    cam_report_dir: str = field(
        default_factory=lambda: _env(
            "LNN_DREAM_CAM_REPORT_DIR",
            os.path.join("output", "cam_validation"),
        )
    )

    # Reflector（P0 DreamReflector）
    # 最少触发反思的 Session 数：低于此值不触发反思（避免数据不足）
    min_sessions_for_dream: int = field(default_factory=lambda: _int_env("LNN_DREAM_MIN_SESSIONS", 5))
    # 洞察去重相似度阈值（0-1，余弦相似度）
    dedup_similarity_threshold: float = field(default_factory=lambda: _float_env("LNN_DEDUP_THRESHOLD", 0.85))
    # 单次反思最多提取的洞察数
    max_insights_per_dream: int = field(default_factory=lambda: _int_env("LNN_DREAM_MAX_INSIGHTS", 20))

    # Rule Synthesizer（P0 RuleSynthesizer）
    # 规则草稿输出目录
    rule_output_dir: str = field(
        default_factory=lambda: _path("LNN_DREAM_RULE_DIR", os.path.join("output", "dreaming", "rules"))
    )
    # 单次反思最多合成的规则数
    max_rules_per_dream: int = field(default_factory=lambda: _int_env("LNN_DREAM_MAX_RULES", 10))

    # Rule Validator（P1 RuleValidator）
    # 沙箱验证工作目录
    sandbox_validation_dir: str = field(
        default_factory=lambda: _path(
            "LNN_DREAM_SANDBOX_DIR",
            os.path.join("output", "dreaming", "sandbox"),
        )
    )
    # 单条规则沙箱验证超时（秒）
    validation_timeout_seconds: int = field(default_factory=lambda: _int_env("LNN_DREAM_VALIDATION_TIMEOUT", 120))

    # Progressive Publisher（P2）
    # 灰度发布记录持久化目录
    publication_records_dir: str = field(
        default_factory=lambda: _path(
            "LNN_DREAM_PUB_DIR",
            os.path.join("output", "dreaming", "publications"),
        )
    )
    # 默认初始灰度阶段（shadow / canary / rolling_10 / rolling_50 / full）
    # 生产环境应保持 shadow，仅在验证通过后通过 promote晋级
    default_initial_stage: str = field(default_factory=lambda: _env("LNN_DREAM_INITIAL_STAGE", "shadow"))
    # 晋级阈值：准确率达到此值才允许晋级
    promote_accuracy_threshold: float = field(default_factory=lambda: _float_env("LNN_DREAM_PROMOTE_ACC", 0.75))
    # 降级阈值：准确率低于此值触发降级
    demote_accuracy_threshold: float = field(default_factory=lambda: _float_env("LNN_DREAM_DEMOTE_ACC", 0.45))

    # Effectiveness Metrics（P2）
    # 度量样本持久化目录
    metrics_samples_dir: str = field(
        default_factory=lambda: _path(
            "LNN_DREAM_METRICS_DIR",
            os.path.join("output", "dreaming", "metrics_samples"),
        )
    )
    # 度量窗口天数（滚动窗口）
    metrics_window_days: int = field(default_factory=lambda: _int_env("LNN_DREAM_METRICS_WINDOW", 7))
    # 最小样本数（低于此值标记 insufficient_data，不阻断发布但置信度低）
    metrics_min_sample_size: int = field(default_factory=lambda: _int_env("LNN_DREAM_MIN_SAMPLES", 10))

    # Rollback Manager（P2）
    # 回滚历史持久化目录
    rollback_history_dir: str = field(
        default_factory=lambda: _path(
            "LNN_DREAM_ROLLBACK_DIR",
            os.path.join("output", "dreaming", "rollback_history"),
        )
    )
    # 冷却期小时数：回滚后规则进入冷却，期间不可重新发布
    rollback_cooldown_hours: int = field(default_factory=lambda: _int_env("LNN_DREAM_COOLDOWN_HOURS", 24))
    # 连续异常次数阈值：连续 N 次指标低于阈值触发回滚
    rollback_consecutive_anomaly_threshold: int = field(
        default_factory=lambda: _int_env("LNN_DREAM_CONSECUTIVE_ANOMALY", 3)
    )
    # 生产异常率阈值：超过此值立即回滚
    rollback_production_error_rate_threshold: float = field(
        default_factory=lambda: _float_env("LNN_DREAM_PROD_ERROR_RATE", 0.25)
    )

    # Closed Loop（P2，DempsterShaferFusion + TaskRouter）
    # 闭环状态持久化目录
    closed_loop_state_dir: str = field(
        default_factory=lambda: _path(
            "LNN_DREAM_CLOSED_LOOP_DIR",
            os.path.join("output", "dreaming", "closed_loop"),
        )
    )
    # 闭环决策置信度阈值（fused_confidence 高于此值才允许 promote）
    closed_loop_promote_confidence: float = field(default_factory=lambda: _float_env("LNN_DREAM_CL_PROMOTE_CONF", 0.75))
    # 闭环决策置信度阈值（fused_confidence 低于此值触发 demote）
    closed_loop_demote_confidence: float = field(default_factory=lambda: _float_env("LNN_DREAM_CL_DEMOTE_CONF", 0.45))
    # 闭环决策最小样本数（低于此值返回 keep，不触发 promote/demote）
    closed_loop_min_samples_for_decision: int = field(default_factory=lambda: _int_env("LNN_DREAM_CL_MIN_SAMPLES", 5))
    # 规则效果滚动窗口大小（每条规则最多保留的 outcome 样本数）
    rule_outcome_window_size: int = field(default_factory=lambda: _int_env("LNN_DREAM_RULE_WINDOW", 64))

    # 硬约束（__post_init__ 强制，不可被环境变量关闭）
    # CAM 二次校验强制（始终 True，不被反思规则绕过）
    cam_validation_required: bool = field(default_factory=lambda: _bool_env("LNN_DREAM_CAM_VALIDATION_REQUIRED", True))
    # SUCCEEDED 任务禁删（始终 False，避免追溯链断裂）
    allow_delete_succeeded: bool = field(default_factory=lambda: _bool_env("LNN_DREAM_ALLOW_DELETE_SUCCEEDED", False))
    # HRC52 pending_calibration 置信度惩罚系数（0-1，规则触发 HRC52 时强制乘以此值）
    hrc52_pending_calibration_penalty: float = field(default_factory=lambda: _float_env("LNN_DREAM_HRC52_PENALTY", 0.5))
    # K_s cutting_force_coeff 直接传递（始终 True，不二次拟合）
    k_s_direct_passthrough: bool = field(default_factory=lambda: _bool_env("LNN_DREAM_KS_DIRECT_PASSTHROUGH", True))

    def __post_init__(self) -> None:
        """启动时校验配置合法性，强制项目记忆硬约束。"""
        # 校验 cron 表达式非空
        if not self.dream_cron_expression.strip():
            logger.warning("LNN_DREAM_CRON 为空，使用默认值 '0 2 * * *'（每天凌晨 02:00）。")
            self.dream_cron_expression = "0 2 * * *"

        # 校验灰度阶段合法性
        valid_stages = {
            "shadow",
            "canary",
            "rolling_10",
            "rolling_50",
            "full",
        }
        if self.default_initial_stage not in valid_stages:
            logger.warning(
                "Invalid LNN_DREAM_INITIAL_STAGE='%s', expected one of %s. Falling back to 'shadow'.",
                self.default_initial_stage,
                sorted(valid_stages),
            )
            self.default_initial_stage = "shadow"

        # 校验阈值范围
        if not 0.0 <= self.promote_accuracy_threshold <= 1.0:
            logger.warning(
                "LNN_DREAM_PROMOTE_ACC=%s 超出 [0,1] 范围，重置为 0.75。",
                self.promote_accuracy_threshold,
            )
            self.promote_accuracy_threshold = 0.75

        if not 0.0 <= self.demote_accuracy_threshold <= 1.0:
            logger.warning(
                "LNN_DREAM_DEMOTE_ACC=%s 超出 [0,1] 范围，重置为 0.45。",
                self.demote_accuracy_threshold,
            )
            self.demote_accuracy_threshold = 0.45

        if self.demote_accuracy_threshold >= self.promote_accuracy_threshold:
            logger.warning(
                "LNN_DREAM_DEMOTE_ACC=%s >= PROMOTE_ACC=%s，"
                "会导致规则在 promote 与 demote 之间震荡，"
                "强制调整 demote 到 promote 的 60%%。",
                self.demote_accuracy_threshold,
                self.promote_accuracy_threshold,
            )
            self.demote_accuracy_threshold = self.promote_accuracy_threshold * 0.6

        # 校验闭环置信度阈值
        if not 0.0 <= self.closed_loop_promote_confidence <= 1.0:
            logger.warning(
                "LNN_DREAM_CL_PROMOTE_CONF=%s 超出 [0,1] 范围，重置为 0.75。",
                self.closed_loop_promote_confidence,
            )
            self.closed_loop_promote_confidence = 0.75

        if not 0.0 <= self.closed_loop_demote_confidence <= 1.0:
            logger.warning(
                "LNN_DREAM_CL_DEMOTE_CONF=%s 超出 [0,1] 范围，重置为 0.45。",
                self.closed_loop_demote_confidence,
            )
            self.closed_loop_demote_confidence = 0.45

        # 校验 HRC52 惩罚系数
        if not 0.0 < self.hrc52_pending_calibration_penalty <= 1.0:
            logger.warning(
                "LNN_DREAM_HRC52_PENALTY=%s 超出 (0,1] 范围，"
                "HRC52 pending_calibration 必须强制降低置信度，重置为 0.5。",
                self.hrc52_pending_calibration_penalty,
            )
            self.hrc52_pending_calibration_penalty = 0.5

        # 校验窗口大小
        if self.rule_outcome_window_size < 10:
            logger.warning(
                "LNN_DREAM_RULE_WINDOW=%s 太小（<10），样本不足以支撑 DS 融合，重置为 64。",
                self.rule_outcome_window_size,
            )
            self.rule_outcome_window_size = 64

        if self.min_sessions_for_dream < 1:
            logger.warning(
                "LNN_DREAM_MIN_SESSIONS=%s 无效（<1），重置为 5。",
                self.min_sessions_for_dream,
            )
            self.min_sessions_for_dream = 5

        # 项目记忆硬约束（不可被环境变量绕过）

        # 硬约束 1：CAM 二次校验始终 True（不被反思规则绕过）
        if not self.cam_validation_required:
            logger.warning(
                "LNN_DREAM_CAM_VALIDATION_REQUIRED=false 违反项目记忆硬约束"
                "（反思生成的规则不得绕过 CAM 二次校验），强制重置为 true。"
            )
            self.cam_validation_required = True

        # 硬约束 2：SUCCEEDED 任务禁删（始终 False，避免追溯链断裂）
        if self.allow_delete_succeeded:
            logger.warning(
                "LNN_DREAM_ALLOW_DELETE_SUCCEEDED=true 违反项目记忆硬约束"
                "（SUCCEEDED 任务可能被后续阶段引用，删除会破坏追溯链），"
                "强制重置为 false。"
            )
            self.allow_delete_succeeded = False

        # 硬约束 3：K_s cutting_force_coeff 直接传递（始终 True，不二次拟合）
        if not self.k_s_direct_passthrough:
            logger.warning(
                "LNN_DREAM_KS_DIRECT_PASSTHROUGH=false 违反项目记忆硬约束"
                "（K_s → cutting_force_coeff 必须直接传递，不二次拟合），"
                "强制重置为 true。"
            )
            self.k_s_direct_passthrough = True
