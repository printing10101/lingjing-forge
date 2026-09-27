"""Database connection pool management unit tests.

覆盖：
- DatabaseConfig 配置解析
- _DatabaseSingletons 线程安全单例
- get_engine/get_sessionmaker 公共 API
- 连接池参数和环境变量处理
- SQLite vs PostgreSQL 差异处理
- 健康检查和资源释放
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from app.database.connection import (
    DatabaseConfig,
    _DatabaseSingletons,
    get_engine,
    get_sessionmaker,
    get_session,
    check_db_health,
    close_db,
)

# 环境变量操纵统一使用 pytest monkeypatch（setenv/delenv 只操作单个 key），
# 避免 unittest.mock.patch.dict(os.environ) 的整份 copy/restore 行为——
# 当宿主环境存在超长环境变量（>32767 字符，如某些 CI/IDE 注入的配置）时，
# patch.dict 退出时 os.environ.update() 会抛 ValueError 并清空整个环境，
# 连锁导致后续测试的 Path.home() 抛 "Could not determine home directory"。
_DB_ENV_KEYS = (
    "DB_URL",
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "DB_POOL_TIMEOUT",
    "DB_POOL_RECYCLE",
    "DB_ECHO",
)


class TestDatabaseConfig:
    """Test database configuration parsing."""

    def test_default_config_from_env(self, monkeypatch):
        """Test config reads from environment variables."""
        values = {
            "DB_URL": "postgresql://user:pass@localhost/db",
            "DB_POOL_SIZE": "20",
            "DB_MAX_OVERFLOW": "15",
            "DB_POOL_TIMEOUT": "60",
            "DB_POOL_RECYCLE": "1800",
            "DB_ECHO": "true",
        }
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        config = DatabaseConfig()
        assert config.url == "postgresql://user:pass@localhost/db"
        assert config.pool_size == 20
        assert config.max_overflow == 15
        assert config.pool_timeout == 60
        assert config.pool_recycle == 1800
        assert config.echo is True

    def test_async_url_conversion_postgresql(self):
        """Test PostgreSQL URL is converted to asyncpg driver."""
        config = DatabaseConfig()
        config.url = "postgresql://user:pass@localhost/db"
        assert config.async_url == "postgresql+asyncpg://user:pass@localhost/db"

    def test_async_url_conversion_sqlite(self):
        """Test SQLite URL is converted to aiosqlite driver."""
        config = DatabaseConfig()
        config.url = "sqlite:///./test.db"
        assert config.async_url == "sqlite+aiosqlite:///./test.db"

    def test_async_url_already_async(self):
        """Test URL already using async driver is not double-converted."""
        config = DatabaseConfig()
        config.url = "postgresql+asyncpg://user:pass@localhost/db"
        assert config.async_url == "postgresql+asyncpg://user:pass@localhost/db"

    def test_enabled_when_url_present(self):
        """Test enabled property returns True when URL is set."""
        config = DatabaseConfig()
        config.url = "postgresql://localhost/db"
        assert config.enabled is True

    def test_disabled_when_url_empty(self):
        """Test enabled property returns False when URL is empty."""
        config = DatabaseConfig()
        config.url = ""
        assert config.enabled is False

    def test_default_values(self, monkeypatch):
        """Test default configuration values."""
        for key in _DB_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        config = DatabaseConfig()
        assert config.pool_size == 15
        assert config.max_overflow == 10
        assert config.pool_timeout == 30
        assert config.pool_recycle == 3600
        assert config.echo is False


class TestDatabaseSingletons:
    """Test thread-safe singleton holder."""

    def test_get_engine_creates_once(self, monkeypatch):
        """Test engine is created only once."""
        singletons = _DatabaseSingletons()

        monkeypatch.setenv("DB_URL", "sqlite:///./test.db")
        with patch('app.database.connection.create_async_engine') as mock_create:

            mock_engine = MagicMock()
            mock_create.return_value = mock_engine

            engine1 = singletons.get_engine()
            engine2 = singletons.get_engine()

            # Should only create once
            assert mock_create.call_count == 1
            assert engine1 is engine2

    def test_get_engine_returns_none_when_not_configured(self):
        """Test get_engine returns None when DB_URL is not set."""
        singletons = _DatabaseSingletons()

        # default_factory=_resolve_db_url 在类定义时绑定函数对象，
        # patch 模块属性不会影响已绑定的 default_factory，必须直接 mock DatabaseConfig。
        with patch('app.database.connection.DatabaseConfig') as MockConfig:
            MockConfig.return_value.enabled = False
            engine = singletons.get_engine()
            assert engine is None

    def test_get_sessionmaker_creates_once(self, monkeypatch):
        """Test sessionmaker is created only once."""
        singletons = _DatabaseSingletons()

        monkeypatch.setenv("DB_URL", "sqlite:///./test.db")
        with patch('app.database.connection.create_async_engine') as mock_create,              patch('app.database.connection.async_sessionmaker') as mock_sessionmaker:

            mock_engine = MagicMock()
            mock_create.return_value = mock_engine
            mock_sm = MagicMock()
            mock_sessionmaker.return_value = mock_sm

            sm1 = singletons.get_sessionmaker()
            sm2 = singletons.get_sessionmaker()

            assert mock_sessionmaker.call_count == 1
            assert sm1 is sm2

    def test_get_sessionmaker_returns_none_when_no_engine(self):
        """Test get_sessionmaker returns None when engine is not available."""
        singletons = _DatabaseSingletons()

        with patch('app.database.connection.DatabaseConfig') as MockConfig:
            MockConfig.return_value.enabled = False
            sm = singletons.get_sessionmaker()
            assert sm is None

    def test_thread_safety_concurrent_engine_creation(self, monkeypatch):
        """Test that concurrent engine creation is thread-safe."""
        singletons = _DatabaseSingletons()
        results = []
        errors = []

        # DB_URL 在启动线程前统一设置，避免线程内 patch.dict(os.environ) 的
        # 整份 copy/restore 竞态与超长环境变量问题。
        monkeypatch.setenv("DB_URL", "sqlite:///./test.db")

        def create_engine():
            try:
                with patch('app.database.connection.create_async_engine') as mock_create:
                    mock_engine = MagicMock()
                    mock_create.return_value = mock_engine
                    engine = singletons.get_engine()
                    results.append(engine)
            except Exception as e:
                errors.append(e)

        # Create multiple threads
        threads = [threading.Thread(target=create_engine) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No errors should occur
        assert len(errors) == 0
        # All threads should get the same engine instance
        assert len(set(id(r) for r in results)) == 1

    def test_close_disposes_engine(self, monkeypatch):
        """Test close method disposes engine and resets singletons."""
        singletons = _DatabaseSingletons()

        monkeypatch.setenv("DB_URL", "sqlite:///./test.db")
        with patch('app.database.connection.create_async_engine') as mock_create:

            mock_engine = MagicMock()
            mock_create.return_value = mock_engine

            # Create engine
            engine = singletons.get_engine()
            assert engine is not None

            # Close should dispose
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(singletons.close())
            finally:
                loop.close()

            mock_engine.dispose.assert_called_once()

            # After close, engine should be None
            assert singletons._engine is None
            assert singletons._sessionmaker is None

    def test_sqlite_uses_different_pool_config(self, monkeypatch):
        """Test SQLite uses different pool configuration than PostgreSQL."""
        singletons = _DatabaseSingletons()

        monkeypatch.setenv("DB_URL", "sqlite:///./test.db")
        with patch('app.database.connection.create_async_engine') as mock_create:

            mock_create.return_value = MagicMock()
            singletons.get_engine()

            # Check that pool_size/max_overflow are not passed for SQLite
            call_kwargs = mock_create.call_args[1]
            assert 'pool_size' not in call_kwargs
            assert 'max_overflow' not in call_kwargs
            assert call_kwargs.get('pool_pre_ping') is False

    def test_postgresql_uses_pool_config(self, monkeypatch):
        """Test PostgreSQL uses pool configuration parameters."""
        singletons = _DatabaseSingletons()

        monkeypatch.setenv("DB_URL", "postgresql://localhost/db")
        monkeypatch.setenv("DB_POOL_SIZE", "20")
        monkeypatch.setenv("DB_MAX_OVERFLOW", "10")
        with patch('app.database.connection.create_async_engine') as mock_create:

            mock_create.return_value = MagicMock()
            singletons.get_engine()

            call_kwargs = mock_create.call_args[1]
            assert call_kwargs['pool_size'] == 20
            assert call_kwargs['max_overflow'] == 10
            assert call_kwargs['pool_pre_ping'] is True


class TestPublicHelpers:
    """Test public helper functions."""

    def test_get_engine_delegates_to_singleton(self):
        """Test get_engine delegates to singleton holder."""
        with patch('app.database.connection._singletons') as mock_singletons:
            mock_singletons.get_engine.return_value = MagicMock()
            engine = get_engine()
            mock_singletons.get_engine.assert_called_once()

    def test_get_sessionmaker_delegates_to_singleton(self):
        """Test get_sessionmaker delegates to singleton holder."""
        with patch('app.database.connection._singletons') as mock_singletons:
            mock_singletons.get_sessionmaker.return_value = MagicMock()
            sm = get_sessionmaker()
            mock_singletons.get_sessionmaker.assert_called_once()

    def test_get_session_raises_when_not_configured(self):
        """Test get_session raises RuntimeError when DB is not configured."""
        with patch('app.database.connection.get_sessionmaker', return_value=None):
            with pytest.raises(RuntimeError, match="Database not configured"):
                import asyncio
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(get_session())
                finally:
                    loop.close()

    def test_get_session_returns_session(self):
        """Test get_session returns a new session."""
        mock_sm = MagicMock()
        mock_session = MagicMock()
        mock_sm.return_value = mock_session

        with patch('app.database.connection.get_sessionmaker', return_value=mock_sm):
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                session = loop.run_until_complete(get_session())
                assert session is mock_session
            finally:
                loop.close()


class TestDatabaseHealthCheck:
    """Test database health check functionality."""

    def test_health_check_returns_disabled_when_not_configured(self):
        """Test health check returns disabled status when DB is not configured."""
        with patch('app.database.connection.get_engine', return_value=None):
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(check_db_health())
                assert result["status"] == "disabled"
            finally:
                loop.close()

    def test_health_check_returns_healthy_on_success(self):
        """Test health check returns healthy status on successful connection."""
        from unittest.mock import AsyncMock

        mock_engine = MagicMock()
        mock_pool = MagicMock()
        mock_pool.size.return_value = 10
        mock_pool.checkedout.return_value = 2
        mock_engine.pool = mock_pool

        mock_conn = MagicMock()
        mock_conn.__aenter__.return_value = mock_conn
        mock_conn.__aexit__.return_value = None
        # 生产代码用 await conn.execute(...)，必须用 AsyncMock 返回 coroutine
        mock_conn.execute = AsyncMock()

        mock_engine.connect.return_value = mock_conn

        with patch('app.database.connection.get_engine', return_value=mock_engine):
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(check_db_health())
                assert result["status"] == "healthy"
                assert result["pool_size"] == 10
                assert result["checked_out"] == 2
            finally:
                loop.close()

    def test_health_check_returns_unhealthy_on_error(self):
        """Test health check returns unhealthy status on connection error."""
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = Exception("Connection failed")

        with patch('app.database.connection.get_engine', return_value=mock_engine):
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(check_db_health())
                assert result["status"] == "unhealthy"
                assert "error" in result
            finally:
                loop.close()


class TestCloseDatabase:
    """Test database close functionality."""

    def test_close_db_delegates_to_singleton(self):
        """Test close_db delegates to singleton close method."""
        from unittest.mock import AsyncMock

        with patch('app.database.connection._singletons') as mock_singletons:
            # close_db 用 await _singletons.close()，必须用 AsyncMock 返回 coroutine
            mock_singletons.close = AsyncMock()
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(close_db())
                mock_singletons.close.assert_called_once()
            finally:
                loop.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
