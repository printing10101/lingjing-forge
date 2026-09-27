"""Tests for :mod:`app.services.tdengine_client`.

These tests require a running TDengine instance reachable through the URL
configured in the ``TDENGINE_URL`` environment variable (default
``taos://root:@localhost:6030``).  They are designed to run
independently of the rest of the project: no FastAPI app, no LNN models,
no DXF / RAG dependencies are loaded.

If the ``taospy`` library is not installed or the TDengine service is
unreachable, the tests are skipped rather than failing - this is the
expected behavior for environments where the operator has not yet
provisioned TDengine.  When the service is available the test suite
verifies:

1. Synchronous ``get_tdengine()`` returns a connected client.
2. Asynchronous ``get_tdengine_async()`` returns the same client instance.
3. Database creation via ``ensure_database`` succeeds.
4. Table creation via ``create_table_if_not_exists`` succeeds.
5. Inserting 1000+ rows via ``insert_rows`` succeeds and reports the
   expected affected row count.
6. Time-range query via ``query_time_range`` returns the correct number
   of rows within the requested window.
7. Edge cases: empty insert list, query with no matches, value formatting
   helpers handle strings / numbers / None / bytes / datetime correctly.
8. ``check_tdengine_health`` returns a healthy status.

Run with::

    cd python && python -m pytest app/services/tests/test_tdengine_client.py -v

The service must be healthy beforehand (Docker compose healthcheck
returns ``healthy``).
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from collections.abc import Iterator

import pytest

# 允许 ``cd python && pytest app/services/tests/...`` 与 ``pytest`` 两种入口
_PYTHON_DIR = Path(__file__).resolve().parents[3]
if str(_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(_PYTHON_DIR))

from app.services import tdengine_client


# 条件跳过：未安装 taospy 或服务不可达时跳过


_HAS_TAOS = importlib.util.find_spec("taos") is not None


def _client_available() -> bool:
    """尝试建立一次真实连接，超时/失败返回 ``False``。"""
    if not _HAS_TAOS:
        return False
    try:
        client = tdengine_client.get_tdengine()
    except Exception:
        return False
    if client is None:
        return False
    try:
        client.execute("SELECT SERVER_STATUS()")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _HAS_TAOS,
    reason="taospy 未安装，跳过 TDengine 集成测试",
)


# Fixtures


@pytest.fixture(scope="module")
def tdengine_url() -> str:
    """测试用 TDengine URL（可通过环境变量覆盖）。"""
    return os.environ.get("TDENGINE_URL", "taos://root:@localhost:6030")


@pytest.fixture(scope="module")
def test_database() -> str:
    """使用独立测试数据库，避免污染生产数据。"""
    return os.environ.get("TDENGINE_TEST_DB", "lnn_tsdb_test")


@pytest.fixture(scope="module")
def tdengine_ready(tdengine_url: str) -> Iterator[None]:
    """在测试模块加载前建立连接；不可达时自动跳过依赖该 fixture 的用例。

    注意：此 fixture **不再 autouse**，仅显式声明 ``tdengine_ready`` 参数的
    测试/类会在服务不可达时被 skip。纯函数测试（如值格式化）保持可执行。
    """
    os.environ.setdefault("TDENGINE_URL", tdengine_url)
    if not _client_available():
        pytest.skip(
            f"TDengine service not reachable at {tdengine_url}; "
            "start the lnn-tdengine container before running these tests."
        )
    yield


@pytest.fixture()
def test_table(test_database: str) -> Iterator[str]:
    """为每个测试生成独立的表名，测试结束后删除。"""
    table_name = f"sensor_{uuid.uuid4().hex[:8]}"

    async def _setup() -> None:
        ok = await tdengine_client.ensure_database(test_database)
        assert ok, "ensure_database failed during test setup"
        ok = await tdengine_client.create_table_if_not_exists(
            table_name=table_name,
            columns=["(ts TIMESTAMP, value DOUBLE, machine_id INT)"],
            database=test_database,
        )
        assert ok, "create_table_if_not_exists failed during test setup"

    async def _teardown() -> None:
        client = tdengine_client.get_tdengine()
        if client is not None:
            try:
                client.execute(f"DROP TABLE IF EXISTS {test_database}.{table_name}")
            except Exception:
                pass

    _run(_setup)
    yield table_name
    _run(_teardown)


def _run(coro: Any) -> Any:
    """在新的事件循环中执行协程（pytest-asyncio 不可用时的回退方案）。"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 在已运行循环中创建新循环（pytest-asyncio 会驱动主循环）
            return asyncio.run(coro)
    except RuntimeError:
        pass
    return asyncio.run(coro)


