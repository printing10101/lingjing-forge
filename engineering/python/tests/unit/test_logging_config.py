"""core/logging_config 覆盖率补强测试。

覆盖：
- SensitiveDataFilter 全部敏感模式（含参数化日志/JSON/中文格式）
- RequestIdFilter / JSONFormatter / _MillisecondFormatter
- _DailySizeRotatingHandler 轮转、清理、写入失败
- configure_logging / configure_logging_with_config / shutdown_logging
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from app.core import logging_config as lc
from app.core.logging_config import (
    JSONFormatter,
    RequestIdFilter,
    SensitiveDataFilter,
    _DailySizeRotatingHandler,
    _MillisecondFormatter,
    configure_logging,
    configure_logging_with_config,
    shutdown_logging,
)


def make_record(msg: str, args=None, level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name="test",
        level=level,
        pathname=__file__,
        lineno=0,
        msg=msg,
        args=args,
        exc_info=None,
    )


# ---------------------------------------------------------------- SensitiveDataFilter


class TestSensitiveDataFilter:
    def test_param_tuple_args(self):
        f = SensitiveDataFilter()
        r = make_record("user %s login", ("password=s3cr3t",))
        f.filter(r)
        out = r.getMessage()
        assert "s3cr3t" not in out
        assert "password=***" in out

    def test_param_dict_args(self):
        # logging._log 会把 dict args 包成单元素 tuple，LogRecord.__init__
        # 再展开回 dict（Python 3.11）——模拟真实调用链
        f = SensitiveDataFilter()
        r = make_record("login token=%(token)s", ({"token": "tk_abc123def"},))
        assert isinstance(r.args, dict)
        f.filter(r)
        out = r.getMessage()
        assert "tk_abc123def" not in out
        assert "token=***" in out

    def test_plain_msg(self):
        f = SensitiveDataFilter()
        r = make_record("db connect with password=hunter2 ok")
        f.filter(r)
        assert "hunter2" not in r.getMessage()
        assert "password=***" in r.getMessage()

    @pytest.mark.parametrize(
        "text,secret",
        [
            ("cookie=abc123", "abc123"),
            ("private_key=PEMDATA", "PEMDATA"),
            ("access_key=AKIA123", "AKIA123"),
            ("refresh_token=rt_456", "rt_456"),
            ("passwd=p@ss", "p@ss"),
            ("pwd=short", "short"),
            ("secret=s3cr3t", "s3cr3t"),
            ("api_key=KEY123", "KEY123"),
            ("authorization=Bearer xyz", "Bearer xyz"),
        ],
    )
    def test_keywords(self, text, secret):
        f = SensitiveDataFilter()
        r = make_record(f"config {text}")
        f.filter(r)
        out = r.getMessage()
        assert secret not in out, f"脱敏失败: {out}"
        assert "***" in out

    def test_case_insensitive(self):
        f = SensitiveDataFilter()
        r = make_record("PASSWORD=MySecret TOKEN=abc")
        f.filter(r)
        out = r.getMessage()
        assert "MySecret" not in out
        assert "abc" not in out

    def test_jwt(self):
        f = SensitiveDataFilter()
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnopqrstuvwxyz"
        r = make_record(f"token in header {jwt}")
        f.filter(r)
        assert jwt not in r.getMessage()

    def test_email_idcard_phone(self):
        f = SensitiveDataFilter()
        r = make_record("contact a@b.com id 110101199003074512 phone 13800138000")
        f.filter(r)
        out = r.getMessage()
        assert "a@b.com" not in out
        assert "110101199003074512" not in out
        assert "13800138000" not in out

    def test_normal_log_untouched(self):
        f = SensitiveDataFilter()
        r = make_record("task %s completed in %d ms", ("train_001", 300))
        f.filter(r)
        assert r.getMessage() == "task train_001 completed in 300 ms"

    def test_format_failure_falls_back(self):
        # msg 无法用 args 格式化（% 语法不匹配） 合并回退，但第一层
        # PATTERNS 仍对 msg 脱敏（password=abc%d password=***），args 原样保留
        f = SensitiveDataFilter()
        r = make_record("login password=abc%d def", ("x",))
        f.filter(r)
        assert "abc%d" not in r.msg  # 值部分已被脱敏吞掉
        assert "password=***" in r.msg
        assert r.args == ("x",)  # args 原样保留

    def test_remaining_tuple_args_sanitized(self):
        f = SensitiveDataFilter()
        r = make_record("key=%s val=%s", ("password=secret1", "token=secret2"))
        f.filter(r)
        out = r.getMessage()
        assert "secret1" not in out and "secret2" not in out

    def test_sanitize_text_non_string(self):
        f = SensitiveDataFilter()
        assert f._sanitize_text(123) == 123
        assert f._sanitize_text(None) is None

    def test_sanitize_text_no_sentinel(self):
        f = SensitiveDataFilter()
        assert f._sanitize_text("plain message") == "plain message"

    def test_reentrancy_guard_skips_sanitizer(self):
        # 递归保护：active 时跳过 LogSanitizer 层，仍放行
        f = SensitiveDataFilter()
        lc._REENTRANCY_GUARD.active = True
        try:
            r = make_record("password=hunter2")
            assert f.filter(r) is True
        finally:
            lc._REENTRANCY_GUARD.active = False

    def test_sanitizer_exception_degraded(self):
        # LogSanitizer.sanitize 抛异常 降级不阻断
        f = SensitiveDataFilter()
        f._log_sanitizer = _BrokenSanitizer()
        r = make_record("password=hunter2")
        f.filter(r)
        assert "hunter2" not in r.getMessage()


class _BrokenSanitizer:
    def sanitize(self, text: str) -> str:
        raise RuntimeError("boom")


# ---------------------------------------------------------------- filters & formatters


class TestRequestIdFilter:
    def test_sets_request_id(self):
        r = make_record("x")
        RequestIdFilter().filter(r)
        assert hasattr(r, "request_id")


class TestJSONFormatter:
    def test_default_context(self):
        fmt = JSONFormatter()
        r = make_record("hello world")
        data = json.loads(fmt.format(r))
        assert data["message"] == "hello world"
        assert data["level"] == "INFO"
        assert "request_id" in data
        assert data["module"] == "test_logging_config"

    def test_no_context(self):
        fmt = JSONFormatter(include_context=False)
        r = make_record("plain")
        data = json.loads(fmt.format(r))
        assert "request_id" not in data

    def test_exc_info(self):
        fmt = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            r = make_record("failed", args=None, level=logging.ERROR)
            r.exc_info = sys.exc_info()
        data = json.loads(fmt.format(r))
        assert "ValueError: boom" in data["exception"]

    def test_extra_data(self):
        fmt = JSONFormatter()
        r = make_record("with extra")
        r.extra_data = {"k": "v"}
        data = json.loads(fmt.format(r))
        assert data["data"] == {"k": "v"}

    def test_non_serializable_default(self):
        fmt = JSONFormatter()
        r = make_record("obj")
        r.extra_data = {"path": Path("/tmp/x")}
        data = json.loads(fmt.format(r))
        assert "tmp" in str(data["data"]["path"])


class TestMillisecondFormatter:
    def test_format_time_millis(self):
        fmt = _MillisecondFormatter()
        r = make_record("x")
        # LogRecord 构造时 msecs 已按当时时间算好；同时设 created 与 msecs
        r.created = 1750000000.12345
        r.msecs = 123.45
        out = fmt.formatTime(r)
        assert out.endswith(".123")
        assert "1750000000" not in out  # 已格式化


# ---------------------------------------------------------------- rotating handler


class TestDailySizeRotatingHandler:
    def test_get_log_path_suffix(self, tmp_path: Path):
        h = _DailySizeRotatingHandler(str(tmp_path), module_name="app")
        assert h._get_log_path().name == "app.log"
        assert h._get_log_path(2).name == "app.2.log"

    def test_ensure_date_dir_creates(self, tmp_path: Path):
        h = _DailySizeRotatingHandler(str(tmp_path))
        h._ensure_date_dir()
        assert (tmp_path / datetime.now().strftime("%Y-%m-%d")).is_dir()
        assert h._current_file is not None
        h._close_stream()

    def test_open_stream_failure_raises(self, tmp_path: Path):
        h = _DailySizeRotatingHandler(str(tmp_path))
        # 目标路径是一个已存在的文件（无法作为目录） open 失败
        blocker = tmp_path / "blocker"
        blocker.mkdir(parents=True, exist_ok=True)
        h._log_root = blocker
        with pytest.raises((OSError, IOError)):
            h._open_stream()

    def test_close_stream_idempotent(self, tmp_path: Path):
        h = _DailySizeRotatingHandler(str(tmp_path))
        h._ensure_date_dir()
        h._close_stream()
        h._close_stream()  # 二次关闭不抛
        assert h._stream is None

    def test_rotate_by_size_rotates(self, tmp_path: Path):
        h = _DailySizeRotatingHandler(str(tmp_path), max_bytes=10, backup_count=3)
        h._ensure_date_dir()
        # 写入超过 max_bytes
        h._stream.write("0123456789abcdef")
        h._stream.flush()
        h._rotate_by_size()
        date_dir = h._get_date_dir()
        assert (date_dir / "app.log").exists()
        assert (date_dir / "app.1.log").exists()

    def test_rotate_small_file_noop(self, tmp_path: Path):
        h = _DailySizeRotatingHandler(str(tmp_path), max_bytes=10_000)
        h._ensure_date_dir()
        h._stream.write("tiny")
        h._stream.flush()
        h._rotate_by_size()
        date_dir = h._get_date_dir()
        assert not (date_dir / "app.1.log").exists()

    def test_cleanup_old_dirs(self, tmp_path: Path):
        h = _DailySizeRotatingHandler(str(tmp_path), retention_days=1)
        old = tmp_path / "2000-01-01"
        old.mkdir(parents=True)
        (old / "x.log").write_text("old")
        h._cleanup_old_dirs()
        assert not old.exists()

    def test_cleanup_missing_root(self, tmp_path: Path):
        h = _DailySizeRotatingHandler(str(tmp_path))
        h._log_root = tmp_path / "nonexistent"
        h._cleanup_old_dirs()  # 不抛

    def test_emit_writes(self, tmp_path: Path):
        h = _DailySizeRotatingHandler(str(tmp_path))
        h.setFormatter(logging.Formatter("%(message)s"))
        h._ensure_date_dir()
        r = make_record("hello log")
        h.emit(r)
        h._close_stream()
        content = (h._get_date_dir() / "app.log").read_text(encoding="utf-8")
        assert "hello log" in content

    def test_emit_failure_handle_error(self, tmp_path: Path):
        h = _DailySizeRotatingHandler(str(tmp_path))
        h.setFormatter(logging.Formatter("%(message)s"))
        # stream 置为 None write 失败走 handleError
        h._stream = None
        r = make_record("x")
        h.emit(r)  # 不抛

    def test_close(self, tmp_path: Path):
        h = _DailySizeRotatingHandler(str(tmp_path))
        h._ensure_date_dir()
        h.close()
        assert h._stream is None


# ---------------------------------------------------------------- configure/shutdown


@pytest.fixture
def saved_root_logger():
    """保存并恢复 root logger 的 handlers/level，避免测试污染。"""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    yield root
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


class TestConfigureLogging:
    def test_console_only(self, saved_root_logger):
        root = saved_root_logger
        configure_logging(level=logging.WARNING)
        assert root.level == logging.WARNING
        assert any(
            isinstance(h, logging.StreamHandler) and not isinstance(h, logging.handlers.QueueHandler)
            for h in root.handlers
        )

    def test_json_format(self, saved_root_logger):
        configure_logging(json_format=True)
        root = logging.getLogger()
        stream = next(h for h in root.handlers if isinstance(h, logging.StreamHandler))
        assert isinstance(stream.formatter, JSONFormatter)

    def test_text_format(self, saved_root_logger):
        configure_logging(json_format=False)
        root = logging.getLogger()
        stream = next(h for h in root.handlers if isinstance(h, logging.StreamHandler))
        assert isinstance(stream.formatter, _MillisecondFormatter)

    def test_with_file_and_queue(self, saved_root_logger, tmp_path: Path):
        configure_logging(log_root=str(tmp_path), module_name="unit")
        root = logging.getLogger()
        assert any(isinstance(h, logging.handlers.QueueHandler) for h in root.handlers)
        shutdown_logging()

    def test_sentry_no_dsn(self, saved_root_logger, monkeypatch):
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        # enable_sentry=True 但无 DSN warning 分支
        configure_logging(enable_sentry=True)
        root = logging.getLogger()
        assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)

    def test_with_config_dict(self, saved_root_logger, tmp_path: Path):
        configure_logging_with_config(
            {
                "logLevel": "ERROR",
                "logRoot": str(tmp_path),
                "logModule": "cfg",
                "logMaxBytes": 1024,
                "logRetentionDays": 7,
            }
        )
        root = logging.getLogger()
        assert root.level == logging.ERROR
        shutdown_logging()

    def test_with_config_defaults(self, saved_root_logger):
        configure_logging_with_config({})
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_shutdown_no_listener(self, saved_root_logger):
        root = logging.getLogger()
        # 无 listener 时 shutdown 不抛
        if hasattr(root, "_file_queue_listener"):
            delattr(root, "_file_queue_listener")
        shutdown_logging()
