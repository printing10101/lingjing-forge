"""
Heartbeat Scheduling Core Module

Implements a Paperclip Heartbeat Execution architecture for automated LNN inference
and training task scheduling with database-backed wakeup queue and coalescing support.
"""

import asyncio
import logging
import time
import json
import random
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field, asdict
from typing import Any
from pathlib import Path
from enum import Enum

from app.utils.sqlite_retry import sqlite_retry
from app.utils.utils import get_output_dir
from app.utils.sqlite_pool import get_sqlite_manager

logger = logging.getLogger(__name__)


class ScheduleStatus(str, Enum):
    """任务状态"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


@dataclass
class ScheduledTask:
    """标准任务数据结构"""

    task_id: str
    agent_id: str
    schedule: str
    task_type: str
    params: dict[str, Any] = field(default_factory=dict)
    last_run: float | None = None
    next_run: float | None = None
    status: ScheduleStatus = ScheduleStatus.PENDING
    retry_count: int = 0
    max_retries: int = 3
    created_at: float | None = None
    updated_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduledTask":
        if "status" in data and isinstance(data["status"], str):
            data["status"] = ScheduleStatus(data["status"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class CronParser:
    """
    简化的Cron表达式解析器

    支持格式：分 时 日 月 星期
    支持特殊字符：* , - /

    性能优化：
        1. 分钟级 TTL 缓存：同一 cron_expr 在同一分钟内复用解析结果，
           避免 add_task 批量插入时重复遍历 7×24×60 时间槽。
        2. 字段预编译：将字段表达式一次性编译为匹配值集合（frozenset），
           替代每次循环中的字符串解析，大幅降低 _matches_field 开销。
    """

    # 缓存：键为 (cron_expr, minute_bucket)，值为时间戳列表
    # minute_bucket = int(time.time() // 60)，TTL 自然为 60 秒
    _CACHE: dict[tuple, list[float]] = {}
    _CACHE_LOCK = threading.Lock()
    _CACHE_MAX_SIZE = 256  # 防止无界增长（最多 256 个不同的 cron_expr）

    @staticmethod
    def parse(cron_expr: str) -> list[float]:
        """
        解析cron表达式，返回未来7天内的所有执行时间戳

        Args:
            cron_expr: Cron表达式 (分 时 日 月 星期)

        Returns:
            执行时间戳列表
        """
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"无效的Cron表达式: '{cron_expr}'。期望格式：分 时 日 月 星期（5个字段）")

        # 缓存键：同一 cron_expr 在同一分钟内结果一致
        # （执行时间粒度为分钟，second/microsecond 不影响结果集）
        minute_bucket = int(time.time() // 60)
        cache_key = (cron_expr, minute_bucket)

        with CronParser._CACHE_LOCK:
            cached = CronParser._CACHE.get(cache_key)
        if cached is not None:
            return cached

        timestamps = CronParser._compute_timestamps(parts)

        with CronParser._CACHE_LOCK:
            # 淘汰过期条目（超过 2 分钟的 bucket）
            stale_cutoff = minute_bucket - 2
            stale_keys = [k for k in CronParser._CACHE if k[1] < stale_cutoff]
            for k in stale_keys:
                del CronParser._CACHE[k]
            # 容量保护：超过上限时淘汰最旧 bucket
            if len(CronParser._CACHE) >= CronParser._CACHE_MAX_SIZE:
                oldest_bucket = min(b for _, b in CronParser._CACHE.keys())
                stale_keys = [k for k in CronParser._CACHE if k[1] == oldest_bucket]
                for k in stale_keys:
                    del CronParser._CACHE[k]
            CronParser._CACHE[cache_key] = timestamps

        return timestamps

    @staticmethod
    def _compute_timestamps(parts: list[str]) -> list[float]:
        """实际计算时间戳列表（无缓存）"""
        minute_field, hour_field, day_field, month_field, dow_field = parts

        # 预编译字段为匹配值集合，避免在嵌套循环中重复解析字符串
        minute_set = CronParser._compile_field(minute_field, 0, 59)
        hour_set = CronParser._compile_field(hour_field, 0, 23)
        day_set = CronParser._compile_field(day_field, 1, 31)
        month_set = CronParser._compile_field(month_field, 1, 12)
        dow_set = CronParser._compile_dow(dow_field)

        now = datetime.now(timezone.utc)
        timestamps = []

        # 覆盖窗口必须严格大于一个完整周期。
        # cron 的最小周期是 7 天（星期字段），而本窗口以「今天」为第 0 天，且窗口内
        # 早于 now 的槽位会被下方 `exec_time > now` 过滤掉。若只取 range(7)，
        # 「今天正好是目标星期且目标时刻已过」的表达式会得到**空列表**——
        # 例如周日 20:05 求 ``30 2 * * 0``：唯一的周日就是今天，02:30 已过，
        # 下一个周日（offset=7）落在窗口外 → 该周任务永远不会被调度。
        # 取 8 天可保证任意 7 天周期表达式在窗口内至少命中一次未来的执行时刻。
        for day_offset in range(8):
            check_date = now + timedelta(days=day_offset)

            if check_date.month not in month_set:
                continue
            if check_date.day not in day_set:
                continue
            if check_date.weekday() not in dow_set:
                continue

            for hour in hour_set:
                for minute in minute_set:
                    exec_time = check_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

                    if exec_time > now:
                        timestamps.append(exec_time.timestamp())

        return sorted(timestamps)

    @staticmethod
    def _compile_dow(field_str: str) -> frozenset:
        """编译 cron 星期字段为 Python weekday 值集合。

        语义修正（2026-09-08）：cron 星期约定为 0=周日..6=周六（7 亦为周日），
        而 Python ``date.weekday()`` 为 0=周一..6=周日。此前实现直接比较两者，
        导致所有指定星期的表达式**整体错位一天**（如 ``* * * 0`` 实际在周一触发）。

        映射：cron d → python weekday (d + 6) % 7（0/7→周日=6，1→周一=0）。
        """
        cron_vals = CronParser._compile_field(field_str, 0, 7)
        return frozenset((v + 6) % 7 for v in cron_vals)

    @staticmethod
    def _compile_field(field_str: str, min_val: int, max_val: int) -> frozenset:
        """将 cron 字段表达式编译为匹配值集合

        相比 _matches_field 的逐值检查，预编译方式：
        - 一次解析覆盖整个值域
        - 集合查询 O(1)，远快于字符串解析 O(n)
        - 适合在 7×24×60 嵌套循环中复用
        """
        if field_str == "*":
            return frozenset(range(min_val, max_val + 1))

        result: set = set()
        for part in field_str.split(","):
            if "/" in part:
                base_str, step_str = part.split("/", 1)
                step = int(step_str)
                if base_str == "*":
                    start = min_val
                else:
                    start = int(base_str)
                for v in range(start, max_val + 1, step):
                    result.add(v)
            elif "-" in part:
                start_str, end_str = part.split("-", 1)
                start = int(start_str)
                end = int(end_str)
                for v in range(start, end + 1):
                    result.add(v)
            else:
                result.add(int(part))

        # 过滤越界值，避免无效字段污染匹配集
        return frozenset(v for v in result if min_val <= v <= max_val)

    @staticmethod
    def _matches_field(field_str: str, value: int, min_val: int, max_val: int) -> bool:
        """检查值是否匹配cron字段（保留向后兼容，新代码应使用 _compile_field）"""
        if field_str == "*":
            return True

        parts = field_str.split(",")
        for part in parts:
            if "/" in part:
                base_str, step_str = part.split("/", 1)
                step = int(step_str)
                if base_str == "*":
                    start = min_val
                else:
                    start = int(base_str)
                for v in range(start, max_val + 1, step):
                    if v == value:
                        return True
            elif "-" in part:
                start_str, end_str = part.split("-", 1)
                start = int(start_str)
                end = int(end_str)
                if start <= value <= end:
                    return True
            else:
                if int(part) == value:
                    return True

        return False

    @staticmethod
    def get_next_run(cron_expr: str, after_timestamp: float | None = None) -> float | None:
        """
        获取下次执行时间戳

        Args:
            cron_expr: Cron表达式
            after_timestamp: 参考时间戳（默认当前时间）

        Returns:
            下次执行时间戳，若无则返回None
        """
        timestamps = CronParser.parse(cron_expr)
        ref = after_timestamp or time.time()

        for ts in timestamps:
            if ts > ref:
                return ts

        return None

    @staticmethod
    def clear_cache() -> None:
        """清空缓存（测试用）"""
        with CronParser._CACHE_LOCK:
            CronParser._CACHE.clear()


class WakeupQueue:
    """基于SQLite的唤醒队列"""

    def __init__(self, db_path: str | None = None):
        """
        初始化唤醒队列

        Args:
            db_path: SQLite数据库路径
        """
        if db_path is None:
            db_path = str(get_output_dir("data") / "heartbeat.db")

        db_dir = Path(db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path
        # 使用统一的连接池管理器（传入 db_path 避免跨测试共享连接池死锁）
        self._manager = get_sqlite_manager()
        self._pool = self._manager.get_pool("heartbeat", db_path=self.db_path)
        # 关闭后置 None（见 close()）；保持 Any 类型避免 mypy union-attr 误报
        self._conn: Any = self._pool.get_connection()
        self._closed = False  # P3 幂等性标志位
        self._init_schema()

        logger.info("WakeupQueue initialized at %s", db_path)

    def close(self) -> None:
        """关闭队列，归还连接到连接池

        幂等性：
            多次调用安全；已关闭后再次调用为 no-op。
        """
        if self._closed:
            return
        if hasattr(self, "_conn") and self._conn:
            self._pool.return_connection(self._conn)
            self._conn = None
            logger.info("WakeupQueue closed")
        self._closed = True

    def _init_schema(self) -> None:
        """初始化数据库模式"""
        # [F-P0-6] 防复发：启用 WAL 模式，提高并发读写性能，防止读写阻塞导致心跳任务延迟。
        # 同步模式设为 NORMAL（WAL 模式推荐配置），在性能与数据安全间取得平衡。
        # foreign_keys=ON 保证外键约束生效，防止任务执行日志与主表数据不一致。
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        except sqlite3.Error as exc:
            logger.warning("Failed to set PRAGMA for WakeupQueue: %s", exc)

        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                task_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                schedule TEXT NOT NULL,
                task_type TEXT NOT NULL,
                params TEXT DEFAULT '{}',
                last_run REAL,
                next_run REAL,
                status TEXT DEFAULT 'pending',
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 3,
                created_at REAL,
                updated_at REAL,
                metadata TEXT DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_next_run ON scheduled_tasks(next_run);
            CREATE INDEX IF NOT EXISTS idx_status ON scheduled_tasks(status);
            CREATE INDEX IF NOT EXISTS idx_agent_id ON scheduled_tasks(agent_id);

            CREATE TABLE IF NOT EXISTS task_execution_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                execution_time REAL NOT NULL,
                status TEXT NOT NULL,
                duration_ms REAL,
                error_message TEXT,
                result_summary TEXT,
                created_at REAL
            );

            CREATE TABLE IF NOT EXISTS budget_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                metric_value REAL NOT NULL,
                metric_limit REAL,
                recorded_at REAL
            );

            CREATE TABLE IF NOT EXISTS execution_sessions (
                session_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL,
                checkpoint_data TEXT,
                started_at REAL,
                last_updated REAL,
                created_at REAL
            );
        """)
        self._conn.commit()

    @sqlite_retry()
    def add_task(self, task: ScheduledTask) -> ScheduledTask:
        """
        添加调度任务到队列

        Args:
            task: ScheduledTask实例

        Returns:
            添加的任务实例
        """
        now = time.time()
        if task.created_at is None:
            task.created_at = now
        task.updated_at = now

        if task.next_run is None and task.status == ScheduleStatus.PENDING:
            task.next_run = CronParser.get_next_run(task.schedule)

        self._conn.execute(
            """INSERT OR REPLACE INTO scheduled_tasks
               (task_id, agent_id, schedule, task_type, params, last_run, next_run,
                status, retry_count, max_retries, created_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.task_id,
                task.agent_id,
                task.schedule,
                task.task_type,
                json.dumps(task.params),
                task.last_run,
                task.next_run,
                task.status.value,
                task.retry_count,
                task.max_retries,
                task.created_at,
                task.updated_at,
                json.dumps(task.metadata),
            ),
        )
        self._conn.commit()

        logger.info("Task added to wakeup queue: %s (next_run=%s)", task.task_id, task.next_run)
        return task

    def get_due_tasks(self, current_time: float | None = None) -> list[ScheduledTask]:
        """
        获取到达执行时间的任务

        Args:
            current_time: 当前时间戳（默认当前时间）

        Returns:
            到期的任务列表
        """
        now = current_time or time.time()

        rows = self._conn.execute(
            """SELECT * FROM scheduled_tasks
               WHERE next_run <= ? AND status IN ('pending', 'completed')
               ORDER BY next_run ASC""",
            (now,),
        ).fetchall()

        tasks = []
        for row in rows:
            task_data = dict(row)
            task_data["params"] = json.loads(task_data["params"]) if task_data["params"] else {}
            task_data["metadata"] = json.loads(task_data["metadata"]) if task_data["metadata"] else {}
            tasks.append(ScheduledTask.from_dict(task_data))

        return tasks

    # 允许的列名白名单，防止 SQL 注入
    _ALLOWED_COLUMNS = {"status", "last_run", "next_run", "updated_at", "retry_count", "metadata"}

    @sqlite_retry()
    def update_task_status(self, task_id: str, status: ScheduleStatus, last_run: float | None = None) -> None:
        """
        更新任务状态

        Args:
            task_id: 任务ID
            status: 新状态
            last_run: 上次执行时间（可选）
        """
        now = time.time()
        # 值类型混合（status 枚举值 str / 时间戳 float / last_run 可能 str），
        # 显式标注 Any 避免 mypy 收紧为 str|float 后无法赋值 None
        updates: dict[str, Any] = {"status": status.value, "updated_at": now}

        if last_run is not None:
            updates["last_run"] = last_run
            if status == ScheduleStatus.COMPLETED:
                task = self.get_task(task_id)
                if task:
                    updates["next_run"] = CronParser.get_next_run(task.schedule, now)

        # 验证列名是否在白名单中，防止 SQL 注入
        for key in updates.keys():
            if key not in self._ALLOWED_COLUMNS:
                logger.warning("Attempted to update disallowed column: %s", key)
                continue

        set_clause = ", ".join(f"{k} = ?" for k in updates.keys() if k in self._ALLOWED_COLUMNS)
        values = [v for k, v in updates.items() if k in self._ALLOWED_COLUMNS] + [task_id]

        self._conn.execute(f"UPDATE scheduled_tasks SET {set_clause} WHERE task_id = ?", values)
        self._conn.commit()

        logger.debug("Task %s status updated to %s", task_id, status.value)

    def get_task(self, task_id: str) -> ScheduledTask | None:
        """获取指定任务"""
        row = self._conn.execute("SELECT * FROM scheduled_tasks WHERE task_id = ?", (task_id,)).fetchone()

        if row is None:
            return None

        task_data = dict(row)
        task_data["params"] = json.loads(task_data["params"]) if task_data["params"] else {}
        task_data["metadata"] = json.loads(task_data["metadata"]) if task_data["metadata"] else {}
        return ScheduledTask.from_dict(task_data)

    def is_task_running(self, task_id: str) -> bool:
        """检查任务是否正在执行（用于coalescing）"""
        row = self._conn.execute("SELECT status FROM scheduled_tasks WHERE task_id = ?", (task_id,)).fetchone()

        if row is None:
            return False

        return row["status"] == ScheduleStatus.RUNNING.value

    def pause_task(self, task_id: str) -> None:
        """暂停任务"""
        self.update_task_status(task_id, ScheduleStatus.PAUSED)

    @sqlite_retry()
    def resume_task(self, task_id: str) -> None:
        """恢复任务"""
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")

        next_run = task.next_run
        if next_run is None or next_run <= time.time():
            next_run = CronParser.get_next_run(task.schedule)

        now = time.time()
        self._conn.execute(
            "UPDATE scheduled_tasks SET status = ?, next_run = ?, updated_at = ? WHERE task_id = ?",
            (ScheduleStatus.PENDING.value, next_run, now, task_id),
        )
        self._conn.commit()

    @sqlite_retry()
    def delete_task(self, task_id: str) -> bool:
        """删除任务"""
        cursor = self._conn.execute("DELETE FROM scheduled_tasks WHERE task_id = ?", (task_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    @sqlite_retry()
    def log_execution(
        self,
        task_id: str,
        status: str,
        duration_ms: float | None = None,
        error_message: str | None = None,
        result_summary: str | None = None,
    ) -> None:
        """
        记录任务执行日志

        Args:
            task_id: 任务ID
            status: 执行状态
            duration_ms: 执行耗时（毫秒）
            error_message: 错误信息
            result_summary: 结果摘要
        """
        self._conn.execute(
            """INSERT INTO task_execution_log
               (task_id, execution_time, status, duration_ms, error_message, result_summary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                time.time(),
                status,
                duration_ms,
                error_message,
                result_summary,
                time.time(),
            ),
        )
        self._conn.commit()

    def get_task_history(self, task_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """获取任务执行历史"""
        rows = self._conn.execute(
            """SELECT * FROM task_execution_log
               WHERE task_id = ? ORDER BY execution_time DESC LIMIT ?""",
            (task_id, limit),
        ).fetchall()

        return [dict(row) for row in rows]

    def list_tasks(self, agent_id: str | None = None, status: ScheduleStatus | None = None) -> list[ScheduledTask]:
        """列出所有任务，支持过滤"""
        query = "SELECT * FROM scheduled_tasks WHERE 1=1"
        params = []

        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)

        if status:
            query += " AND status = ?"
            params.append(status.value)

        query += " ORDER BY next_run ASC"

        rows = self._conn.execute(query, params).fetchall()

        tasks = []
        for row in rows:
            task_data = dict(row)
            task_data["params"] = json.loads(task_data["params"]) if task_data["params"] else {}
            task_data["metadata"] = json.loads(task_data["metadata"]) if task_data["metadata"] else {}
            tasks.append(ScheduledTask.from_dict(task_data))

        return tasks