# 单元测试


class TestTDengineConfig:
    def test_default_url_contains_localhost(self) -> None:
        cfg = tdengine_client.TDengineConfig()
        # 默认配置应允许本地开箱即用
        assert cfg.url
        assert cfg.user
        assert cfg.password
        assert cfg.database

    def test_enabled_property(self) -> None:
        cfg = tdengine_client.TDengineConfig()
        assert cfg.enabled is True

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TDENGINE_DB", "custom_db")
        monkeypatch.setenv("TDENGINE_USER", "custom_user")
        cfg = tdengine_client.TDengineConfig()
        assert cfg.database == "custom_db"
        assert cfg.user == "custom_user"


class TestClientConnection:
    # 显式声明依赖：服务不可达时本类整体 skip
    @pytest.fixture(autouse=True)
    def _require_service(self, tdengine_ready: None) -> None:
        return None

    def test_get_tdengine_returns_client(self) -> None:
        client = tdengine_client.get_tdengine()
        assert client is not None

    def test_get_tdengine_is_singleton(self) -> None:
        c1 = tdengine_client.get_tdengine()
        c2 = tdengine_client.get_tdengine()
        assert c1 is c2

    @pytest.mark.asyncio
    async def test_get_tdengine_async_returns_client(self) -> None:
        client = await tdengine_client.get_tdengine_async()
        assert client is not None

    @pytest.mark.asyncio
    async def test_health_check_returns_healthy(self) -> None:
        result = await tdengine_client.check_tdengine_health()
        assert result["status"] == "healthy"


class TestDatabaseManagement:
    @pytest.fixture(autouse=True)
    def _require_service(self, tdengine_ready: None) -> None:
        return None

    @pytest.mark.asyncio
    async def test_ensure_database_idempotent(self, test_database: str) -> None:
        ok_first = await tdengine_client.ensure_database(test_database)
        ok_second = await tdengine_client.ensure_database(test_database)
        assert ok_first is True
        assert ok_second is True

    @pytest.mark.asyncio
    async def test_use_database(self, test_database: str) -> None:
        ok = await tdengine_client.use_database(test_database)
        assert ok is True


