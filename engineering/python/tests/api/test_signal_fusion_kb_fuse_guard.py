"""POST /api/v1/signal-fusion-kb/fuse attention 守卫的 API 层回归测试.

路由层在 ``safe_error_message`` 脱敏之前显式返回引导信息，
保证用户能看到"为什么 attention 被拒绝、该用什么替代"。
"""

from __future__ import annotations


def _fuse_payload(strategy: str) -> dict:
    return {
        "samples": [
            {"signal_type": "vibration", "source": "api_test", "features": [0.1] * 9},
            {"signal_type": "cutting_force", "source": "api_test", "features": [0.2] * 9},
        ],
        "strategy": strategy,
    }


class TestFuseAttentionGuard:
    def test_attention_rejected_by_default(self, client, monkeypatch):
        monkeypatch.delenv("LNN_SIGNAL_FUSION_ALLOW_UNTRAINED", raising=False)
        response = client.post("/api/v1/signal-fusion-kb/fuse", json=_fuse_payload("attention"))
        assert response.status_code == 200
        data = response.json()
        assert data["code"] != 0
        assert "未经训练" in data["message"]
        assert "weighted" in data["message"]

    def test_attention_allowed_with_env_override(self, client, monkeypatch):
        monkeypatch.setenv("LNN_SIGNAL_FUSION_ALLOW_UNTRAINED", "1")
        response = client.post("/api/v1/signal-fusion-kb/fuse", json=_fuse_payload("attention"))
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["strategy"] == "attention"

    def test_weighted_still_works(self, client, monkeypatch):
        monkeypatch.delenv("LNN_SIGNAL_FUSION_ALLOW_UNTRAINED", raising=False)
        response = client.post("/api/v1/signal-fusion-kb/fuse", json=_fuse_payload("weighted"))
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 0
        assert data["data"]["strategy"] == "weighted"