# [F-P0-6] 防复发说明：
# 历史上本文件存在两个 WakeupQueue.close 方法定义（L202 正确版 + L535 错误版），
# Python 解释器会以后者为准，导致 close() 直接调用 sqlite3.Connection.close()
# 而绕过连接池的 return_connection()，造成连接泄漏。
# 现已删除错误的重复定义，统一使用 L202 的正确实现（归还连接到连接池）。
# 防御性措施：在 __init__ 中通过 hasattr 检查避免重复初始化，并在 close 中
# 设置 self._conn = None 防止重复关闭。


class HeartbeatScheduler:
    """
    心跳调度器服务

    以每分钟为间隔扫描任务队列，精确触发所有到达执行时间的任务
    实现任务合并（coalescing）机制避免并发执行
    """

    def __init__(self, wakeup_queue: WakeupQueue | None = None, heartbeat_interval: int = 60):
        """
        初始化心跳调度器

        Args:
            wakeup_queue: 唤醒队列实例
            heartbeat_interval: 心跳间隔（秒，默认60秒）
        """
        self.wakeup_queue = wakeup_queue or WakeupQueue()
        self.heartbeat_interval = heartbeat_interval
        self._running = False
        self._stopped = False  # P3 幂等性标志位
        self._task: asyncio.Task | None = None
        self._on_task_trigger = None
        self._execution_stats = {
            "total_triggered": 0,
            "total_coalesced": 0,
            "total_failed": 0,
        }

        logger.info("HeartbeatScheduler initialized (interval=%ds)", heartbeat_interval)

    def set_task_trigger_callback(self, callback):
        """设置任务触发回调函数"""
        self._on_task_trigger = callback

    async def start(self) -> None:
        """启动心跳调度循环"""
        if self._running:
            logger.warning("HeartbeatScheduler already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info("HeartbeatScheduler started")

    async def stop(self) -> None:
        """停止心跳调度循环

        幂等性：
            多次调用安全；已停止后再次调用为 no-op。
        """
        if self._stopped:
            return
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                # 任务取消是正常的关闭信号，无需记录异常
                pass
            self._task = None

        self.wakeup_queue.close()
        logger.info("HeartbeatScheduler stopped")
        self._stopped = True

    async def _heartbeat_loop(self) -> None:
        """心跳循环主逻辑（含 jitter 防惊群）"""
        base_interval = self.heartbeat_interval
        while self._running:
            try:
                await self._tick()
            except (ConnectionError, OSError, RuntimeError) as e:
                logger.error("Heartbeat tick error: %s", e, exc_info=True)

            jitter = random.uniform(-base_interval * 0.2, base_interval * 0.2)
            sleep_time = max(1.0, base_interval + jitter)
            await asyncio.sleep(sleep_time)

    async def _tick(self) -> None:
        """单次心跳扫描"""
        now = time.time()
        due_tasks = self.wakeup_queue.get_due_tasks(now)

        if not due_tasks:
            return

        logger.info("Heartbeat tick: %d tasks due", len(due_tasks))

        for task in due_tasks:
            await self._trigger_task(task)

    async def _trigger_task(self, task: ScheduledTask) -> None:
        """
        触发单个任务执行

        实现coalescing机制：如果任务正在执行，则跳过本次调度
        """
        if self.wakeup_queue.is_task_running(task.task_id):
            self._execution_stats["total_coalesced"] += 1
            logger.info(
                "Task %s coalesced: already running from previous execution",
                task.task_id,
            )

            self.wakeup_queue.log_execution(
                task.task_id,
                "coalesced",
                result_summary="Skipped: previous execution still running",
            )
            return

        if task.status == ScheduleStatus.PAUSED:
            logger.debug("Task %s paused, skipping", task.task_id)
            return

        logger.info("Triggering task: %s (type=%s)", task.task_id, task.task_type)

        self.wakeup_queue.update_task_status(task.task_id, ScheduleStatus.RUNNING, last_run=time.time())

        self._execution_stats["total_triggered"] += 1

        if self._on_task_trigger:
            try:
                await self._on_task_trigger(task)
            except (ConnectionError, OSError, RuntimeError) as e:
                logger.error(
                    "Task %s trigger callback failed: %s",
                    task.task_id,
                    e,
                    exc_info=True,
                )
                self._execution_stats["total_failed"] += 1
                self.wakeup_queue.update_task_status(task.task_id, ScheduleStatus.FAILED)
                self.wakeup_queue.log_execution(task.task_id, "failed", error_message=type(e).__name__)

    def get_stats(self) -> dict[str, Any]:
        """获取调度器统计信息"""
        return {
            "running": self._running,
            "heartbeat_interval": self.heartbeat_interval,
            "execution_stats": self._execution_stats.copy(),
        }

    def schedule_task(self, task: ScheduledTask) -> ScheduledTask:
        """
        添加调度任务

        Args:
            task: 要调度的任务

        Returns:
            添加的任务实例
        """
        return self.wakeup_queue.add_task(task)

    def pause_task(self, task_id: str) -> None:
        """暂停任务"""
        self.wakeup_queue.pause_task(task_id)

    def resume_task(self, task_id: str) -> None:
        """恢复任务"""
        self.wakeup_queue.resume_task(task_id)

    def trigger_now(self, task_id: str) -> None:
        """立即触发指定任务"""
        task = self.wakeup_queue.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")

        # 修复：保存任务引用防止 GC 提前回收，并添加异常处理
        trigger_task = asyncio.create_task(self._trigger_task(task))
        trigger_task.add_done_callback(lambda t: self._handle_trigger_done(t, task_id))

    def _handle_trigger_done(self, task: asyncio.Task, task_id: str) -> None:
        """记录触发任务完成状态"""
        if task.cancelled():
            logger.warning("Trigger task %s was cancelled", task_id)
        elif task.exception():
            logger.error(
                "Trigger task %s failed: %s",
                task_id,
                task.exception(),
            )


class _SchedulerHolder:
    """Thread-safe lazy holder for the :class:`HeartbeatScheduler` singleton."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instance: HeartbeatScheduler | None = None

    def get(self) -> HeartbeatScheduler:
        # 快速路径：已存在则直接返回，避免持锁开销
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = HeartbeatScheduler()
            return self._instance

    def init(
        self,
        db_path: str | None = None,
        heartbeat_interval: int = 60,
    ) -> HeartbeatScheduler:
        """强制重新创建实例（用于启动时初始化的场景）。"""
        with self._lock:
            self._instance = HeartbeatScheduler(
                wakeup_queue=WakeupQueue(db_path),
                heartbeat_interval=heartbeat_interval,
            )
            return self._instance

    def reset(self) -> None:
        """Reset the cached instance (mainly for tests)."""
        with self._lock:
            self._instance = None


_holder = _SchedulerHolder()


def get_scheduler() -> HeartbeatScheduler:
    """获取共享的 :class:`HeartbeatScheduler` 单例；首次访问时懒初始化。

    Returns:
        :class:`HeartbeatScheduler` 实例（应用生命周期内同一实例）。

    Note:
        同时也是 FastAPI 依赖工厂，可直接用于 ``Depends(get_scheduler)``。
        实现是线程安全的，行为与重构前完全一致。
    """
    return _holder.get()


def init_scheduler(db_path: str | None = None, heartbeat_interval: int = 60) -> HeartbeatScheduler:
    """初始化心跳调度器，行为与重构前完全一致。"""
    return _holder.init(db_path=db_path, heartbeat_interval=heartbeat_interval)