class TestInsertAndQuery:
    @pytest.fixture(autouse=True)
    def _require_service(self, tdengine_ready: None) -> None:
        return None

    @pytest.mark.asyncio
    async def test_insert_1000_rows_and_query(self, test_table: str, test_database: str) -> None:
        """主验收用例：插入 1000 条数据并按时间范围查询验证。"""
        now = datetime(2026, 1, 1, 0, 0, 0)
        rows: list[list[Any]] = []
        for i in range(1000):
            ts = now + timedelta(milliseconds=i * 10)  # 每条 +10ms
            rows.append(
                [
                    ts.strftime("%Y-%m-%d %H:%M:%S.") + f"{i % 1000:03d}000",
                    float(i) * 0.1,
                    i % 5,
                ]
            )

        affected = await tdengine_client.insert_rows(table_name=test_table, rows=rows, database=test_database)
        # TDengine 成功执行 INSERT 后返回受影响的行数（应 >= 实际行数）
        assert affected >= 1000, f"expected >=1000 affected rows, got {affected}"

        # 时间范围查询：拉取全部 1000 条
        start_ts = now - timedelta(seconds=1)
        end_ts = now + timedelta(seconds=60)
        fetched = await tdengine_client.query_time_range(
            table_name=test_table,
            start_ts=start_ts,
            end_ts=end_ts,
            columns="*",
            database=test_database,
            limit=2000,
        )
        assert len(fetched) == 1000, f"expected 1000 rows, got {len(fetched)}"

        # 验证行结构 [ts, value, machine_id]
        first = fetched[0]
        assert len(first) == 3
        # 第一行的 value 应为 0.0
        assert abs(float(first[1]) - 0.0) < 1e-9
        # 最后一行 value 应为 99.9
        last = fetched[-1]
        assert abs(float(last[1]) - 99.9) < 1e-9

    @pytest.mark.asyncio
    async def test_insert_empty_returns_zero(self, test_table: str, test_database: str) -> None:
        affected = await tdengine_client.insert_rows(table_name=test_table, rows=[], database=test_database)
        assert affected == 0

    @pytest.mark.asyncio
    async def test_time_range_filter(self, test_table: str, test_database: str) -> None:
        """验证时间范围查询的过滤效果。"""
        base = datetime(2026, 2, 1, 0, 0, 0)
        rows = [[base.strftime("%Y-%m-%d %H:%M:%S.") + f"{i:03d}000", float(i), 0] for i in range(100)]
        await tdengine_client.insert_rows(table_name=test_table, rows=rows, database=test_database)

        # 只查询前 10 条的范围
        start = base - timedelta(milliseconds=1)
        end = base + timedelta(milliseconds=10)  # 包含 ts=base+10ms
        result = await tdengine_client.query_time_range(
            table_name=test_table,
            start_ts=start,
            end_ts=end,
            database=test_database,
            limit=100,
        )
        # 起点 ts=base（含），终点 ts=base+10ms（含） 2 行（i=0 与 i=1）
        assert len(result) == 2, f"expected 2 rows, got {len(result)}"

    @pytest.mark.asyncio
    async def test_query_no_match(self, test_table: str, test_database: str) -> None:
        """查询不存在的远期时间范围应返回空列表。"""
        result = await tdengine_client.query_time_range(
            table_name=test_table,
            start_ts=datetime(2099, 1, 1),
            end_ts=datetime(2099, 1, 2),
            database=test_database,
        )
        assert result == []


class TestValueFormatting:
    """纯函数测试：值格式化逻辑无需 TDengine 连接。"""

    def test_format_none(self) -> None:
        assert tdengine_client._format_value(None) == "NULL"

    def test_format_bool(self) -> None:
        assert tdengine_client._format_value(True) == "1"
        assert tdengine_client._format_value(False) == "0"

    def test_format_int_float(self) -> None:
        assert tdengine_client._format_value(42) == "42"
        assert tdengine_client._format_value(3.14) == "3.14"

    def test_format_string_escapes_quotes(self) -> None:
        assert tdengine_client._format_value("hello") == "'hello'"
        assert tdengine_client._format_value("it's") == "'it''s'"

    def test_format_datetime(self) -> None:
        dt = datetime(2026, 5, 1, 12, 0, 0, 123456)
        formatted = tdengine_client._format_value(dt)
        assert formatted.startswith("'2026-05-01 12:00:00.")

    def test_format_bytes(self) -> None:
        assert tdengine_client._format_value(b"abc") == "'abc'"

    def test_format_unknown_falls_back_to_str(self) -> None:
        class _Custom:
            def __str__(self) -> str:
                return "x"

        assert tdengine_client._format_value(_Custom()) == "'x'"


