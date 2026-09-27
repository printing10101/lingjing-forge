"""日志系统规范化测试。

验证:
- configure_logging: 配置正确应用
- RequestIdFilter: request_id自动注入到日志记录
- 日志格式: [ISO 8601时间] [级别] [request_id] [模块] 消息
- 不同日志级别的输出效果
"""

import io
import logging
import re
from datetime import datetime

import pytest

from app.core.logging_config import configure_logging, LOG_FORMAT, RequestIdFilter
from app.core.request_id import set_request_id


ISO8601_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


class _TestISOFormatter(logging.Formatter):
    """测试用 ISO 8601 格式化器（毫秒 + Z 后缀；logging datefmt 不支持 %f）。"""

    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created).strftime(
            "%Y-%m-%dT%H:%M:%S"
        ) + f".{int(record.msecs):03d}Z"


class TestConfigureLogging:
    """测试日志配置函数"""

    def test_configures_root_logger(self):
        configure_logging(level=logging.DEBUG)
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) > 0

    def test_handler_has_formatter(self):
        configure_logging()
        root = logging.getLogger()
        for handler in root.handlers:
            if isinstance(handler, logging.StreamHandler):
                assert handler.formatter is not None
                assert handler.formatter._fmt == LOG_FORMAT

    def test_handler_has_request_id_filter(self):
        configure_logging()
        root = logging.getLogger()
        filters_found = False
        for handler in root.handlers:
            for f in handler.filters:
                if isinstance(f, RequestIdFilter):
                    filters_found = True
        assert filters_found

    def test_configure_logging_clears_previous_handlers(self):
        configure_logging()
        first_count = len(logging.getLogger().handlers)
        configure_logging()
        assert len(logging.getLogger().handlers) == first_count


class TestRequestIdFilter:
    """测试RequestIdFilter自动注入"""

    def test_filter_adds_request_id_to_record(self):
        set_request_id("test-rid-abc123")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=1,
            msg="test message",
            args=(),
            exc_info=None,
        )
        f = RequestIdFilter()
        result = f.filter(record)
        assert result is True
        assert record.request_id == "test-rid-abc123"

    def test_filter_uses_unknown_when_no_context(self):
        from app.core.request_id import _request_id_var

        token = _request_id_var.set("unknown")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=1,
                msg="test",
                args=(),
                exc_info=None,
            )
            f = RequestIdFilter()
            f.filter(record)
            assert record.request_id == "unknown"
        finally:
            _request_id_var.reset(token)


class TestLogFormat:
    """测试日志输出格式"""

    @pytest.fixture
    def capture_stream(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.DEBUG)
        handler.addFilter(RequestIdFilter())
        handler.setFormatter(_TestISOFormatter(fmt=LOG_FORMAT))
        return stream, handler

    def _parse_log_line(self, line):
        # LOG_FORMAT: [%(asctime)s] [%(levelname)s] [%(name)s] [%(request_id)s] msg
        m = re.match(r"^\[([^\]]+)\] \[(\S+)\s*\] \[(\S+)\] \[(\S+)\] (.*)$", line.strip())
        if m is None:
            return "", "", "", "", ""
        return m.group(1), m.group(2), m.group(4), m.group(3), m.group(5)

    def test_log_format_structure(self, capture_stream):
        stream, handler = capture_stream
        logger = logging.getLogger("test.structure")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        set_request_id("format-test-rid")
        logger.info("这是一条测试日志")

        handler.flush()
        output = stream.getvalue()
        assert output, "日志输出不应为空"

        timestamp, level, rid, module, message = self._parse_log_line(output)

        assert ISO8601_PATTERN.match(timestamp), f"时间格式不正确: {timestamp}"
        assert level == "INFO"
        assert rid == "format-test-rid"
        assert "test" in module
        assert message == "这是一条测试日志"

    def test_iso8601_timestamp_format(self, capture_stream):
        stream, handler = capture_stream
        logger = logging.getLogger("test.iso8601")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        logger.info("ISO 8601 test")

        handler.flush()
        timestamp = self._parse_log_line(stream.getvalue())[0]

        parsed = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")
        assert parsed.year >= 2026

    @pytest.mark.parametrize(
        "level,level_name",
        [
            (logging.DEBUG, "DEBUG"),
            (logging.INFO, "INFO"),
            (logging.WARNING, "WARNING"),
            (logging.ERROR, "ERROR"),
            (logging.CRITICAL, "CRITICAL"),
        ],
    )
    def test_log_level_differentiation(self, capture_stream, level, level_name):
        stream, handler = capture_stream
        logger = logging.getLogger(f"test.level.{level_name.lower()}")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        logger.log(level, f"Level {level_name} message")

        handler.flush()
        parsed_level = self._parse_log_line(stream.getvalue())[1]
        assert parsed_level == level_name

    def test_request_id_in_log_when_set(self, capture_stream):
        stream, handler = capture_stream
        logger = logging.getLogger("test.rid")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        set_request_id("log-test-rid-xyz")
        logger.warning("带request_id的警告")

        handler.flush()
        rid = self._parse_log_line(stream.getvalue())[2]
        assert rid == "log-test-rid-xyz"

    def test_unknown_request_id_when_not_set(self, capture_stream):
        from app.core.request_id import _request_id_var

        stream, handler = capture_stream
        logger = logging.getLogger("test.unknown")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        token = _request_id_var.set("unknown")
        try:
            logger.warning("无上下文的警告")
        finally:
            _request_id_var.reset(token)

        handler.flush()
        rid = self._parse_log_line(stream.getvalue())[2]
        assert rid == "unknown"


class TestLogFormatEdgeCases:
    """日志格式边界测试"""

    @pytest.fixture
    def capture(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.DEBUG)
        handler.addFilter(RequestIdFilter())
        handler.setFormatter(
            logging.Formatter(fmt=LOG_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S")
        )
        return stream, handler

    def test_empty_message(self, capture):
        stream, handler = capture
        logger = logging.getLogger("test.empty")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.info("")
        handler.flush()
        assert stream.getvalue() != ""

    def test_special_characters_in_message(self, capture):
        stream, handler = capture
        logger = logging.getLogger("test.special")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        msg = "包含中文、emoji😀、特殊字符!@#$%^&*()"
        logger.info(msg)
        handler.flush()
        output = stream.getvalue()
        assert "中文" in output
        assert "😀" in output

    def test_multiline_message(self, capture):
        stream, handler = capture
        logger = logging.getLogger("test.multi")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.warning("第一行\n第二行\n第三行")
        handler.flush()
        assert "第一行" in stream.getvalue()

    def test_exception_traceback(self, capture):
        stream, handler = capture
        logger = logging.getLogger("test.exc")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            raise ValueError("测试异常")
        except ValueError:
            logger.exception("捕获异常:")
        handler.flush()
        output = stream.getvalue()
        assert "ValueError" in output or "测试异常" in output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
