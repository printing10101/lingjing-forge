"""限流器状态清理工具的回归测试.

对应根因（2026-09 工程体检）：``app/api/v1/auth.py`` 在同一 pytest 进程内
被执行两次时，slowapi ``Limiter`` 会向 ``_route_limits["app.api.v1.auth.register"]``
追加两条相同的 ``3/hour`` 条目，导致每个请求计数两次（3/hour 实际变为
1.5/hour），``test_auth_register`` 的 409/429 断言被提前触发的 429 击穿。
本文件锁定 ``tests/utils/rate_limiter_reset.py`` 的去重与清理行为。
"""

from __future__ import annotations

import pytest

from tests.utils.rate_limiter_reset import (
    _dedupe_limits_entry,
    reset_all_rate_limiters,
)

pytestmark = pytest.mark.unit


class TestDedupeLimitsEntry:
    def test_removes_identical_duplicates(self):
        lims = ["limitA", "limitA", "limitA"]

        class _L:
            def __init__(self, tag: str):
                self.tag = tag
                self.limit = f"{tag}/hour"
                self.scope = "/api/v1/auth/register"

        entries = [_L("x"), _L("x"), _L("y")]
        deduped = _dedupe_limits_entry(entries)
        assert [e.tag for e in deduped] == ["x", "y"]

    def test_keeps_distinct_scopes(self):
        class _L:
            def __init__(self, scope: str):
                self.limit = "3/hour"
                self.scope = scope

        entries = [_L("/a"), _L("/b")]
        assert len(_dedupe_limits_entry(entries)) == 2

    def test_empty_and_single(self):
        assert _dedupe_limits_entry([]) == []
        assert len(_dedupe_limits_entry(["only"])) == 1

    def test_tolerates_malformed_entries(self):
        """缺 limit/scope 属性的异常条目不应让清理抛错."""
        entries = [{"weird": True}, "raw-string"]
        assert len(_dedupe_limits_entry(entries)) == 2


class TestResetAllRateLimiters:
    def test_dedupes_double_registered_route_limit(self):
        """复现双次执行残留：同 key 两条相同 3/hour 条目 → 去重为一条."""
        from app.api.v1 import auth as auth_module  # 触发真实装饰器注册
        from app.middleware.rate_limiter import limiter

        key = "app.api.v1.auth.register"
        lims = limiter._route_limits.get(key, [])
        assert lims, "auth 路由装饰器应已注册限流条目"

        original_len = len(lims)
        # 模拟 auth 模块被二次执行：追加一条相同条目
        limiter._route_limits[key] = list(lims) + list(lims)
        try:
            reset_all_rate_limiters()
            assert len(limiter._route_limits[key]) == original_len
        finally:
            limiter._route_limits[key] = lims

    def test_clears_permission_checker_ip_counters(self):
        from app.auth.permissions import permission_checker

        permission_checker._rate_limiter["192.168.1.1"] = object()
        reset_all_rate_limiters()
        assert "192.168.1.1" not in permission_checker._rate_limiter

    def test_resets_slowapi_counters(self):
        """写入一条伪造计数后 reset，存储应清空."""
        from app.middleware.rate_limiter import limiter

        storage = limiter._storage
        fake_key = "LIMITER/testclient//api/v1/auth/register/3/1/hour"
        getattr(storage, "storage", None)
        if hasattr(storage, "storage"):
            storage.storage[fake_key] = {"k": 3}
            storage.storage[fake_key]["k"] = 3
        reset_all_rate_limiters()
        if hasattr(storage, "storage"):
            assert fake_key not in storage.storage