class TestIdentifierValidation:
    """P2-3-3 修复：标识符白名单校验单元测试，防止 SQL 注入。

    纯函数测试：不依赖 TDengine 服务连接。
    """

    def test_valid_simple_names(self) -> None:
        """合法标识符：字母开头，后接字母/数字/下划线。"""
        for name in ("sensor_01", "A", "_test", "machine_id", "vibration_2024"):
            # 不抛出异常即通过
            tdengine_client._validate_identifier(name, "table")

    def test_valid_single_char(self) -> None:
        """单字符合法标识符。"""
        tdengine_client._validate_identifier("a", "database")
        tdengine_client._validate_identifier("Z", "table")
        tdengine_client._validate_identifier("_", "column")

    def test_valid_max_length_63(self) -> None:
        """长度上限 63（1 个首字符 + 62 个后续字符）。"""
        name = "a" + "b" * 62  # 总长 63
        assert len(name) == 63
        tdengine_client._validate_identifier(name, "table")

    def test_invalid_starts_with_digit(self) -> None:
        """非法：数字开头。"""
        with pytest.raises(ValueError, match="Invalid table identifier"):
            tdengine_client._validate_identifier("1abc", "table")

    def test_invalid_contains_space(self) -> None:
        """非法：包含空格。"""
        with pytest.raises(ValueError, match="Invalid database identifier"):
            tdengine_client._validate_identifier("a b c", "database")

    def test_invalid_contains_special_chars(self) -> None:
        """非法：包含 SQL 注入特殊字符。"""
        injection_attempts = [
            "'; DROP TABLE--",
            "sensor; DROP TABLE users--",
            "table_name'",
            'name" OR 1=1',
            "a;b",
            "a--b",
            "col/*comment*/",
            "a`b",
        ]
        for evil in injection_attempts:
            with pytest.raises(ValueError, match="Invalid .* identifier"):
                tdengine_client._validate_identifier(evil, "column")

    def test_invalid_too_long(self) -> None:
        """非法：长度超过 63。"""
        name = "a" * 64  # 64 字符，超长
        with pytest.raises(ValueError, match="Invalid table identifier"):
            tdengine_client._validate_identifier(name, "table")

    def test_invalid_empty_string(self) -> None:
        """非法：空字符串。"""
        with pytest.raises(ValueError, match="Invalid database identifier"):
            tdengine_client._validate_identifier("", "database")

    def test_invalid_non_string_type(self) -> None:
        """非法：非字符串类型（None/int/list）。"""
        for bad in (None, 123, ["sensor"], {"name": "x"}):
            with pytest.raises(ValueError, match="Invalid column identifier"):
                tdengine_client._validate_identifier(bad, "column")  # type: ignore[arg-type]

    def test_invalid_contains_hyphen(self) -> None:
        """非法：连字符（-）不在白名单内。"""
        with pytest.raises(ValueError, match="Invalid table identifier"):
            tdengine_client._validate_identifier("my-table", "table")

    def test_invalid_contains_dot(self) -> None:
        """非法：点号（用于 db.table 复合标识符需调用方拆分）。"""
        with pytest.raises(ValueError, match="Invalid table identifier"):
            tdengine_client._validate_identifier("db.table", "table")


