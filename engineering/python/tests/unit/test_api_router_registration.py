"""api 路由注册单元测试（12 领域注册顺序回归护栏）。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from app.api.routers import register_all_domain_routers

pytestmark = pytest.mark.unit


DOMAIN_ORDER = [
    'system',
    'identity',
    'ai',
    'tasks',
    'governance',
    'manufacturing',
    'engineering',
    'dnc_mes',
    'templates',
    'workflows',
    'plugins',
    'adr_pipeline',
]


class TestRegisterAllDomainRouters:
    def test_registration_order(self, monkeypatch):
        import app.api.routers as routers
        calls = []

        def make_register(name):
            def register(app, **kw):
                calls.append(name)
            return register

        for name in DOMAIN_ORDER[:-1]:  # 前 11 个领域 register 返回 None
            monkeypatch.setattr(getattr(routers, name), 'register', make_register(name))

        # adr_pipeline.register 返回 flags dict
        monkeypatch.setattr(routers.adr_pipeline, 'register', lambda app: calls.append('adr_pipeline') or {'image_to_3d': True})

        app = FastAPI()
        flags = register_all_domain_routers(app)

        assert calls == DOMAIN_ORDER
        assert isinstance(flags, dict)

    def test_adr_pipeline_returns_flags(self, monkeypatch):
        import app.api.routers as routers
        for name in DOMAIN_ORDER[:-1]:
            monkeypatch.setattr(getattr(routers, name), 'register', lambda app, **kw: None)
        monkeypatch.setattr(routers.adr_pipeline, 'register', lambda app: {'image_to_3d': True, 'feature_extraction': False})

        app = FastAPI()
        flags = register_all_domain_routers(app)
        assert flags == {'image_to_3d': True, 'feature_extraction': False}


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
