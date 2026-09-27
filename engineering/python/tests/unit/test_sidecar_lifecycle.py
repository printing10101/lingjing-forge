"""Sidecar lifecycle management unit tests.

覆盖：
- IdleAutoShutdownMiddleware 空闲检测和自动关闭
- GracefulShutdownHandler 优雅关闭流程
- 资源清理和状态文件管理
- 信号处理和关闭触发
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.sidecar.sidecar_lifecycle import (
    IdleAutoShutdownMiddleware,
    GracefulShutdownHandler,
)


class TestIdleAutoShutdownMiddleware:
    """Test idle timeout and auto shutdown middleware."""

    @pytest.fixture
    def app(self):
        return FastAPI()

    @pytest.fixture
    def state_file(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"status": "running"}))
        return str(path)

    def test_middleware_initialization(self, app):
        """Test middleware can be initialized with default parameters."""
        middleware = IdleAutoShutdownMiddleware(app)
        assert middleware.idle_timeout == 1800
        assert middleware._check_interval == 60
        assert middleware._shutdown_initiated is False

    def test_middleware_custom_timeout(self, app):
        """Test middleware with custom idle timeout."""
        middleware = IdleAutoShutdownMiddleware(app, idle_timeout=300)
        assert middleware.idle_timeout == 300

    def test_dispatch_updates_last_activity(self, app):
        """Test that non-health requests update last activity time."""
        middleware = IdleAutoShutdownMiddleware(app, idle_timeout=60)
        
        # Simulate a request
        mock_request = MagicMock()
        mock_request.url.path = "/api/test"
        
        async def call_next(request):
            from starlette.responses import JSONResponse
            return JSONResponse(content={"status": "ok"})
        
        # Run dispatch
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(middleware.dispatch(mock_request, call_next))
            # Verify last_activity_time was updated
            assert middleware.last_activity_time > 0
        finally:
            loop.close()

    def test_health_check_does_not_update_activity(self, app):
        """Test that /health requests do not update last activity time."""
        middleware = IdleAutoShutdownMiddleware(app, idle_timeout=60)
        initial_time = middleware.last_activity_time
        
        mock_request = MagicMock()
        mock_request.url.path = "/health"
        
        async def call_next(request):
            from starlette.responses import JSONResponse
            return JSONResponse(content={"status": "healthy"})
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(middleware.dispatch(mock_request, call_next))
            # last_activity_time should not change for /health
            assert middleware.last_activity_time == initial_time
        finally:
            loop.close()

    def test_check_idle_and_shutdown_triggers_after_timeout(self, app, state_file):
        """Test that shutdown is triggered when idle timeout is exceeded."""
        middleware = IdleAutoShutdownMiddleware(
            app, idle_timeout=1, state_file_path=state_file
        )
        
        # Set last activity to past
        middleware.last_activity_time = time.time() - 2
        
        # Mock the shutdown methods to avoid actual process termination
        with patch.object(middleware, '_cleanup_resources') as mock_cleanup, \
             patch.object(middleware, '_remove_state_file') as mock_remove, \
             patch.object(middleware, '_trigger_shutdown') as mock_trigger:
            
            middleware.check_idle_and_shutdown()
            
            # Verify shutdown was initiated
            assert middleware._shutdown_initiated is True
            mock_cleanup.assert_called_once()
            mock_remove.assert_called_once()
            mock_trigger.assert_called_once()

    def test_check_idle_no_shutdown_before_timeout(self, app):
        """Test that shutdown is not triggered before timeout."""
        middleware = IdleAutoShutdownMiddleware(app, idle_timeout=60)
        
        # Last activity is recent
        middleware.last_activity_time = time.time()
        
        middleware.check_idle_and_shutdown()
        
        # Should not initiate shutdown
        assert middleware._shutdown_initiated is False

    def test_remove_state_file_deletes_file(self, app, state_file):
        """Test that state file is removed when shutdown is triggered."""
        middleware = IdleAutoShutdownMiddleware(
            app, state_file_path=state_file
        )
        
        assert Path(state_file).exists()
        middleware._remove_state_file()
        assert not Path(state_file).exists()

    def test_remove_state_file_handles_missing_file(self, app, tmp_path):
        """Test that missing state file is handled gracefully."""
        middleware = IdleAutoShutdownMiddleware(
            app, state_file_path=str(tmp_path / "nonexistent.json")
        )
        
        # Should not raise exception
        middleware._remove_state_file()

    def test_cleanup_resources_handles_import_error(self, app):
        """Test that resource cleanup handles import errors gracefully."""
        middleware = IdleAutoShutdownMiddleware(app)
        
        # Mock ModelCache to raise ImportError
        with patch.dict('sys.modules', {'app.ai.lnn.inference.model_cache': None}):
            # Should not raise exception
            middleware._cleanup_resources()

    def test_idle_check_loop_starts_background_task(self, app):
        """Test that idle checker background task is started."""
        middleware = IdleAutoShutdownMiddleware(app)
        
        async def start_and_check():
            await middleware.start_idle_checker()
            assert middleware._checker_task is not None
            # Cancel the task to avoid hanging
            middleware._checker_task.cancel()
            try:
                await middleware._checker_task
            except asyncio.CancelledError:
                pass
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(start_and_check())
        finally:
            loop.close()


class TestGracefulShutdownHandler:
    """Test graceful shutdown handler."""

    @pytest.fixture
    def state_file(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text(json.dumps({"status": "running", "updated_at": "2024-01-01T00:00:00"}))
        return str(path)

    def test_handler_initialization(self):
        """Test handler can be initialized."""
        handler = GracefulShutdownHandler()
        assert handler._shutting_down is False

    def test_setup_registers_signal_handlers(self):
        """Test that signal handlers are registered."""
        handler = GracefulShutdownHandler()
        
        with patch('signal.signal') as mock_signal:
            handler.setup()
            # Should register handlers for SIGTERM and SIGINT
            assert mock_signal.call_count >= 2

    def test_update_status_file(self, state_file):
        """Test that status file is updated with new status."""
        handler = GracefulShutdownHandler(state_file_path=state_file)
        
        handler._update_status_file("shutting_down")
        
        with open(state_file, 'r', encoding='utf-8') as f:
            state = json.load(f)
        
        assert state["status"] == "shutting_down"
        assert "updated_at" in state

    def test_update_status_file_handles_missing_file(self, tmp_path):
        """Test that missing state file is handled gracefully."""
        handler = GracefulShutdownHandler(
            state_file_path=str(tmp_path / "nonexistent.json")
        )
        
        # Should not raise exception
        handler._update_status_file("shutting_down")

    def test_remove_state_file(self, state_file):
        """Test that state file is removed."""
        handler = GracefulShutdownHandler(state_file_path=state_file)
        
        assert Path(state_file).exists()
        handler._remove_state_file()
        assert not Path(state_file).exists()

    def test_cleanup_all_resources_handles_errors(self):
        """Test that resource cleanup handles errors gracefully."""
        handler = GracefulShutdownHandler()
        
        # Mock ModelCache to raise exception
        with patch.dict('sys.modules', {'app.ai.lnn.inference.model_cache': None}):
            # Should not raise exception
            handler._cleanup_all_resources()

    def test_handle_shutdown_signal_initiates_shutdown(self, state_file):
        """Test that shutdown signal initiates graceful shutdown."""
        handler = GracefulShutdownHandler(state_file_path=state_file)
        
        # Mock os._exit and the graceful shutdown task to prevent actual exit
        with patch('os._exit') as mock_exit, \
             patch('asyncio.get_running_loop', side_effect=RuntimeError), \
             patch('asyncio.run') as mock_run:
            
            # Call handler directly (simulating signal)
            import signal
            handler._handle_shutdown_signal(signal.SIGTERM, None)
            
            # Should set shutting_down flag
            assert handler._shutting_down is True
            
            # Status file should be updated before task runs
            with open(state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
            assert state["status"] == "shutting_down"

    def test_handle_atexit_performs_cleanup(self):
        """Test that atexit handler performs cleanup."""
        handler = GracefulShutdownHandler()
        
        with patch.object(handler, '_cleanup_all_resources') as mock_cleanup:
            handler._handle_atexit()
            mock_cleanup.assert_called_once()

    def test_handle_atexit_skips_if_already_shutting_down(self):
        """Test that atexit handler skips cleanup if already shutting down."""
        handler = GracefulShutdownHandler()
        handler._shutting_down = True
        
        with patch.object(handler, '_cleanup_all_resources') as mock_cleanup:
            handler._handle_atexit()
            mock_cleanup.assert_not_called()


class TestShutdownIntegration:
    """Integration tests for shutdown scenarios."""

    def test_full_shutdown_sequence(self, tmp_path):
        """Test complete shutdown sequence from idle timeout to cleanup."""
        state_file = tmp_path / "state.json"
        state_file.write_text(json.dumps({"status": "running"}))
        
        app = FastAPI()
        middleware = IdleAutoShutdownMiddleware(
            app, idle_timeout=1, state_file_path=str(state_file)
        )
        
        # Simulate idle timeout
        middleware.last_activity_time = time.time() - 2
        
        with patch.object(middleware, '_trigger_shutdown') as mock_trigger:
            middleware.check_idle_and_shutdown()
            
            assert middleware._shutdown_initiated is True
            assert not state_file.exists()
            mock_trigger.assert_called_once()

    def test_concurrent_idle_checks(self):
        """Test that concurrent idle checks don't cause race conditions."""
        app = FastAPI()
        middleware = IdleAutoShutdownMiddleware(app, idle_timeout=1)
        middleware.last_activity_time = time.time() - 2
        
        # Multiple checks should be idempotent
        with patch.object(middleware, '_cleanup_resources'), \
             patch.object(middleware, '_remove_state_file'), \
             patch.object(middleware, '_trigger_shutdown'):
            
            middleware.check_idle_and_shutdown()
            first_initiated = middleware._shutdown_initiated
            
            middleware.check_idle_and_shutdown()
            second_initiated = middleware._shutdown_initiated
            
            # Both should be True, no exception raised
            assert first_initiated is True
            assert second_initiated is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