class TestTimestampLiteralValidation:
    """P2-3-3 修复：时间字面量白名单校验单元测试，防止通过时间戳注入 SQL。

    纯函数测试：不依赖 TDengine 服务连接。
    """

    def test_valid_null(self) -> None:
        """合法：NULL 字面量。"""
        assert tdengine_client._validate_timestamp_literal("NULL") == "NULL"

    def test_valid_pure_digit_milliseconds(self) -> None:
        """合法：纯数字毫秒时间戳。"""
        for ts in ("0", "1700000000000", "1", "9999999999999"):
            assert tdengine_client._validate_timestamp_literal(ts) == ts

    def test_valid_quoted_full_datetime(self) -> None:
        """合法：单引号包裹的完整日期时间字符串。"""
        ts = "'2024-01-01 00:00:00.000000'"
        assert tdengine_client._validate_timestamp_literal(ts) == ts

    def test_valid_quoted_datetime_no_microseconds(self) -> None:
        """合法：单引号包裹的日期时间（无微秒）。"""
        ts = "'2024-01-01 00:00:00'"
        assert tdengine_client._validate_timestamp_literal(ts) == ts

    def test_valid_quoted_date_only(self) -> None:
        """合法：单引号包裹的纯日期。"""
        ts = "'2024-01-01'"
        assert tdengine_client._validate_timestamp_literal(ts) == ts

    def test_valid_quoted_datetime_with_t_separator(self) -> None:
        """合法：ISO 8601 风格的 T 分隔符。"""
        ts = "'2024-01-01T00:00:00'"
        assert tdengine_client._validate_timestamp_literal(ts) == ts

    def test_valid_quoted_datetime_with_partial_microseconds(self) -> None:
        """合法：微秒位数少于 6 位（.3 / .30 / .300 等均允许）。"""
        for ts in (
            "'2024-01-01 00:00:00.1'",
            "'2024-01-01 00:00:00.12'",
            "'2024-01-01 00:00:00.123'",
            "'2024-01-01 00:00:00.123456'",
        ):
            assert tdengine_client._validate_timestamp_literal(ts) == ts

    def test_invalid_unquoted_date_string(self) -> None:
        """非法：未加引号的日期字符串。"""
        with pytest.raises(ValueError, match="Invalid timestamp literal"):
            tdengine_client._validate_timestamp_literal("2024-01-01 00:00:00")

    def test_invalid_sql_injection_attempt(self) -> None:
        """非法：SQL 注入尝试。"""
        injection_attempts = [
            "'; DROP TABLE--",
            "' OR 1=1--",
            "'; DELETE FROM sensor WHERE '1'='1",
            "NULL; DROP TABLE x",
            "1700000000000; DROP TABLE",
        ]
        for evil in injection_attempts:
            with pytest.raises(ValueError, match="Invalid timestamp literal"):
                tdengine_client._validate_timestamp_literal(evil)

    def test_invalid_negative_number(self) -> None:
        """非法：负数（纯数字正则不允许负号）。"""
        with pytest.raises(ValueError, match="Invalid timestamp literal"):
            tdengine_client._validate_timestamp_literal("-1")

    def test_invalid_float_string(self) -> None:
        """非法：浮点数字符串（纯数字正则不允许小数点）。"""
        with pytest.raises(ValueError, match="Invalid timestamp literal"):
            tdengine_client._validate_timestamp_literal("1700000000000.5")

    def test_invalid_malformed_date(self) -> None:
        """非法：日期格式错误（正则只校验格式，不校验语义）。

        注意：``'2024-13-01 00:00:00'`` 虽然月份 13 语义非法，但正则 ``\\d{2}``
        仍匹配，因此会**通过**校验——语义校验由 TDengine 服务端完成。
        这里仅测试格式层面的拒绝。
        """
        for ts in (
            "'2024-1-1 00:00:00'",  # 月份/日 单位数（正则要求 2 位）
            "'24-01-01 00:00:00'",  # 年份仅 2 位（正则要求 4 位）
            "'2024/01/01 00:00:00'",  # 斜杠分隔符（正则要求连字符）
            "'2024-01-01 25:00:00'",  # 小时 25 同样匹配 \d{2}，但格式合法——此条用于对照
        ):
            if ts == "'2024-01-01 25:00:00'":
                # 对照组：格式合法（语义由 TDengine 校验）
                assert tdengine_client._validate_timestamp_literal(ts) == ts
            else:
                with pytest.raises(ValueError, match="Invalid timestamp literal"):
                    tdengine_client._validate_timestamp_literal(ts)

    def test_invalid_unmatched_quotes(self) -> None:
        """非法：引号不匹配。"""
        for ts in (
            "'2024-01-01 00:00:00",  # 缺右引号
            "2024-01-01 00:00:00'",  # 缺左引号
            "''2024-01-01''",  # 双引号
        ):
            with pytest.raises(ValueError, match="Invalid timestamp literal"):
                tdengine_client._validate_timestamp_literal(ts)

    def test_invalid_empty_string(self) -> None:
        """非法：空字符串。"""
        with pytest.raises(ValueError, match="Invalid timestamp literal"):
            tdengine_client._validate_timestamp_literal("")


# 集成测试执行时间 sanity check


class TestExecutionTime:
    def test_holder_singleton_no_extra_connect(self) -> None:
        """重复调用 ``get_tdengine`` 不会触发额外连接验证开销。"""
        t0 = time.perf_counter()
        for _ in range(100):
            tdengine_client.get_tdengine()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        # 100 次同步缓存命中应远低于 1 秒（本地 ~5ms 以内）
        assert elapsed_ms < 1000, f"singleton lookup too slow: {elapsed_ms:.1f}ms"
