"""AI 主权/自主等级策略服务（W9.1，对应 docs/产品叙事与战略对标-2026-09.md）。

历史问题：主权设置此前仅存于前端 localStorage（useSovereigntySettings），
不回传后端、不影响任何 AI 行为——叙事红线 5「数据主权不得只是前端开关」。

本模块让主权等级成为后端真实行为：

- **持久化**：sqlite3 标准库落盘 ``python/data/sovereignty_settings.db``
  （环境变量 ``SOVEREIGNTY_DB`` 可覆盖），与 LLM 注册表持久化同一模式；
- **五级语义**：与前端 useSovereigntySettings 的标签严格一致——
  0 完全手动 / 1 建议需确认 / 2 推荐模式（默认）/ 3 半自动（高置信自动）/ 4 全自动；
- **决策入口**：:meth:`SovereigntyPolicyEngine.evaluate_action` 返回结构化
  ``SovereigntyDecision``（是否需人工确认），由 rl_agent /act、训练启动等
  AI 动作端点消费，决定「自动执行」还是「转人工确认/审批」。

信任边界（与叙事红线一致）：主权等级**不**越过物理安全闸门——体素仿真
校验、DNC 下发硬闸、rl_agent SafetyShield 是独立于自主度的硬约束，任何
等级下都不放行未过闸的动作。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 半自动档（L3）的置信度阈值，与前端描述「高置信度（≥80%）自动执行」一致
AUTONOMY_CONFIDENCE_THRESHOLD = 0.8

# 自主等级 → 动作保守系数（W6b：等级越低，护盾介入越深）。
# 物理安全盾（SafetyShield）过滤后的动作，再按等级做保守缩放：
# 低自主等级下，任何被下游自动应用的调整都被向当前参数收敛（阻尼），
# 高自主等级不额外干预。默认档（2 推荐）系数 1.0，行为与历史一致。
CONSERVATISM_FACTORS: dict[int, float] = {
    0: 0.5,  # 完全手动：调整量减半
    1: 0.7,  # 建议需确认：调整量打七折
    2: 1.0,  # 推荐模式：不干预
    3: 1.0,  # 半自动：不干预（置信度门控在 evaluate_action）
    4: 1.0,  # 全自动：不干预
}


def conservatism_for_level(level: int) -> float:
    """返回给定自主等级的动作保守系数。"""
    if level not in CONSERVATISM_FACTORS:
        raise ValueError(f"未知自主等级: {level!r}（支持 0-4）")
    return CONSERVATISM_FACTORS[level]


def apply_conservatism(
    action: dict[str, float],
    level: int,
) -> tuple[dict[str, float], dict[str, Any]]:
    """按自主等级对候选动作（ActionField delta 字典）做保守缩放。

    Returns:
        (缩放后的动作字典, 缩放信息 {"level", "factor", "scaled"})
    """
    factor = conservatism_for_level(level)
    scaled = {k: round(float(v) * factor, 6) for k, v in action.items()}
    info = {"level": level, "factor": factor, "scaled": factor != 1.0}
    return scaled, info


# 支持的动作类型（决策语义见 evaluate_action）
ACTION_TYPES = ("predict", "train", "agent_action")

_VALID_LEVELS = range(0, 5)


@dataclass
class SovereigntySettings:
    """主权设置。字段与前端 useSovereigntySettings 一一对应。"""

    ai_autonomy_level: int = 2
    require_confirmation_for_predict: bool = False
    require_confirmation_for_train: bool = True
    show_confidence_indicator: bool = True
    show_alternatives: bool = True
    show_reasoning: bool = True

    def __post_init__(self) -> None:
        level = self.ai_autonomy_level
        if isinstance(level, bool) or not isinstance(level, int) or level not in _VALID_LEVELS:
            raise ValueError(f"ai_autonomy_level 必须是 0-4 的整数，实际: {level!r}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SovereigntySettings":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


AUTONOMY_LABELS = ("完全手动", "建议需确认", "推荐模式", "半自动", "全自动")


@dataclass
class SovereigntyDecision:
    """一次 AI 动作的主权决策结果。"""

    action_type: str
    autonomy_level: int
    autonomy_label: str
    requires_confirmation: bool
    reason: str
    confidence: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SovereigntyPolicyEngine:
    """主权策略引擎：设置存取 + 动作决策。线程安全。"""

    db_path: Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _settings: SovereigntySettings | None = field(default=None, repr=False)

    # ---- 持久化 ----

    def _resolve_db_path(self) -> Path:
        if self.db_path is not None:
            path = Path(self.db_path)
        else:
            env_path = os.environ.get("SOVEREIGNTY_DB")
            if env_path:
                path = Path(env_path)
            else:
                # 约定：python/data/sovereignty_settings.db（与 app.db 同级）
                python_dir = Path(__file__).resolve().parent.parent.parent
                path = python_dir / "data" / "sovereignty_settings.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _load_settings(self) -> SovereigntySettings:
        path = self._resolve_db_path()
        try:
            conn = sqlite3.connect(str(path), timeout=5.0)
            try:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS sovereignty_settings ("
                    " id INTEGER PRIMARY KEY CHECK (id = 1),"
                    " payload TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT '')"
                )
                row = conn.execute("SELECT payload FROM sovereignty_settings WHERE id = 1").fetchone()
            finally:
                conn.close()
            if row:
                return SovereigntySettings.from_dict(json.loads(row[0]))
        except (sqlite3.Error, json.JSONDecodeError, ValueError) as exc:
            logger.warning("主权设置加载失败，使用默认值: %s", exc)
        return SovereigntySettings()

    def _persist_settings(self, settings: SovereigntySettings) -> None:
        path = self._resolve_db_path()
        conn = sqlite3.connect(str(path), timeout=5.0)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sovereignty_settings ("
                " id INTEGER PRIMARY KEY CHECK (id = 1),"
                " payload TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT '')"
            )
            conn.execute(
                "INSERT INTO sovereignty_settings (id, payload, updated_at) VALUES (1, ?, datetime('now')) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
                (json.dumps(settings.to_dict(), ensure_ascii=False),),
            )
            conn.commit()
        finally:
            conn.close()

    # ---- 公共 API ----

    def get_settings(self) -> SovereigntySettings:
        """读取当前设置（懒加载 + 缓存）。"""
        with self._lock:
            if self._settings is None:
                self._settings = self._load_settings()
            return self._settings

    def update_settings(self, updates: dict) -> SovereigntySettings:
        """部分更新设置并持久化；非法字段值抛 ValueError，不落盘。"""
        with self._lock:
            current = self._settings or self._load_settings()
            merged = {**current.to_dict(), **updates}
            new_settings = SovereigntySettings.from_dict(merged)
            self._persist_settings(new_settings)
            self._settings = new_settings
            logger.info("主权设置已更新: level=%s", new_settings.ai_autonomy_level)
            return new_settings

    def reset(self) -> SovereigntySettings:
        """恢复默认设置并持久化。"""
        return self.update_settings(SovereigntySettings().to_dict())

    def evaluate_action(self, action_type: str, confidence: float | None = None) -> SovereigntyDecision:
        """对一次 AI 动作做主权决策：自动执行，还是需要人工确认。

        决策表（与前端五档语义严格一致）：

        = ====  =====================================================
        档位    行为
        = ====  =====================================================
        0/1     一律需确认（完全手动 / 建议需确认）
        2       按动作开关：predict→require_confirmation_for_predict，
                train→require_confirmation_for_train，
                agent_action→需确认（推荐模式 = 用户决定）
        3       train 受 require_confirmation_for_train 约束；其余动作
                confidence ≥ 0.8 自动，否则确认（半自动）
        4       一律自动（全自动；操作日志与物理安全闸门照常生效）
        = ====  =====================================================
        """
        if action_type not in ACTION_TYPES:
            raise ValueError(f"未知 action_type: {action_type!r}（支持: {ACTION_TYPES}）")
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence 必须在 [0, 1]，实际: {confidence!r}")

        settings = self.get_settings()
        level = settings.ai_autonomy_level

        if level >= 4:
            requires, reason = False, "全自动模式：AI 直接执行，操作日志供事后审查"
        elif level <= 1:
            requires = True
            reason = AUTONOMY_LABELS[level] + "：AI 建议需用户明确确认后执行"
        elif level == 2:
            if action_type == "predict":
                requires = settings.require_confirmation_for_predict
            elif action_type == "train":
                requires = settings.require_confirmation_for_train
            else:
                requires = True  # agent_action：推荐模式下由用户决定
            reason = "推荐模式：" + ("需用户确认" if requires else "已按用户设置放行")
        else:  # level == 3
            if action_type == "train" and settings.require_confirmation_for_train:
                requires, reason = True, "半自动模式：训练动作按用户设置需确认"
            elif confidence is not None and confidence >= AUTONOMY_CONFIDENCE_THRESHOLD:
                requires, reason = (
                    False,
                    f"半自动模式：置信度 {confidence:.2f} ≥ {AUTONOMY_CONFIDENCE_THRESHOLD}，自动执行",
                )
            else:
                requires, reason = True, "半自动模式：置信度不足或缺失，需用户确认"

        return SovereigntyDecision(
            action_type=action_type,
            autonomy_level=level,
            autonomy_label=AUTONOMY_LABELS[level],
            requires_confirmation=requires,
            reason=reason,
            confidence=confidence,
        )


# 单例管理（与仓库既有 holder 风格一致）


class _SovereigntyHolder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: SovereigntyPolicyEngine | None = None

    def get(self) -> SovereigntyPolicyEngine:
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = SovereigntyPolicyEngine()
            return self._instance

    def reset(self) -> None:
        with self._lock:
            self._instance = None


_holder = _SovereigntyHolder()


def get_sovereignty_policy() -> SovereigntyPolicyEngine:
    """获取全局主权策略引擎单例。"""
    return _holder.get()


def reset_sovereignty_policy() -> None:
    """重置全局单例（测试用）。"""
    _holder.reset()
