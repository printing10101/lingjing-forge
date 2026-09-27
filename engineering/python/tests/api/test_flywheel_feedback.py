"""反馈提交端点测试（自进化 M0：把 data_flywheel 反馈采集层接入业务调用方）。

覆盖：三种反馈类型路由、adoption/correction 校验、非法类型 400、
采集器未就绪 503、flush 立即落盘。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.plugins.plugin_manager import PluginRegistry

pytestmark = pytest.mark.api


class _FakeCollector:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.flush_count = 0

    async def record_annotation(self, **kw: Any) -> str:
        self.calls.append(("annotation", kw))
        return "fb-anno"

    async def record_adoption(self, **kw: Any) -> str:
        self.calls.append(("adoption", kw))
        return "fb-adoption"

    async def record_correction(self, **kw: Any) -> str:
        self.calls.append(("correction", kw))
        return "fb-correction"

    async def flush(self) -> Any:
        self.flush_count += 1
        return object()  # truthy version 占位

    @property
    def buffer_size(self) -> int:
        return len(self.calls)


class _FakePlugin:
    def __init__(self, collector: _FakeCollector) -> None:
        self._collector = collector

    def get_feedback_collector(self) -> _FakeCollector:
        return self._collector


@pytest.fixture()
def fake_collector(monkeypatch: pytest.MonkeyPatch) -> _FakeCollector:
    """把插件注册表中的 data_flywheel 实例替换为桩（app 启动后打补丁）。"""
    collector = _FakeCollector()
    registry = PluginRegistry.get_instance()
    original = registry.get_plugin_instance

    def _lookup(plugin_id: str) -> Any:
        if plugin_id == "data_flywheel":
            return _FakePlugin(collector)
        return original(plugin_id)

    monkeypatch.setattr(registry, "get_plugin_instance", _lookup)
    return collector


def _post(client, payload: dict[str, Any]):
    return client.post("/api/v1/flywheel/feedback", json=payload)


class TestFeedbackSubmission:
    def test_adoption_submitted(self, client, fake_collector):
        resp = _post(
            client,
            {
                "feedback_type": "adoption",
                "accepted": True,
                "prediction_id": "pipe-1",
                "metadata": {"prompt_version": 1},
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["feedback_id"] == "fb-adoption"
        assert body["feedback_type"] == "adoption"
        assert body["flushed"] is False
        kind, kw = fake_collector.calls[0]
        assert kind == "adoption"
        assert kw["accepted"] is True
        assert kw["prediction_id"] == "pipe-1"
        assert kw["user_id"] == "local"
        assert kw["metadata"] == {"prompt_version": 1}

    def test_annotation_submitted_with_flush(self, client, fake_collector):
        resp = _post(
            client,
            {
                "feedback_type": "annotation",
                "notes": "切削参数合理",
                "flush": True,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["flushed"] is True
        assert fake_collector.flush_count == 1

    def test_correction_submitted(self, client, fake_collector):
        original = {"gcode": "O1000"}
        corrected = {"gcode": "O1000\nM30"}
        resp = _post(
            client,
            {
                "feedback_type": "correction",
                "original_output": original,
                "corrected_output": corrected,
            },
        )
        assert resp.status_code == 200
        kind, kw = fake_collector.calls[0]
        assert kind == "correction"
        assert kw["original_output"] == original
        assert kw["corrected_output"] == corrected

    def test_adoption_requires_accepted_bool(self, client, fake_collector):
        resp = _post(client, {"feedback_type": "adoption"})
        assert resp.status_code == 400
        assert fake_collector.calls == []

    def test_correction_requires_both_outputs(self, client, fake_collector):
        resp = _post(
            client,
            {"feedback_type": "correction", "original_output": {"gcode": "x"}},
        )
        assert resp.status_code == 400
        assert fake_collector.calls == []

    def test_invalid_feedback_type_400(self, client, fake_collector):
        resp = _post(client, {"feedback_type": "praise"})
        assert resp.status_code == 400
        # 全局异常处理器把 HTTPException.detail 平铺进统一 message 字段
        assert "不合法" in resp.json()["message"]

    def test_collector_unavailable_503(self, client, monkeypatch):
        registry = PluginRegistry.get_instance()
        monkeypatch.setattr(registry, "get_plugin_instance", lambda pid: None)
        resp = _post(client, {"feedback_type": "adoption", "accepted": True})
        assert resp.status_code == 503
        # 5xx 经统一脱敏：仅断言状态码与统一错误码（2002=服务不可用）
        assert resp.json()["code"] == 2002
