"""统一日志管理系统。

日志格式: [YYYY-MM-DD HH:MM:SS.SSS] [级别] [模块名] 消息内容
支持功能：日期目录结构、文件大小轮转、自动清理旧日志、JSON格式化、Sentry集成
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import queue as _queue_mod
import re
import sys
import threading

import time as _time
from typing import TextIO
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.request_id import get_request_id
from app.config.limits import DEFAULT_MAX_BYTES

logger = logging.getLogger(__name__)

LOG_FORMAT = "[%(asctime)s] [%(levelname)-5s] [%(name)s] [%(request_id)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ``DEFAULT_MAX_BYTES`` 由 ``app.config.limits`` 集中管理（50 MB），
# 与 ``main.py`` (``LOG_MAX_BYTES``) / ``config/logging_config.py``
# (``LoggingConfig.max_bytes`` 默认值) 共享同一基准值。
DEFAULT_BACKUP_COUNT = 5
DEFAULT_RETENTION_DAYS = 30

# P0-12 修复：线程本地递归保护，防止 LogSanitizer 内部调用 logger 时
# 再次触发 SensitiveDataFilter.filter 导致无限递归。
# 使用 threading.local() 保证多线程下标志位隔离。
_REENTRANCY_GUARD = threading.local()
_REENTRANCY_GUARD.active = False


class SensitiveDataFilter(logging.Filter):
    """日志脱敏过滤器

    优化：使用预编译的组合"哨兵"正则做一次快速扫描，仅在命中哨兵时
    才执行逐条替换，避免对绝大多数不含敏感信息的日志消息执行 9 次正则
    替换的开销。

    P0-12 修复：集成 LogSanitizer 作为第二层脱敏，补充 SensitiveDataFilter
    未覆盖的场景（工艺参数、文件路径、当前用户名、API 密钥模式等）。
    LogSanitizer 失败不得阻断日志记录，全部异常被捕获并降级为仅使用
    SensitiveDataFilter 自身的脱敏结果。
    """

    # 敏感信息模式（顺序保持稳定，便于阅读与维护）
    PATTERNS = [
        (re.compile(r'password["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)', re.IGNORECASE), "password=***"),
        (re.compile(r'passwd["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)', re.IGNORECASE), "passwd=***"),
        (re.compile(r'pwd["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)', re.IGNORECASE), "pwd=***"),
        (re.compile(r'token["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)', re.IGNORECASE), "token=***"),
        (re.compile(r'refresh[_-]?token["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)', re.IGNORECASE), "refresh_token=***"),
        (re.compile(r'secret["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)', re.IGNORECASE), "secret=***"),
        (re.compile(r'api[_-]?key["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)', re.IGNORECASE), "api_key=***"),
        (re.compile(r'access[_-]?key["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)', re.IGNORECASE), "access_key=***"),
        (re.compile(r'authorization["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)', re.IGNORECASE), "authorization=***"),
        (re.compile(r'cookie["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)', re.IGNORECASE), "cookie=***"),
        (re.compile(r'private[_-]?key["\']?\s*[:=]\s*["\']?([^"\'\s,}]+)', re.IGNORECASE), "private_key=***"),
        # JWT token
        (re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "jwt=***"),
        # 邮箱
        (re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"), "email=***"),
        # 身份证号（中国）
        (re.compile(r"\d{17}[\dXx]"), "id_card=***"),
        # 手机号（中国）
        (re.compile(r"1[3-9]\d{9}"), "phone=***"),
    ]

    # 组合哨兵：任一敏感关键字命中即触发逐条替换
    # 选择"出现概率极低但匹配廉价"的子串作为哨兵
    _SENTINEL = re.compile(
        r"password|passwd|pwd|token|refresh[_-]?token|secret|api[_-]?key|"
        r"access[_-]?key|authorization|cookie|private[_-]?key|eyJ|@|\d{17}|1[3-9]\d{9}",
        re.IGNORECASE,
    )

    def __init__(self) -> None:
        super().__init__()
        # P0-12 修复：延迟导入 LogSanitizer 避免循环依赖，初始化失败时降级为 None
        # 日志记录仍由 SensitiveDataFilter 自身模式保证，LogSanitizer 仅作为补充层
        self._log_sanitizer = None
        try:
            from app.core.log_sanitizer import sanitizer as _sanitizer_instance

            self._log_sanitizer = _sanitizer_instance
        except Exception as init_err:
            # 初始化失败不阻断日志系统，记录到 stderr（此时日志系统可能未就绪）
            import sys as _sys

            _sys.stderr.write(
                f"[SensitiveDataFilter] LogSanitizer init failed, "
                f"degraded mode (process params/paths NOT sanitized): {init_err}\n"
            )

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.msg
        args = record.args

        # P0-12 修复：递归保护 —— LogSanitizer 失败时若调用 logger.debug 会
        # 再次触发本 filter，可能无限递归。使用线程本地标志位阻断重入。
        if getattr(_REENTRANCY_GUARD, "active", False):
            # 已在脱敏流程中，跳过 LogSanitizer 二次脱敏，直接放行
            return True

        # 若存在 args，先将 msg 与 args 合并为完整字符串再做脱敏，
        # 确保参数化日志（如 logger.info("user %s login", password)）中的
        # 敏感数据也被覆盖。合并失败时回退为分别脱敏 msg 与 args。
        if args:
            try:
                if isinstance(args, dict):
                    merged = str(msg) % args
                else:
                    merged = str(msg) % args
                record.msg = merged
                record.args = None
                msg = merged
            except (TypeError, ValueError, KeyError, IndexError):
                # 格式化失败：分别脱敏 msg 与 args
                pass

        if isinstance(msg, str) and self._SENTINEL.search(msg):
            for pattern, replacement in self.PATTERNS:
                msg = pattern.sub(replacement, msg)
            record.msg = msg

        # P0-12 修复：第二层脱敏 —— 委托 LogSanitizer 处理工艺参数、
        # 文件路径、当前用户名等 SensitiveDataFilter 未覆盖的模式。
        # 任何异常被捕获以保证日志记录不被阻断（脱敏失败优于日志丢失）。
        if self._log_sanitizer is not None and isinstance(record.msg, str):
            _REENTRANCY_GUARD.active = True
            try:
                record.msg = self._log_sanitizer.sanitize(record.msg)
            except Exception:
                # LogSanitizer 异常不得影响日志输出，降级为仅使用第一层脱敏。
                # 不使用 logger.debug 记录此错误以避免递归；降级信息已通过
                # __init__ 的 stderr 警告提示运维方。
                pass
            finally:
                _REENTRANCY_GUARD.active = False

        # 若 args 仍存在（合并失败），对 args 中的字符串逐一脱敏
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(self._sanitize_text(a) if isinstance(a, str) else a for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: self._sanitize_text(v) if isinstance(v, str) else v for k, v in record.args.items()}

        return True

    def _sanitize_text(self, text: str) -> str:
        """对单个字符串应用全部敏感词替换。"""
        if not isinstance(text, str):
            return text
        if not self._SENTINEL.search(text):
            # 即使哨兵未命中，仍交给 LogSanitizer 处理工艺参数/路径等模式
            if self._log_sanitizer is not None and not getattr(_REENTRANCY_GUARD, "active", False):
                _REENTRANCY_GUARD.active = True
                try:
                    return self._log_sanitizer.sanitize(text)
                except Exception:
                    return text
                finally:
                    _REENTRANCY_GUARD.active = False
            return text
        for pattern, replacement in self.PATTERNS:
            text = pattern.sub(replacement, text)
        if self._log_sanitizer is not None and not getattr(_REENTRANCY_GUARD, "active", False):
            _REENTRANCY_GUARD.active = True
            try:
                text = self._log_sanitizer.sanitize(text)
            except Exception:
                pass
            finally:
                _REENTRANCY_GUARD.active = False
        return text


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class JSONFormatter(logging.Formatter):
    """JSON 格式化器，用于结构化日志输出"""

    def __init__(self, include_context: bool = True):
        super().__init__()
        self.include_context = include_context

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat() + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        if self.include_context:
            log_data["request_id"] = get_request_id()

        # 添加异常信息
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # 添加额外字段
        if hasattr(record, "extra_data"):
            log_data["data"] = record.extra_data

        return json.dumps(log_data, ensure_ascii=False, default=str)


class _MillisecondFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        ct = datetime.fromtimestamp(record.created)
        return ct.strftime("%Y-%m-%d %H:%M:%S") + f".{int(record.msecs):03d}"


class _AsyncConsoleHandler(logging.StreamHandler):
    """控制台日志处理器：把 ``sys.stdout`` 写入委托给后台守护线程。

    背景：桌面 sidecar（Tauri 以 stdio 管道捕获后端输出）以及本仓库系统级
    E2E 测试（``subprocess`` 的 ``stdout=PIPE``）下，``sys.stdout`` 是管道。
    若上游未及时排空，管道缓冲区（Windows 匿名管道默认仅 4KB）会写满，
    同步 ``StreamHandler.emit`` 的 ``stream.write`` 会**永久阻塞主线程**，
    导致 FastAPI 启动 / 请求处理卡死（栈：main.py:317 的 ``logger.info``
    卡在 ``logging/__init__.py`` 的 ``emit``）。

    本 handler 保持 ``logging.StreamHandler`` 形态（兼容既有测试对
    root.handlers 中 StreamHandler 的断言），但 ``emit`` 只把格式化后的消息
    放入内存队列，由后台守护线程负责实际写 ``sys.stdout`` 并 flush：
    - 主线程永不因 stdout 阻塞；
    - 管道写满时仅后台线程阻塞（守护线程，进程退出即回收，无泄漏）；
    - 写失败（管道关闭 / 流被替换）时丢弃该条，日志系统自身不受影响。
    """

    def __init__(self, stream: TextIO | None = None):
        super().__init__(stream)
        self._queue: _queue_mod.Queue[str | None] = _queue_mod.Queue()
        self._writer_thread = threading.Thread(
            target=self._writer,
            name="console-log-writer",
            daemon=True,
        )
        self._writer_thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._queue.put(msg + self.terminator)
        except Exception:  # noqa: BLE001
            # 格式化 / 入队失败不能反噬日志系统本身；
            # 仅在有活动异常时才调用 handleError（Python 3.14 无活动异常时会崩溃）。
            if sys.exc_info()[0] is not None:
                self.handleError(record)

    def _writer(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            try:
                if self._stream is not None:
                    self._stream.write(item)
                    self._stream.flush()
            except Exception:  # noqa: BLE001
                # 写失败（如管道关闭 / 流被替换）时丢弃该条，继续消费队列
                pass

    def close(self) -> None:
        self._queue.put(None)
        super().close()


class _DailySizeRotatingHandler(logging.Handler):
    def __init__(
        self,
        log_root: str,
        module_name: str = "app",
        max_bytes: int = DEFAULT_MAX_BYTES,
        backup_count: int = DEFAULT_BACKUP_COUNT,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        encoding: str = "utf-8",
    ):
        super().__init__()
        self._log_root = Path(log_root)
        self._module_name = module_name
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._retention_days = retention_days
        self._encoding = encoding

        self._current_date = ""
        self._current_file: str | None = None
        self._stream: TextIO | None = None
        self._lock = threading.Lock()
        self._last_size_check = 0.0
        self._size_check_interval = 10.0

    def _get_date_dir(self) -> Path:
        today = datetime.now().strftime("%Y-%m-%d")
        return self._log_root / today

    def _get_log_path(self, suffix: int = 0) -> Path:
        date_dir = self._get_date_dir()
        if suffix > 0:
            return date_dir / f"{self._module_name}.{suffix}.log"
        return date_dir / f"{self._module_name}.log"

    def _ensure_date_dir(self):
        date_dir = self._get_date_dir()
        date_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        if self._current_date != today:
            self._current_date = today
            self._close_stream()
            self._open_stream()
            self._cleanup_old_dirs()

    def _open_stream(self):
        log_path = self._get_log_path()
        self._current_file = str(log_path)
        try:
            self._stream = open(log_path, "a", encoding=self._encoding)
        except (OSError, IOError) as e:
            # 打开失败时清理已设置的状态，避免不一致
            logger.error("Failed to open log stream %s: %s", log_path, e, exc_info=True)
            self._current_file = None
            self._stream = None
            raise

    def _close_stream(self):
        if self._stream:
            try:
                self._stream.close()
            except (OSError, ValueError) as e:
                # 流关闭失败时仅记录，置空引用避免重复关闭
                logger.debug("Log stream close failed: %s", e, exc_info=True)
            self._stream = None
            self._current_file = None

    def _rotate_by_size(self):
        if not self._current_file:
            return
        current_path = Path(self._current_file)
        if not current_path.exists():
            return
        current_size = current_path.stat().st_size
        if current_size < self._max_bytes:
            return

        self._close_stream()

        for i in range(self._backup_count - 1, 0, -1):
            src = self._get_log_path(i)
            dst = self._get_log_path(i + 1)
            if src.exists():
                try:
                    src.replace(dst)
                except OSError as e:
                    # 日志轮转中单文件迁移失败不应阻塞后续轮转
                    logger.debug(
                        f"Failed to rotate log file {src} -> {dst}: {e}",
                        exc_info=True,
                    )

        base_path = self._get_log_path(0)
        backup_path = self._get_log_path(1)
        if base_path.exists():
            try:
                base_path.replace(backup_path)
            except OSError as e:
                # 当前日志归档失败时记录，继续打开新流
                logger.debug(
                    f"Failed to archive current log {base_path} -> {backup_path}: {e}",
                    exc_info=True,
                )

        self._open_stream()

    def _cleanup_old_dirs(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self._retention_days)).strftime("%Y-%m-%d")
        try:
            for item in sorted(self._log_root.iterdir()):
                if item.is_dir() and item.name < cutoff:
                    import shutil

                    shutil.rmtree(item, ignore_errors=True)
        except FileNotFoundError as e:
            # 日志目录在清理过程中被并发删除是常见情况，记录后继续
            logger.debug("Log root already removed during cleanup: %s", e)

    def emit(self, record: logging.LogRecord):
        try:
            with self._lock:
                self._ensure_date_dir()
                if _time.time() - self._last_size_check > self._size_check_interval:
                    self._rotate_by_size()
                    self._last_size_check = _time.time()

                msg = self.format(record) + "\n"
                if self._stream is None:
                    # 流未打开（打开失败时 _open_stream 置 None 并 raise）时降级，
                    # 避免 None.write 崩溃导致日志系统自身不可用。
                    # 仅在存在活动异常时才调用 handleError：Python 3.14 的
                    # handleError 在无活动异常（sys.exc_info()[0] is None）时会因
                    # exc.__traceback__ 为 None 抛出 AttributeError，进而崩溃日志
                    # monitor 线程，导致后端静默启动失败。
                    if sys.exc_info()[0] is not None:
                        self.handleError(record)
                    return
                self._stream.write(msg)
                self._stream.flush()
        except (OSError, ValueError, RuntimeError) as e:
            # 日志写入失败时调用 handleError，避免静默失败
            self.handleError(record)
            logger.debug("Log write failed: %s", e)

    def close(self):
        with self._lock:
            self._close_stream()
        super().close()


def configure_logging(
    level: int = logging.INFO,
    log_root: str | None = None,
    module_name: str = "app",
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    json_format: bool = False,
    enable_sentry: bool = False,
) -> None:
    """配置日志系统

    Args:
        level: 日志级别
        log_root: 日志文件根目录
        module_name: 模块名称
        max_bytes: 单个日志文件最大字节数
        backup_count: 保留的备份文件数量
        retention_days: 日志保留天数
        json_format: 是否使用 JSON 格式输出（适用于生产环境）
        enable_sentry: 是否启用 Sentry 错误追踪
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 根据配置选择格式化器
    if json_format:
        formatter: logging.Formatter = JSONFormatter(include_context=True)
    else:
        formatter = _MillisecondFormatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    # 创建脱敏过滤器
    sensitive_filter = SensitiveDataFilter()
    request_id_filter = RequestIdFilter()

    # 控制台输出：使用异步 handler，避免 stdout 为管道且写满时阻塞主线程
    # （桌面 sidecar / E2E 测试的 stdout=PIPE 场景下会永久死锁，详见
    # _AsyncConsoleHandler 类注释）。
    console_handler = _AsyncConsoleHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(sensitive_filter)
    console_handler.addFilter(request_id_filter)
    root_logger.addHandler(console_handler)

    if log_root:
        file_handler = _DailySizeRotatingHandler(
            log_root=log_root,
            module_name=module_name,
            max_bytes=max_bytes,
            backup_count=backup_count,
            retention_days=retention_days,
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(sensitive_filter)
        file_handler.addFilter(request_id_filter)

        # 使用 QueueHandler + QueueListener 模式将同步文件 I/O 移至后台线程，
        # 避免阻塞 asyncio 事件循环。日志记录入队后立即返回，QueueListener
        # 在单独线程中调用 file_handler.emit 落盘。
        log_queue: _queue_mod.Queue[logging.LogRecord] = _queue_mod.Queue(-1)
        queue_handler = logging.handlers.QueueHandler(log_queue)
        queue_handler.setLevel(level)
        # QueueHandler 仅负责入队；过滤与格式化由下游 listener 调用的
        # file_handler 完成，因此这里不再为 queue_handler 设置 filter/formatter。
        root_logger.addHandler(queue_handler)

        queue_listener = logging.handlers.QueueListener(log_queue, file_handler, respect_handler_level=True)
        queue_listener.start()
        # 将 listener 挂到 logger 模块属性上，便于 shutdown 时 enqueued 处理
        root_logger._file_queue_listener = queue_listener  # type: ignore[attr-defined]

        root_logger.info(
            "File logging enabled: root=%s module=%s max_bytes=%d retention=%dd",
            log_root,
            module_name,
            max_bytes,
            retention_days,
        )

    # Sentry 集成
    if enable_sentry:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.logging import LoggingIntegration

            sentry_dsn = os.getenv("SENTRY_DSN")
            if sentry_dsn:
                # P2-2-1/P2-2-2 修复：添加 before_send 回调过滤敏感信息，
                # 并显式设置 send_default_pii=False，防止 Sentry 自动采集
                # 用户 IP、Cookie、Authorization 头等 PII，满足 GDPR/SOC 2 合规。
                _sentry_sensitive_keys = frozenset(
                    {
                        "password",
                        "token",
                        "secret",
                        "api_key",
                        "apikey",
                        "authorization",
                        "cookie",
                        "refresh_token",
                        "access_token",
                        "private_key",
                        "session_id",
                    }
                )

                def _sentry_scrub(obj):
                    """递归脱敏 Sentry event 中的敏感字段。"""
                    if isinstance(obj, dict):
                        return {
                            k: ("***" if k.lower() in _sentry_sensitive_keys else _sentry_scrub(v))
                            for k, v in obj.items()
                        }
                    if isinstance(obj, list):
                        return [_sentry_scrub(i) for i in obj]
                    return obj

                def _sentry_before_send(event, hint):
                    """Sentry event 发送前过滤敏感信息。"""
                    try:
                        if "request" in event:
                            event["request"] = _sentry_scrub(event["request"])
                        if "extra" in event:
                            event["extra"] = _sentry_scrub(event["extra"])
                        if "contexts" in event:
                            event["contexts"] = _sentry_scrub(event["contexts"])
                    except Exception:
                        # 脱敏失败不应阻断 event 上报，但记录到本地日志
                        root_logger.warning("Sentry before_send scrub failed", exc_info=True)
                    return event

                sentry_sdk.init(
                    dsn=sentry_dsn,
                    integrations=[
                        LoggingIntegration(
                            # P2-2-3 修复：breadcrumb 级别从 INFO 提升到 WARNING，
                            # 减少 Sentry 事件体积与配额消耗，仅保留警告及以上上下文。
                            level=logging.WARNING,
                            event_level=logging.ERROR,
                        ),
                    ],
                    traces_sample_rate=0.1,
                    environment=os.getenv("APP_ENV", "production"),
                    before_send=_sentry_before_send,
                    send_default_pii=False,
                )
                root_logger.info("Sentry integration enabled (PII filtering active)")
            else:
                root_logger.warning("Sentry enabled but SENTRY_DSN not set")
        except ImportError:
            root_logger.warning("sentry-sdk not installed, Sentry integration disabled")
        except Exception as e:
            root_logger.error("Failed to initialize Sentry: %s", e, exc_info=True)

    root_logger.info(
        "Logging configured [level=%s format=%s]",
        logging.getLevelName(level),
        "json" if json_format else "text",
    )


def configure_logging_with_config(config: dict) -> None:
    configure_logging(
        level=getattr(logging, config.get("logLevel", "INFO").upper(), logging.INFO),
        log_root=config.get("logRoot"),
        module_name=config.get("logModule", "app"),
        max_bytes=config.get("logMaxBytes", DEFAULT_MAX_BYTES),
        retention_days=config.get("logRetentionDays", DEFAULT_RETENTION_DAYS),
    )


def shutdown_logging() -> None:
    """关闭日志系统，确保 QueueListener 中残留日志全部落盘。

    应在 FastAPI shutdown 事件中调用，避免进程退出时丢失队列中尚未写入
    的日志记录。
    """
    root_logger = logging.getLogger()
    listener = getattr(root_logger, "_file_queue_listener", None)
    if listener is not None:
        try:
            listener.stop()
        except (RuntimeError, OSError) as e:
            # 停止失败时仅记录到控制台，避免阻塞 shutdown
            logger.debug("QueueListener stop failed: %s", e, exc_info=True)
        try:
            delattr(root_logger, "_file_queue_listener")
        except AttributeError as del_attr:
            # 属性不存在说明已被清理或从未设置，属于正常情况
            logger.debug("_file_queue_listener already absent: %s", del_attr)
    # 刷新所有 handler，确保缓冲区写入
    for handler in root_logger.handlers:
        try:
            handler.flush()
        except (OSError, ValueError, RuntimeError) as flush_err:
            # shutdown 路径上 flush 失败不应阻塞，记录便于排查
            logger.debug("Handler flush failed during shutdown: %s", flush_err)


if __name__ == "__main__":
    # 自测：验证参数化日志脱敏生效
    print("=== SensitiveDataFilter 自测 ===")

    f = SensitiveDataFilter()

    # 1. 参数化日志（tuple args）—— 修复核心场景
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="user %s login",
        args=("password=s3cr3t",),
        exc_info=None,
    )
    f.filter(record)
    output = record.getMessage()
    assert "s3cr3t" not in output, f"参数化日志脱敏失败: {output}"
    assert "password=***" in output, f"脱敏标记缺失: {output}"
    print(f"[OK] tuple 参数化脱敏: {output!r}")

    # 2. dict 参数
    record2 = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="login token=%(token)s",
        args={"token": "tk_abc123def"},
        exc_info=None,
    )
    f.filter(record2)
    output2 = record2.getMessage()
    assert "tk_abc123def" not in output2, f"dict 参数脱敏失败: {output2}"
    print(f"[OK] dict 参数脱敏: {output2!r}")

    # 3. 普通 msg 脱敏（无 args）
    record3 = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="db connect with password=hunter2 ok",
        args=None,
        exc_info=None,
    )
    f.filter(record3)
    output3 = record3.getMessage()
    assert "hunter2" not in output3, f"普通 msg 脱敏失败: {output3}"
    print(f"[OK] 普通 msg 脱敏: {output3!r}")

    # 4. 新增关键词覆盖验证
    for kw in (
        "cookie=abc123",
        "private_key=PEMDATA",
        "access_key=AKIA123",
        "refresh_token=rt_456",
        "passwd=p@ss",
        "pwd=short",
    ):
        rec = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg=f"config {kw}",
            args=None,
            exc_info=None,
        )
        f.filter(rec)
        out = rec.getMessage()
        value = kw.split("=", 1)[1]
        assert value not in out, f"关键词 {kw!r} 脱敏失败: {out}"
        print(f"[OK] 关键词脱敏: {kw!r} -> {out!r}")

    # 5. 大小写不敏感验证
    record5 = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="PASSWORD=MySecret TOKEN=abc",
        args=None,
        exc_info=None,
    )
    f.filter(record5)
    output5 = record5.getMessage()
    assert "MySecret" not in output5, f"大小写不敏感脱敏失败: {output5}"
    assert "abc" not in output5, f"TOKEN 大写脱敏失败: {output5}"
    print(f"[OK] 大小写不敏感: {output5!r}")

    # 6. 无敏感信息日志不受影响
    record6 = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg="task %s completed in %d ms",
        args=("train_001", 300),
        exc_info=None,
    )
    f.filter(record6)
    output6 = record6.getMessage()
    assert output6 == "task train_001 completed in 300 ms", f"正常日志被篡改: {output6}"
    print(f"[OK] 正常日志不受影响: {output6!r}")

    print("=== 全部通过 ===")
