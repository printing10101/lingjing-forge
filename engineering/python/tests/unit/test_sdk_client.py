"""lomo SDK 单元测试（p5-7）.

对应 docs/development/core-contracts-design.md 第 10 章阶段 5 验收标准：
    "外部 Python 脚本可调用 SDK 跑通完整工作流"
    "CLI 命令与 HTTP API 行为一致"

本测试文件**不依赖 FastAPI 启动**（避免本地 conftest.py 强制加载 fastapi 导致
ImportError），仅做纯 SDK 行为校验：通过 mock httpx.Client / AsyncClient，
验证 LomoClient / AsyncLomoClient 在各种响应场景下的行为正确性。

覆盖维度:
    1. LomoClient 配置管理（base_url / token / timeout / 环境变量回退）
    2. LomoClient.request() 成功路径（code=0 返回 data）
    3. LomoClient.request() 错误路径（code≠0 抛出对应异常子类）
    4. httpx 网络异常转换（TimeoutException / HTTPError）
    5. 三大资源类方法调用端点契约（Workflow/Dataset/Snapshot）
    6. 流式响应封装（StreamingJSONL / SSEEventStream）
    7. 异步变体（AsyncLomoClient）async 方法
    8. 懒加载资源访问器（client.workflows / datasets / snapshots）
    9. SDK 顶层导出（lomo.__init__）

CI 标记：@pytest.mark.unit（与 ci.yml `pytest -m unit` 对齐）。

断言策略:
    - mock httpx.Client.request / AsyncClient.request 返回伪造响应
    - 断言请求 URL / method / json / params 与端点契约一致
    - 断言响应信封 code=0 时返回 data 字段
    - 断言响应信封 code≠0 时抛出正确异常子类（_NUMERIC_CODE_TO_EXC 映射）
    - 断言 httpx.TimeoutException → LomoTimeoutError
    - 断言 httpx.HTTPError → LomoConnectionError
    - 断言流式响应按 SSE / JSONL 协议正确解析
"""

from __future__ import annotations

import json as _json
import os
import sys
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# 确保 python/ 目录在 sys.path 中（让 lomo 包可被导入）
_PYTHON_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _PYTHON_DIR not in sys.path:
    sys.path.insert(0, _PYTHON_DIR)

from lomo import (  # noqa: E402
    CONTRACTS_VERSION,
    AsyncLomoClient,
    AsyncSSEEventStream,
    AsyncStreamingJSONL,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    LomoClient,
    SSEEventStream,
    StreamingJSONL,
)
from lomo._async import AsyncDataset, AsyncSnapshot, AsyncWorkflow  # noqa: E402
from lomo.dataset import Dataset  # noqa: E402
from lomo.exceptions import (  # noqa: E402
    LomoAPIError,
    LomoAuthError,
    LomoConnectionError,
    LomoError,
    LomoInternalError,
    LomoNotFoundError,
    LomoServiceUnavailableError,
    LomoTimeoutError,
    LomoValidationError,
    _NUMERIC_CODE_TO_EXC,
    _raise_for_envelope,
)
from lomo.snapshot import Snapshot  # noqa: E402
from lomo.workflow import Workflow  # noqa: E402


# 工具函数：构造伪造的 httpx 响应


def _make_response(
    *,
    json_body: Optional[dict] = None,
    status_code: int = 200,
    headers: Optional[dict] = None,
    text: str = "",
) -> MagicMock:
    """构造一个伪造的 httpx.Response（同步）。

    默认返回 JSON 响应；若提供 text 则返回非 JSON（用于测试非 JSON 错误路径）。
    """
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = headers or {"content-type": "application/json"}
    if json_body is not None:
        resp.json.return_value = json_body
        resp.text = _json.dumps(json_body)
    else:
        # 非 JSON 响应：json() 抛 ValueError
        resp.json.side_effect = ValueError("not json")
        resp.text = text
    return resp


def _make_stream_response(
    *,
    lines: list[str],
    content_type: str = "text/event-stream",
    status_code: int = 200,
) -> MagicMock:
    """构造一个伪造的流式 httpx.Response（同步）。

    lines 作为 iter_lines() 的返回值。content_type 决定封装类型
    （text/event-stream → SSEEventStream，其他 → StreamingJSONL）。
    """
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = {"content-type": content_type}
    resp.iter_lines.return_value = iter(lines)
    return resp


def _make_async_response(
    *,
    json_body: Optional[dict] = None,
    status_code: int = 200,
    headers: Optional[dict] = None,
    text: str = "",
) -> MagicMock:
    """构造一个伪造的 httpx.Response（异步）。

    异步响应的 iter_lines / aiter_lines 方法需要返回 AsyncIterator，
    但 LomoClient 异步路径在 request() 中只调用 resp.json() / resp.text /
    resp.headers / resp.status_code；流式路径才调用 aiter_lines。
    因此此处仅 mock json/text/headers/status_code。
    """
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = headers or {"content-type": "application/json"}
    if json_body is not None:
        resp.json.return_value = json_body
        resp.text = _json.dumps(json_body)
    else:
        resp.json.side_effect = ValueError("not json")
        resp.text = text
    return resp


def _make_async_stream_response(
    *,
    lines: list[str],
    content_type: str = "text/event-stream",
    status_code: int = 200,
) -> MagicMock:
    """构造一个伪造的异步流式 httpx.Response。

    aiter_lines() 返回异步迭代器（通过 async generator 实现）。
    """
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = {"content-type": content_type}

    async def _aiter_lines():
        for line in lines:
            yield line

    resp.aiter_lines = _aiter_lines
    return resp


# 1. LomoClient 配置管理


@pytest.mark.unit
class TestLomoClientConfig:
    """LomoClient 配置与生命周期."""

    def test_default_base_url(self):
        """未提供 base_url 时使用 DEFAULT_BASE_URL."""
        with patch("lomo.client.httpx.Client"):
            client = LomoClient()
            assert client.base_url == DEFAULT_BASE_URL
            assert client.timeout == DEFAULT_TIMEOUT

    def test_custom_base_url_strips_trailing_slash(self):
        """base_url 尾部斜杠被去除（避免拼接出 //api/v1）."""
        with patch("lomo.client.httpx.Client"):
            client = LomoClient(base_url="http://example.com/")
            assert client.base_url == "http://example.com"

    def test_token_from_parameter(self):
        """token 优先取参数."""
        with patch("lomo.client.httpx.Client"):
            client = LomoClient(token="abc123")
            assert client.token == "abc123"

    def test_token_from_env(self, monkeypatch):
        """未传 token 时读取 LOMO_TOKEN 环境变量."""
        monkeypatch.setenv("LOMO_TOKEN", "env_token_xyz")
        monkeypatch.delenv("LOMO_BASE_URL", raising=False)
        with patch("lomo.client.httpx.Client"):
            client = LomoClient()
            assert client.token == "env_token_xyz"

    def test_base_url_from_env(self, monkeypatch):
        """未传 base_url 时读取 LOMO_BASE_URL 环境变量."""
        monkeypatch.setenv("LOMO_BASE_URL", "http://env-host:9000")
        with patch("lomo.client.httpx.Client"):
            client = LomoClient()
            assert client.base_url == "http://env-host:9000"

    def test_token_priority_parameter_over_env(self, monkeypatch):
        """参数 token 优先于环境变量."""
        monkeypatch.setenv("LOMO_TOKEN", "env_token")
        with patch("lomo.client.httpx.Client"):
            client = LomoClient(token="param_token")
            assert client.token == "param_token"

    def test_build_url_relative_path(self):
        """相对路径自动追加 /api/v1 前缀."""
        with patch("lomo.client.httpx.Client"):
            client = LomoClient(base_url="http://host")
            assert client._build_url("/datasets") == "http://host/api/v1/datasets"

    def test_build_url_without_leading_slash(self):
        """无前导斜杠的路径会被补上."""
        with patch("lomo.client.httpx.Client"):
            client = LomoClient(base_url="http://host")
            assert client._build_url("datasets") == "http://host/api/v1/datasets"

    def test_build_url_absolute_http(self):
        """http:// 开头视为绝对 URL，直接返回."""
        with patch("lomo.client.httpx.Client"):
            client = LomoClient(base_url="http://host")
            assert client._build_url("http://other-host/x") == "http://other-host/x"

    def test_build_url_absolute_https(self):
        """https:// 开头视为绝对 URL."""
        with patch("lomo.client.httpx.Client"):
            client = LomoClient(base_url="http://host")
            assert client._build_url("https://secure.example.com/y") == "https://secure.example.com/y"

    def test_headers_without_token(self):
        """无 token 时 headers 不含 Authorization."""
        with patch("lomo.client.httpx.Client"):
            client = LomoClient()
            headers = client._headers()
            assert headers["Accept"] == "application/json"
            assert headers["Content-Type"] == "application/json"
            assert "Authorization" not in headers

    def test_headers_with_token(self):
        """有 token 时 headers 含 Bearer."""
        with patch("lomo.client.httpx.Client"):
            client = LomoClient(token="t123")
            headers = client._headers()
            assert headers["Authorization"] == "Bearer t123"

    def test_headers_stream_mode_omits_content_type(self):
        """stream=True 时不设置 Content-Type（GET 流式请求无 body）."""
        with patch("lomo.client.httpx.Client"):
            client = LomoClient(token="t")
            headers = client._headers(stream=True)
            assert "Content-Type" not in headers
            assert headers["Accept"] == "application/json"
            assert "Authorization" in headers

    def test_context_manager_closes_client(self):
        """with 语句退出时调用 close()."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_instance = mock_cls.return_value
            with LomoClient() as client:
                assert client is not None
            mock_instance.close.assert_called_once()

    def test_close_releases_underlying_client(self):
        """close() 调用 httpx.Client.close()."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_instance = mock_cls.return_value
            client = LomoClient()
            client.close()
            mock_instance.close.assert_called_once()


# 2. LomoClient.request() 成功路径


@pytest.mark.unit
class TestLomoClientRequestSuccess:
    """LomoClient.request() 成功路径（code=0 返回 data）."""

    def test_get_returns_data_field(self):
        """code=0 时返回响应信封的 data 字段."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.return_value = _make_response(
                json_body={
                    "code": 0,
                    "message": "OK",
                    "data": {"dataset_id": "ds-1"},
                    "request_id": "req-1",
                }
            )
            client = LomoClient(base_url="http://h")
            data = client.get("/datasets/ds-1")
            assert data == {"dataset_id": "ds-1"}

    def test_post_returns_data_field(self):
        """POST 成功时返回 data."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.return_value = _make_response(
                json_body={
                    "code": 0,
                    "message": "Success",
                    "data": {"workflow_run_id": "wf-1"},
                    "request_id": "req-2",
                }
            )
            client = LomoClient(base_url="http://h")
            data = client.post("/workflows/run", json={"spec": {}})
            assert data == {"workflow_run_id": "wf-1"}

    def test_request_passes_method_and_url(self):
        """request() 将 method/url 正确传递给 httpx."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.return_value = _make_response(
                json_body={"code": 0, "message": "OK", "data": None, "request_id": "r"}
            )
            client = LomoClient(base_url="http://h")
            client.request("GET", "/datasets")
            args, kwargs = mock_client.request.call_args
            assert args[0] == "GET"
            assert args[1] == "http://h/api/v1/datasets"

    def test_request_passes_json_and_params(self):
        """request() 将 json/params 透传给 httpx."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.return_value = _make_response(
                json_body={"code": 0, "message": "OK", "data": None, "request_id": "r"}
            )
            client = LomoClient(base_url="http://h")
            client.request(
                "POST",
                "/datasets",
                json={"name": "ds"},
                params={"k": "v"},
            )
            _, kwargs = mock_client.request.call_args
            assert kwargs["json"] == {"name": "ds"}
            assert kwargs["params"] == {"k": "v"}

    def test_request_with_none_data(self):
        """data 为 None 时也合法返回 None."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.return_value = _make_response(
                json_body={
                    "code": 0,
                    "message": "OK",
                    "data": None,
                    "request_id": "r",
                }
            )
            client = LomoClient(base_url="http://h")
            assert client.get("/x") is None

    def test_request_with_complex_data(self):
        """data 为嵌套结构时正确返回."""
        nested = {"a": {"b": [1, 2, {"c": "d"}]}}
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.return_value = _make_response(
                json_body={
                    "code": 0,
                    "message": "OK",
                    "data": nested,
                    "request_id": "r",
                }
            )
            client = LomoClient(base_url="http://h")
            assert client.get("/x") == nested

    def test_delete_method(self):
        """delete() 便捷方法."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.return_value = _make_response(
                json_body={"code": 0, "message": "OK", "data": {"deleted": True}, "request_id": "r"}
            )
            client = LomoClient(base_url="http://h")
            data = client.delete("/workflows/wf-1")
            assert data == {"deleted": True}
            args, _ = mock_client.request.call_args
            assert args[0] == "DELETE"

    def test_put_method(self):
        """put() 便捷方法."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.return_value = _make_response(
                json_body={"code": 0, "message": "OK", "data": {"ok": True}, "request_id": "r"}
            )
            client = LomoClient(base_url="http://h")
            data = client.put("/x", json={"k": "v"})
            assert data == {"ok": True}
            args, _ = mock_client.request.call_args
            assert args[0] == "PUT"


# 3. LomoClient.request() 错误路径


@pytest.mark.unit
class TestLomoClientRequestErrors:
    """LomoClient.request() 错误路径（code≠0 抛对应异常子类）."""

    @pytest.mark.parametrize(
        "code,exc_cls",
        [
            (1001, LomoNotFoundError),
            (1002, LomoValidationError),
            (1003, LomoAuthError),
            (1008, LomoNotFoundError),
            (2001, LomoInternalError),
            (2002, LomoServiceUnavailableError),
        ],
    )
    def test_error_code_maps_to_exception(self, code, exc_cls):
        """每个数值码映射到对应异常子类（与 _NUMERIC_CODE_TO_EXC 一致）."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.return_value = _make_response(
                json_body={
                    "code": code,
                    "message": f"err {code}",
                    "request_id": "req-x",
                }
            )
            client = LomoClient(base_url="http://h")
            with pytest.raises(exc_cls) as exc_info:
                client.get("/x")
            assert exc_info.value.code == code
            assert exc_info.value.request_id == "req-x"

    def test_unknown_code_falls_back_to_lomo_api_error(self):
        """未映射的 code 抛基类 LomoAPIError."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.return_value = _make_response(
                json_body={
                    "code": 9999,
                    "message": "unknown",
                    "request_id": "r",
                }
            )
            client = LomoClient(base_url="http://h")
            with pytest.raises(LomoAPIError) as exc_info:
                client.get("/x")
            # 不应是任何子类
            assert type(exc_info.value) is LomoAPIError
            assert exc_info.value.code == 9999

    def test_cad_generation_error_code_7001(self):
        """CAD 生成错误 code=7001 抛 LomoAPIError（非子类）."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.return_value = _make_response(
                json_body={
                    "code": 7001,
                    "message": "CAD generation failed",
                    "detail": "step export failed",
                    "suggestion": "check OCC version",
                    "recoverable": True,
                    "request_id": "r",
                }
            )
            client = LomoClient(base_url="http://h")
            with pytest.raises(LomoAPIError) as exc_info:
                client.get("/x")
            assert exc_info.value.code == 7001
            assert exc_info.value.detail == "step export failed"
            assert exc_info.value.suggestion == "check OCC version"
            assert exc_info.value.recoverable is True

    def test_error_carries_detail_suggestion_recoverable(self):
        """错误响应的 detail/suggestion/recoverable 透传到异常属性."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.return_value = _make_response(
                json_body={
                    "code": 1002,
                    "message": "invalid",
                    "detail": "field x is required",
                    "suggestion": "add field x",
                    "recoverable": True,
                    "request_id": "r",
                }
            )
            client = LomoClient(base_url="http://h")
            with pytest.raises(LomoValidationError) as exc_info:
                client.get("/x")
            assert exc_info.value.detail == "field x is required"
            assert exc_info.value.suggestion == "add field x"
            assert exc_info.value.recoverable is True

    def test_error_without_optional_fields(self):
        """缺省 detail/suggestion 时异常属性为默认值."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.return_value = _make_response(
                json_body={
                    "code": 1001,
                    "message": "not found",
                    "request_id": "r",
                }
            )
            client = LomoClient(base_url="http://h")
            with pytest.raises(LomoNotFoundError) as exc_info:
                client.get("/x")
            assert exc_info.value.detail is None
            assert exc_info.value.suggestion is None
            assert exc_info.value.recoverable is False

    def test_non_json_response_raises_connection_error(self):
        """响应非 JSON 时抛 LomoConnectionError."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.return_value = _make_response(text="<html>502 Bad Gateway</html>")
            client = LomoClient(base_url="http://h")
            with pytest.raises(LomoConnectionError):
                client.get("/x")


# 4. httpx 网络异常转换


@pytest.mark.unit
class TestLomoClientNetworkErrors:
    """httpx 网络异常转换为 Lomo 异常."""

    def test_timeout_exception_becomes_lomo_timeout(self):
        """httpx.TimeoutException → LomoTimeoutError."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.side_effect = httpx.TimeoutException("timeout")
            client = LomoClient(base_url="http://h")
            with pytest.raises(LomoTimeoutError):
                client.get("/x")

    def test_http_error_becomes_connection_error(self):
        """httpx.HTTPError → LomoConnectionError."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.side_effect = httpx.HTTPError("conn refused")
            client = LomoClient(base_url="http://h")
            with pytest.raises(LomoConnectionError):
                client.get("/x")

    def test_connect_error_becomes_connection_error(self):
        """httpx.ConnectError（HTTPError 子类）→ LomoConnectionError."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.side_effect = httpx.ConnectError("refused")
            client = LomoClient(base_url="http://h")
            with pytest.raises(LomoConnectionError):
                client.get("/x")

    def test_timeout_priority_over_http_error(self):
        """TimeoutException 优先于 HTTPError 捕获（异常层次中 TimeoutException
        继承自 HTTPError，但代码先 catch TimeoutException）."""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.request.side_effect = httpx.TimeoutException("t")
            client = LomoClient(base_url="http://h")
            with pytest.raises(LomoTimeoutError) as exc_info:
                client.get("/x")
            # 不应是 LomoConnectionError
            assert not isinstance(exc_info.value, LomoConnectionError)

    def test_all_lomo_errors_are_lomo_error_subclass(self):
        """所有 Lomo 异常均为 LomoError 子类（消费者可统一捕获 LomoError）."""
        for exc_cls in (
            LomoAPIError,
            LomoConnectionError,
            LomoTimeoutError,
            LomoNotFoundError,
            LomoValidationError,
            LomoAuthError,
            LomoInternalError,
            LomoServiceUnavailableError,
        ):
            assert issubclass(exc_cls, LomoError)


# 5. Workflow 资源类


@pytest.mark.unit
class TestWorkflowResource:
    """Workflow 8 个方法的端点契约."""

    def _make_client_with_mock(self) -> tuple[LomoClient, MagicMock]:
        """构造一个 LomoClient，其底层 httpx.Client 被 mock.
        返回 (client, mock_httpx_client)。"""
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_http = mock_cls.return_value
            client = LomoClient(base_url="http://h")
        # 退出 with 后 patch 已生效，但 client._client 仍指向 mock 实例
        # 因为 LomoClient.__init__ 在 patch 期间调用 httpx.Client()
        return client, mock_http

    def test_validate_endpoint(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {"valid": True, "node_count": 2, "edge_count": 1},
                "request_id": "r",
            }
        )
        client.workflows.validate(spec={"name": "wf"})
        args, kwargs = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://h/api/v1/workflows/validate"
        assert kwargs["json"] == {"spec": {"name": "wf"}}

    def test_run_returns_workflow_run_id(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {"workflow_run_id": "wf-abc", "status": "running"},
                "request_id": "r",
            }
        )
        run_id = client.workflows.run({"name": "wf"}, inputs={"k": "v"}, owner_id="alice")
        assert run_id == "wf-abc"
        args, kwargs = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://h/api/v1/workflows/run"
        assert kwargs["json"] == {
            "spec": {"name": "wf"},
            "inputs": {"k": "v"},
            "owner_id": "alice",
        }

    def test_run_without_optional_fields(self):
        """run() 不传 inputs/owner_id 时 body 仅含 spec."""
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {"workflow_run_id": "wf-x"},
                "request_id": "r",
            }
        )
        client.workflows.run({"name": "wf"})
        _, kwargs = mock_http.request.call_args
        assert kwargs["json"] == {"spec": {"name": "wf"}}

    def test_run_returns_empty_string_when_no_run_id(self):
        """响应 data 无 workflow_run_id 时返回空字符串."""
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {},
                "request_id": "r",
            }
        )
        assert client.workflows.run({"name": "wf"}) == ""

    def test_resume_endpoint(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {"workflow_run_id": "wf-1", "status": "running"},
                "request_id": "r",
            }
        )
        client.workflows.resume("wf-1", {"name": "wf"}, owner_id="alice")
        args, kwargs = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://h/api/v1/workflows/wf-1/resume"
        assert kwargs["json"] == {"spec": {"name": "wf"}, "owner_id": "alice"}

    def test_get_status_endpoint(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {"workflow_run_id": "wf-1", "status": "running"},
                "request_id": "r",
            }
        )
        client.workflows.get_status("wf-1")
        args, _ = mock_http.request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://h/api/v1/workflows/wf-1"

    def test_cancel_returns_bool_true(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {"cancelled": True},
                "request_id": "r",
            }
        )
        assert client.workflows.cancel("wf-1") is True
        args, _ = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://h/api/v1/workflows/wf-1/cancel"

    def test_cancel_returns_bool_with_ok_field(self):
        """响应只有 ok 字段时也能识别为 True."""
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {"ok": True},
                "request_id": "r",
            }
        )
        assert client.workflows.cancel("wf-1") is True

    def test_cancel_returns_false_on_falsy(self):
        """响应 cancelled=False 时返回 False."""
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {"cancelled": False},
                "request_id": "r",
            }
        )
        assert client.workflows.cancel("wf-1") is False

    def test_delete_endpoint(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {"deleted": True},
                "request_id": "r",
            }
        )
        client.workflows.delete("wf-1")
        args, _ = mock_http.request.call_args
        assert args[0] == "DELETE"
        assert args[1] == "http://h/api/v1/workflows/wf-1"

    def test_list_endpoint_with_filters(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {"workflows": [], "limit": 50, "offset": 0},
                "request_id": "r",
            }
        )
        client.workflows.list(limit=50, offset=10, owner_id="alice", status="running")
        args, kwargs = mock_http.request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://h/api/v1/workflows"
        assert kwargs["params"] == {
            "limit": 50,
            "offset": 10,
            "owner_id": "alice",
            "status": "running",
        }

    def test_list_default_params(self):
        """list() 默认 limit=100, offset=0."""
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={"code": 0, "message": "OK", "data": {"workflows": []}, "request_id": "r"}
        )
        client.workflows.list()
        _, kwargs = mock_http.request.call_args
        assert kwargs["params"] == {"limit": 100, "offset": 0}

    def test_subscribe_returns_sse_stream(self):
        """subscribe() 返回迭代器，底层调用 /stream 端点."""
        client, mock_http = self._make_client_with_mock()
        sse_lines = [
            "event: node_started",
            'data: {"node_id": "n1"}',
            "",
            "event: workflow_completed",
            'data: {"workflow_run_id": "wf-1"}',
            "",
        ]
        # stream 路径走 build_request + send(stream=True)
        mock_resp = _make_stream_response(lines=sse_lines, content_type="text/event-stream")
        mock_http.build_request.return_value = MagicMock()
        mock_http.send.return_value = mock_resp
        events = list(client.workflows.subscribe("wf-1"))
        assert len(events) == 2
        assert events[0]["event"] == "node_started"
        assert events[0]["data"]["node_id"] == "n1"
        assert events[1]["event"] == "workflow_completed"
        # 验证 stream=True
        _, kwargs_send = mock_http.send.call_args
        assert kwargs_send["stream"] is True


# 6. Dataset 资源类


@pytest.mark.unit
class TestDatasetResource:
    """Dataset 9 个方法的端点契约."""

    def _make_client_with_mock(self) -> tuple[LomoClient, MagicMock]:
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_http = mock_cls.return_value
            client = LomoClient(base_url="http://h")
        return client, mock_http

    def test_list_endpoint(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={"code": 0, "message": "OK", "data": {"items": []}, "request_id": "r"}
        )
        client.datasets.list(owner_id="alice", status="draft", limit=10, offset=5)
        args, kwargs = mock_http.request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://h/api/v1/datasets"
        assert kwargs["params"] == {
            "limit": 10,
            "offset": 5,
            "owner_id": "alice",
            "status": "draft",
        }

    def test_create_endpoint(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {"dataset_id": "ds-1", "status": "draft"},
                "request_id": "r",
            }
        )
        client.datasets.create(
            name="phm2010",
            schema={"fields": {}, "primary_key": ["id"]},
            owner_id="alice",
            description="PHM dataset",
        )
        args, kwargs = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://h/api/v1/datasets"
        assert kwargs["json"] == {
            "name": "phm2010",
            "schema": {"fields": {}, "primary_key": ["id"]},
            "owner_id": "alice",
            "description": "PHM dataset",
        }

    def test_get_endpoint(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={"code": 0, "message": "OK", "data": {"dataset_id": "ds-1"}, "request_id": "r"}
        )
        client.datasets.get("ds-1")
        args, _ = mock_http.request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://h/api/v1/datasets/ds-1"

    def test_list_versions_endpoint(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={"code": 0, "message": "OK", "data": {"versions": []}, "request_id": "r"}
        )
        client.datasets.list_versions("ds-1")
        args, _ = mock_http.request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://h/api/v1/datasets/ds-1/versions"

    def test_commit_version_with_records(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {"dataset_id": "ds-1", "version": "1.0.0", "row_count": 2},
                "request_id": "r",
            }
        )
        client.datasets.commit_version(
            "ds-1",
            records=[{"id": 1}, {"id": 2}],
            version="1.0.0",
            lineage={"target": "dataset://ds-1/1.0.0"},
        )
        args, kwargs = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://h/api/v1/datasets/ds-1/commit"
        assert kwargs["json"]["records"] == [{"id": 1}, {"id": 2}]
        assert kwargs["json"]["version"] == "1.0.0"
        assert kwargs["json"]["lineage"] == {"target": "dataset://ds-1/1.0.0"}

    def test_commit_version_without_records_uses_empty_list(self):
        """未传 records 时 body 用空列表（后端 lake 适配器自动加载）."""
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={"code": 0, "message": "OK", "data": {"version": "1.0.1"}, "request_id": "r"}
        )
        client.datasets.commit_version("ds-1")
        _, kwargs = mock_http.request.call_args
        assert kwargs["json"]["records"] == []

    def test_deprecate_endpoint(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={"code": 0, "message": "OK", "data": {"status": "deprecated"}, "request_id": "r"}
        )
        client.datasets.deprecate("ds-1", "1.0.0")
        args, kwargs = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://h/api/v1/datasets/ds-1/deprecate"
        assert kwargs["params"] == {"version": "1.0.0"}

    def test_read_returns_streaming_jsonl(self):
        """read() 返回迭代器，逐行解析 JSONL."""
        client, mock_http = self._make_client_with_mock()
        jsonl_lines = [
            '{"id": 1, "force": 12.3}',
            '{"id": 2, "force": 13.4}',
            "",
            '{"id": 3, "force": 14.5}',
        ]
        mock_resp = _make_stream_response(lines=jsonl_lines, content_type="application/x-ndjson")
        mock_http.build_request.return_value = MagicMock()
        mock_http.send.return_value = mock_resp
        rows = list(client.datasets.read("ds-1", version="1.0.0", batch_size=500))
        assert len(rows) == 3
        assert rows[0] == {"id": 1, "force": 12.3}
        assert rows[2] == {"id": 3, "force": 14.5}
        # 验证 stream 请求参数
        args_br, kwargs_br = mock_http.build_request.call_args
        assert args_br[0] == "GET"
        assert kwargs_br["params"] == {"batch_size": 500, "version": "1.0.0"}

    def test_read_raises_on_error_line(self):
        """JSONL 流中的 error 行抛 LomoAPIError."""
        client, mock_http = self._make_client_with_mock()
        jsonl_lines = [
            '{"id": 1}',
            '{"error": "decode_failed", "message": "bad row"}',
        ]
        mock_resp = _make_stream_response(lines=jsonl_lines, content_type="application/x-ndjson")
        mock_http.build_request.return_value = MagicMock()
        mock_http.send.return_value = mock_resp
        with pytest.raises(LomoAPIError) as exc_info:
            list(client.datasets.read("ds-1"))
        assert exc_info.value.code == 1002

    def test_record_lineage_endpoint(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {"record_id": "lin-1"},
                "request_id": "r",
            }
        )
        lineage = {
            "target": "dataset://ds-1/1.0.0",
            "source_type": "task",
            "source_ref": "task://preprocess/abc",
            "inputs": ["dataset://raw/1.0.0"],
            "outputs": ["dataset://ds-1/1.0.0"],
            "operation": "preprocess",
        }
        client.datasets.record_lineage(lineage)
        args, kwargs = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://h/api/v1/datasets/lineage"
        assert kwargs["json"] == lineage

    def test_get_lineage_endpoint(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {"target": "dataset://ds-1/1.0.0", "records": []},
                "request_id": "r",
            }
        )
        client.datasets.get_lineage("dataset://ds-1/1.0.0", direction="upstream", depth=5)
        args, kwargs = mock_http.request.call_args
        assert args[0] == "GET"
        # target_uri 作为 path 拼接
        assert args[1] == "http://h/api/v1/datasets/lineage/dataset://ds-1/1.0.0"
        assert kwargs["params"] == {"direction": "upstream", "depth": 5}

    def test_get_lineage_invalid_direction_raises(self):
        """direction 不合法时客户端直接抛 LomoValidationError."""
        client, _ = self._make_client_with_mock()
        with pytest.raises(LomoValidationError):
            client.datasets.get_lineage("x", direction="invalid")


# 7. Snapshot 资源类


@pytest.mark.unit
class TestSnapshotResource:
    """Snapshot 4 个方法的端点契约."""

    def _make_client_with_mock(self) -> tuple[LomoClient, MagicMock]:
        with patch("lomo.client.httpx.Client") as mock_cls:
            mock_http = mock_cls.return_value
            client = LomoClient(base_url="http://h")
        return client, mock_http

    def test_list_endpoint(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={"code": 0, "message": "OK", "data": {"items": []}, "request_id": "r"}
        )
        client.snapshots.list(created_by="alice", git_sha="abc", model_uri="model://m", detail=True)
        args, kwargs = mock_http.request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://h/api/v1/snapshots"
        assert kwargs["params"] == {
            "detail": True,
            "created_by": "alice",
            "git_sha": "abc",
            "model_uri": "model://m",
        }

    def test_list_default_params(self):
        """list() 默认只带 detail=False."""
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={"code": 0, "message": "OK", "data": {"items": []}, "request_id": "r"}
        )
        client.snapshots.list()
        _, kwargs = mock_http.request.call_args
        assert kwargs["params"] == {"detail": False}

    def test_create_endpoint(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {"snapshot_id": "snap-1", "git_sha": "abc"},
                "request_id": "r",
            }
        )
        client.snapshots.create(
            model_uri="model://ltc/1.0.0",
            created_by="alice",
            config={"hyperparams": {"lr": 0.001}},
            dataset_versions=["dataset://ds-1/1.0.0"],
            metrics={"mae": 0.1},
            notes="first",
        )
        args, kwargs = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://h/api/v1/snapshots"
        assert kwargs["json"] == {
            "model_uri": "model://ltc/1.0.0",
            "created_by": "alice",
            "config": {"hyperparams": {"lr": 0.001}},
            "dataset_versions": ["dataset://ds-1/1.0.0"],
            "metrics": {"mae": 0.1},
            "notes": "first",
        }

    def test_create_with_defaults(self):
        """create() 未传 config/dataset_versions/metrics 时使用空容器."""
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={"code": 0, "message": "OK", "data": {"snapshot_id": "s"}, "request_id": "r"}
        )
        client.snapshots.create(model_uri="model://m", created_by="bob")
        _, kwargs = mock_http.request.call_args
        assert kwargs["json"]["config"] == {}
        assert kwargs["json"]["dataset_versions"] == []
        assert kwargs["json"]["metrics"] == {}
        assert kwargs["json"]["notes"] == ""

    def test_get_endpoint(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={"code": 0, "message": "OK", "data": {"snapshot_id": "snap-1"}, "request_id": "r"}
        )
        client.snapshots.get("snap-1")
        args, _ = mock_http.request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://h/api/v1/snapshots/snap-1"

    def test_reproduce_returns_workflow_run_id(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={
                "code": 0,
                "message": "OK",
                "data": {"workflow_run_id": "wf-2", "snapshot_id": "snap-1"},
                "request_id": "r",
            }
        )
        run_id = client.snapshots.reproduce("snap-1")
        assert run_id == "wf-2"
        args, _ = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://h/api/v1/snapshots/snap-1/reproduce"

    def test_reproduce_returns_empty_string_on_missing_field(self):
        client, mock_http = self._make_client_with_mock()
        mock_http.request.return_value = _make_response(
            json_body={"code": 0, "message": "OK", "data": {}, "request_id": "r"}
        )
        assert client.snapshots.reproduce("snap-1") == ""


# 8. 流式响应封装


@pytest.mark.unit
class TestStreamingJSONL:
    """StreamingJSONL 解析行为."""

    def test_iter_json_yields_dicts(self):
        """iter_json() 跳过空行，逐行解析 JSON."""
        resp = MagicMock(spec=httpx.Response)
        resp.iter_lines.return_value = iter(
            [
                '{"a": 1}',
                "",
                '  {"b": 2}  ',
                '{"c": 3}',
            ]
        )
        stream = StreamingJSONL(resp)
        rows = list(stream.iter_json())
        assert rows == [{"a": 1}, {"b": 2}, {"c": 3}]

    def test_iter_json_raises_on_invalid_json(self):
        """非法 JSON 行抛 LomoConnectionError."""
        resp = MagicMock(spec=httpx.Response)
        resp.iter_lines.return_value = iter(["{not json"])
        stream = StreamingJSONL(resp)
        with pytest.raises(LomoConnectionError):
            list(stream.iter_json())

    def test_iter_json_raises_on_error_line(self):
        """含 error 字段的行抛 LomoAPIError."""
        resp = MagicMock(spec=httpx.Response)
        resp.iter_lines.return_value = iter(
            [
                '{"error": "x", "message": "bad"}',
            ]
        )
        stream = StreamingJSONL(resp)
        with pytest.raises(LomoAPIError) as exc_info:
            list(stream.iter_json())
        assert exc_info.value.code == 1002

    def test_iter_dunder_iter_calls_iter_json(self):
        """__iter__ 返回 iter_json() 迭代器."""
        resp = MagicMock(spec=httpx.Response)
        resp.iter_lines.return_value = iter(['{"x": 1}'])
        stream = StreamingJSONL(resp)
        rows = list(stream)
        assert rows == [{"x": 1}]

    def test_status_code_property(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        stream = StreamingJSONL(resp)
        assert stream.status_code == 200

    def test_iter_lines_passthrough(self):
        """iter_lines() 透传底层 httpx 的 iter_lines()."""
        resp = MagicMock(spec=httpx.Response)
        resp.iter_lines.return_value = iter(["a", "b"])
        stream = StreamingJSONL(resp)
        assert list(stream.iter_lines()) == ["a", "b"]


@pytest.mark.unit
class TestSSEEventStream:
    """SSEEventStream 解析行为."""

    def test_basic_event_parsing(self):
        """标准 SSE 事件（event: + data: + 空行）被正确解析."""
        resp = MagicMock(spec=httpx.Response)
        resp.iter_lines.return_value = iter(
            [
                "event: node_started",
                'data: {"node_id": "n1"}',
                "",
                "event: workflow_completed",
                'data: {"result": "ok"}',
                "",
            ]
        )
        stream = SSEEventStream(resp)
        events = list(stream.iter_events())
        assert len(events) == 2
        assert events[0]["event"] == "node_started"
        assert events[0]["data"] == {"node_id": "n1"}
        assert events[1]["event"] == "workflow_completed"
        assert events[1]["data"] == {"result": "ok"}

    def test_multi_line_data(self):
        """多行 data: 被合并为换行分隔的字符串再解析."""
        resp = MagicMock(spec=httpx.Response)
        resp.iter_lines.return_value = iter(
            [
                "event: progress",
                'data: {"p": 0.5',
                'data: , "n": 1}',
                "",
            ]
        )
        stream = SSEEventStream(resp)
        events = list(stream.iter_events())
        assert len(events) == 1
        # 多行 data 拼接后是合法 JSON
        assert events[0]["data"] == {"p": 0.5, "n": 1}

    def test_comment_lines_ignored(self):
        """以 : 开头的注释行被忽略."""
        resp = MagicMock(spec=httpx.Response)
        resp.iter_lines.return_value = iter(
            [
                ": this is a comment",
                ": another",
                "event: ping",
                "data: {}",
                "",
            ]
        )
        stream = SSEEventStream(resp)
        events = list(stream.iter_events())
        assert len(events) == 1
        assert events[0]["event"] == "ping"

    def test_default_event_type_is_message(self):
        """无 event: 行时 event 默认为 message."""
        resp = MagicMock(spec=httpx.Response)
        resp.iter_lines.return_value = iter(
            [
                'data: {"x": 1}',
                "",
            ]
        )
        stream = SSEEventStream(resp)
        events = list(stream.iter_events())
        assert events[0]["event"] == "message"
        assert events[0]["data"] == {"x": 1}

    def test_invalid_json_data_returns_raw(self):
        """data 非 JSON 时返回 {"raw": <原始字符串>}."""
        resp = MagicMock(spec=httpx.Response)
        resp.iter_lines.return_value = iter(
            [
                "event: log",
                "data: not json",
                "",
            ]
        )
        stream = SSEEventStream(resp)
        events = list(stream.iter_events())
        assert events[0]["event"] == "log"
        assert events[0]["data"] == {"raw": "not json"}

    def test_event_without_data_yields_empty_dict(self):
        """无 data 行的事件返回空 dict."""
        resp = MagicMock(spec=httpx.Response)
        resp.iter_lines.return_value = iter(
            [
                "event: heartbeat",
                "",
            ]
        )
        stream = SSEEventStream(resp)
        events = list(stream.iter_events())
        assert events[0]["event"] == "heartbeat"
        assert events[0]["data"] == {}

    def test_final_event_without_trailing_blank(self):
        """流结束时若仍有缓冲，刷出最后一个事件."""
        resp = MagicMock(spec=httpx.Response)
        resp.iter_lines.return_value = iter(
            [
                "event: done",
                'data: {"ok": true}',
                # 注意：没有结尾空行
            ]
        )
        stream = SSEEventStream(resp)
        events = list(stream.iter_events())
        assert len(events) == 1
        assert events[0]["event"] == "done"
        assert events[0]["data"] == {"ok": True}

    def test_iter_dunder_iter_calls_iter_events(self):
        resp = MagicMock(spec=httpx.Response)
        resp.iter_lines.return_value = iter(
            [
                "event: x",
                "data: {}",
                "",
            ]
        )
        stream = SSEEventStream(resp)
        events = list(stream)
        assert len(events) == 1


# 9. 异常映射函数


@pytest.mark.unit
class TestRaiseForEnvelope:
    """_raise_for_envelope() 函数行为."""

    def test_code_zero_does_not_raise(self):
        _raise_for_envelope({"code": 0, "message": "OK"})
        _raise_for_envelope({"code": 0, "message": "OK", "data": {"x": 1}})

    def test_missing_code_does_not_raise(self):
        """无 code 字段视为 0，不抛异常."""
        _raise_for_envelope({"message": "OK"})

    @pytest.mark.parametrize(
        "code,exc_cls",
        [
            (1001, LomoNotFoundError),
            (1002, LomoValidationError),
            (1003, LomoAuthError),
            (1008, LomoNotFoundError),
            (2001, LomoInternalError),
            (2002, LomoServiceUnavailableError),
        ],
    )
    def test_known_code_raises_subclass(self, code, exc_cls):
        with pytest.raises(exc_cls):
            _raise_for_envelope({"code": code, "message": "err"})

    def test_unknown_code_raises_base_api_error(self):
        with pytest.raises(LomoAPIError) as exc_info:
            _raise_for_envelope({"code": 9999, "message": "x"})
        assert type(exc_info.value) is LomoAPIError

    def test_message_extracted(self):
        with pytest.raises(LomoAPIError, match="custom message"):
            _raise_for_envelope({"code": 1002, "message": "custom message"})

    def test_request_id_extracted(self):
        with pytest.raises(LomoAPIError) as exc_info:
            _raise_for_envelope(
                {
                    "code": 1001,
                    "message": "x",
                    "request_id": "rid-123",
                }
            )
        assert exc_info.value.request_id == "rid-123"

    def test_numeric_code_mapping_table_complete(self):
        """_NUMERIC_CODE_TO_EXC 覆盖所有已定义数值码（与 response.py 一致）."""
        expected = {0, 1001, 1002, 1003, 1008, 2001, 2002, 7001}
        defined_in_sdk = set(_NUMERIC_CODE_TO_EXC.keys()) | {7001}
        # SDK 显式映射的码（1001/1002/1003/1008/2001/2002）
        assert set(_NUMERIC_CODE_TO_EXC.keys()) == {1001, 1002, 1003, 1008, 2001, 2002}
        # 7001 不在显式映射中，应回退到 LomoAPIError
        assert 7001 not in _NUMERIC_CODE_TO_EXC


# 10. 懒加载资源访问器


@pytest.mark.unit
class TestLazyResourceAccessors:
    """client.workflows / datasets / snapshots 懒加载行为."""

    def test_workflows_lazy_init(self):
        with patch("lomo.client.httpx.Client"):
            client = LomoClient()
            assert client._workflows is None
            wf = client.workflows
            assert isinstance(wf, Workflow)
            # 第二次访问不重新创建
            assert client.workflows is wf

    def test_datasets_lazy_init(self):
        with patch("lomo.client.httpx.Client"):
            client = LomoClient()
            ds = client.datasets
            assert isinstance(ds, Dataset)
            assert client.datasets is ds

    def test_snapshots_lazy_init(self):
        with patch("lomo.client.httpx.Client"):
            client = LomoClient()
            sn = client.snapshots
            assert isinstance(sn, Snapshot)
            assert client.snapshots is sn

    def test_three_accessors_independent(self):
        with patch("lomo.client.httpx.Client"):
            client = LomoClient()
            assert client.workflows is not client.datasets
            assert client.workflows is not client.snapshots
            assert client.datasets is not client.snapshots


# 11. SDK 顶层导出


@pytest.mark.unit
class TestSDKTopLevelExports:
    """lomo.__init__ 顶层导出."""

    def test_contracts_version_string(self):
        assert CONTRACTS_VERSION == "1.0.0"

    def test_version_string(self):
        import lomo

        assert lomo.__version__ == "1.0.0"

    def test_sync_client_exported(self):
        from lomo import LomoClient as _LC

        assert _LC is LomoClient

    def test_async_client_exported(self):
        from lomo import AsyncLomoClient as _ALC

        assert _ALC is AsyncLomoClient

    def test_streaming_classes_exported(self):
        assert StreamingJSONL is not None
        assert SSEEventStream is not None
        assert AsyncStreamingJSONL is not None
        assert AsyncSSEEventStream is not None

    def test_default_constants_exported(self):
        assert DEFAULT_BASE_URL == "http://127.0.0.1:8000"
        assert DEFAULT_TIMEOUT == 30.0

    def test_all_exceptions_exported(self):
        import lomo

        for name in (
            "LomoError",
            "LomoAPIError",
            "LomoConnectionError",
            "LomoTimeoutError",
            "LomoNotFoundError",
            "LomoValidationError",
            "LomoAuthError",
            "LomoInternalError",
            "LomoServiceUnavailableError",
        ):
            assert hasattr(lomo, name), f"lomo 缺少导出: {name}"

    def test_lazy_workflow_export(self):
        """from lomo import Workflow 通过 __getattr__ 懒导出."""
        from lomo import Workflow as _W

        assert _W is Workflow

    def test_lazy_dataset_export(self):
        from lomo import Dataset as _D

        assert _D is Dataset

    def test_lazy_snapshot_export(self):
        from lomo import Snapshot as _S

        assert _S is Snapshot

    def test_lazy_async_workflow_export(self):
        from lomo import AsyncWorkflow as _AW

        assert _AW is AsyncWorkflow

    def test_lazy_async_dataset_export(self):
        from lomo import AsyncDataset as _AD

        assert _AD is AsyncDataset

    def test_lazy_async_snapshot_export(self):
        from lomo import AsyncSnapshot as _AS

        assert _AS is AsyncSnapshot

    def test_unknown_attribute_raises(self):
        import lomo

        with pytest.raises(AttributeError):
            lomo.NonExistent  # noqa: B018

    def test_dir_returns_lazy_names(self):
        import lomo

        names = dir(lomo)
        for n in ("Workflow", "Dataset", "Snapshot", "AsyncWorkflow", "AsyncDataset", "AsyncSnapshot"):
            assert n in names


# 12. 异步客户端 —— AsyncLomoClient 配置与生命周期


@pytest.mark.unit
class TestAsyncLomoClientConfig:
    """AsyncLomoClient 配置与生命周期."""

    def test_default_config(self):
        with patch("lomo._async.httpx.AsyncClient"):
            client = AsyncLomoClient()
            assert client.base_url == DEFAULT_BASE_URL
            assert client.timeout == DEFAULT_TIMEOUT

    def test_custom_base_url(self):
        with patch("lomo._async.httpx.AsyncClient"):
            client = AsyncLomoClient(base_url="http://async-host/")
            assert client.base_url == "http://async-host"

    def test_token_from_parameter(self):
        with patch("lomo._async.httpx.AsyncClient"):
            client = AsyncLomoClient(token="abc")
            assert client.token == "abc"

    @pytest.mark.asyncio
    async def test_aclose_releases_underlying_client(self):
        """aclose() 调用 httpx.AsyncClient.aclose()."""
        with patch("lomo._async.httpx.AsyncClient") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.aclose = AsyncMock()
            client = AsyncLomoClient()
            await client.aclose()
            mock_instance.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        """async with 退出时调用 aclose()."""
        with patch("lomo._async.httpx.AsyncClient") as mock_cls:
            mock_instance = mock_cls.return_value
            mock_instance.aclose = AsyncMock()
            async with AsyncLomoClient() as client:
                assert client is not None
            mock_instance.aclose.assert_awaited_once()

    def test_workflows_lazy_init(self):
        with patch("lomo._async.httpx.AsyncClient"):
            client = AsyncLomoClient()
            wf = client.workflows
            assert isinstance(wf, AsyncWorkflow)
            assert client.workflows is wf

    def test_datasets_lazy_init(self):
        with patch("lomo._async.httpx.AsyncClient"):
            client = AsyncLomoClient()
            ds = client.datasets
            assert isinstance(ds, AsyncDataset)
            assert client.datasets is ds

    def test_snapshots_lazy_init(self):
        with patch("lomo._async.httpx.AsyncClient"):
            client = AsyncLomoClient()
            sn = client.snapshots
            assert isinstance(sn, AsyncSnapshot)
            assert client.snapshots is sn


# 13. AsyncLomoClient.request() 成功与错误路径


@pytest.mark.unit
class TestAsyncLomoClientRequest:
    """AsyncLomoClient.request() 行为."""

    @pytest.mark.asyncio
    async def test_get_returns_data(self):
        with patch("lomo._async.httpx.AsyncClient") as mock_cls:
            mock_http = mock_cls.return_value
            mock_http.request = AsyncMock(
                return_value=_make_async_response(
                    json_body={"code": 0, "message": "OK", "data": {"x": 1}, "request_id": "r"}
                )
            )
            client = AsyncLomoClient(base_url="http://h")
            data = await client.get("/x")
            assert data == {"x": 1}

    @pytest.mark.asyncio
    async def test_post_returns_data(self):
        with patch("lomo._async.httpx.AsyncClient") as mock_cls:
            mock_http = mock_cls.return_value
            mock_http.request = AsyncMock(
                return_value=_make_async_response(
                    json_body={"code": 0, "message": "OK", "data": {"y": 2}, "request_id": "r"}
                )
            )
            client = AsyncLomoClient(base_url="http://h")
            data = await client.post("/x", json={"k": "v"})
            assert data == {"y": 2}
            args, kwargs = mock_http.request.call_args
            assert args[0] == "POST"
            assert kwargs["json"] == {"k": "v"}

    @pytest.mark.asyncio
    async def test_error_code_raises_exception(self):
        with patch("lomo._async.httpx.AsyncClient") as mock_cls:
            mock_http = mock_cls.return_value
            mock_http.request = AsyncMock(
                return_value=_make_async_response(json_body={"code": 1001, "message": "not found", "request_id": "r"})
            )
            client = AsyncLomoClient(base_url="http://h")
            with pytest.raises(LomoNotFoundError):
                await client.get("/x")

    @pytest.mark.asyncio
    async def test_timeout_becomes_lomo_timeout(self):
        with patch("lomo._async.httpx.AsyncClient") as mock_cls:
            mock_http = mock_cls.return_value
            mock_http.request = AsyncMock(side_effect=httpx.TimeoutException("t"))
            client = AsyncLomoClient(base_url="http://h")
            with pytest.raises(LomoTimeoutError):
                await client.get("/x")

    @pytest.mark.asyncio
    async def test_http_error_becomes_connection_error(self):
        with patch("lomo._async.httpx.AsyncClient") as mock_cls:
            mock_http = mock_cls.return_value
            mock_http.request = AsyncMock(side_effect=httpx.HTTPError("conn"))
            client = AsyncLomoClient(base_url="http://h")
            with pytest.raises(LomoConnectionError):
                await client.get("/x")

    @pytest.mark.asyncio
    async def test_non_json_raises_connection_error(self):
        with patch("lomo._async.httpx.AsyncClient") as mock_cls:
            mock_http = mock_cls.return_value
            mock_http.request = AsyncMock(return_value=_make_async_response(text="<html>"))
            client = AsyncLomoClient(base_url="http://h")
            with pytest.raises(LomoConnectionError):
                await client.get("/x")

    @pytest.mark.asyncio
    async def test_put_method(self):
        with patch("lomo._async.httpx.AsyncClient") as mock_cls:
            mock_http = mock_cls.return_value
            mock_http.request = AsyncMock(
                return_value=_make_async_response(
                    json_body={"code": 0, "message": "OK", "data": {"ok": True}, "request_id": "r"}
                )
            )
            client = AsyncLomoClient(base_url="http://h")
            data = await client.put("/x", json={"k": "v"})
            assert data == {"ok": True}
            args, _ = mock_http.request.call_args
            assert args[0] == "PUT"

    @pytest.mark.asyncio
    async def test_delete_method(self):
        with patch("lomo._async.httpx.AsyncClient") as mock_cls:
            mock_http = mock_cls.return_value
            mock_http.request = AsyncMock(
                return_value=_make_async_response(
                    json_body={"code": 0, "message": "OK", "data": {"deleted": True}, "request_id": "r"}
                )
            )
            client = AsyncLomoClient(base_url="http://h")
            data = await client.delete("/x")
            assert data == {"deleted": True}
            args, _ = mock_http.request.call_args
            assert args[0] == "DELETE"


# 14. 异步资源类 —— AsyncWorkflow / AsyncDataset / AsyncSnapshot


def _make_async_client_with_mock() -> tuple[AsyncLomoClient, MagicMock]:
    """构造一个 AsyncLomoClient，底层 httpx.AsyncClient 被 mock."""
    with patch("lomo._async.httpx.AsyncClient") as mock_cls:
        mock_http = mock_cls.return_value
        client = AsyncLomoClient(base_url="http://h")
    return client, mock_http


@pytest.mark.unit
class TestAsyncWorkflowResource:
    """AsyncWorkflow 8 个方法的端点契约."""

    @pytest.mark.asyncio
    async def test_validate(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"valid": True}, "request_id": "r"}
            )
        )
        await client.workflows.validate({"name": "wf"})
        args, kwargs = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://h/api/v1/workflows/validate"
        assert kwargs["json"] == {"spec": {"name": "wf"}}

    @pytest.mark.asyncio
    async def test_run_returns_run_id(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"workflow_run_id": "wf-1"}, "request_id": "r"}
            )
        )
        run_id = await client.workflows.run({"name": "wf"}, owner_id="alice")
        assert run_id == "wf-1"
        args, kwargs = mock_http.request.call_args
        assert args[1] == "http://h/api/v1/workflows/run"
        assert kwargs["json"] == {"spec": {"name": "wf"}, "owner_id": "alice"}

    @pytest.mark.asyncio
    async def test_resume(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"workflow_run_id": "wf-1"}, "request_id": "r"}
            )
        )
        await client.workflows.resume("wf-1", {"name": "wf"})
        args, _ = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://h/api/v1/workflows/wf-1/resume"

    @pytest.mark.asyncio
    async def test_get_status(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"status": "running"}, "request_id": "r"}
            )
        )
        await client.workflows.get_status("wf-1")
        args, _ = mock_http.request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://h/api/v1/workflows/wf-1"

    @pytest.mark.asyncio
    async def test_cancel_returns_bool(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"cancelled": True}, "request_id": "r"}
            )
        )
        result = await client.workflows.cancel("wf-1")
        assert result is True

    @pytest.mark.asyncio
    async def test_delete(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"deleted": True}, "request_id": "r"}
            )
        )
        await client.workflows.delete("wf-1")
        args, _ = mock_http.request.call_args
        assert args[0] == "DELETE"

    @pytest.mark.asyncio
    async def test_list(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"workflows": []}, "request_id": "r"}
            )
        )
        await client.workflows.list(limit=10, offset=5, status="running")
        args, kwargs = mock_http.request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://h/api/v1/workflows"
        assert kwargs["params"] == {"limit": 10, "offset": 5, "status": "running"}

    @pytest.mark.asyncio
    async def test_subscribe_yields_events(self):
        """subscribe() 返回异步迭代器."""
        client, mock_http = _make_async_client_with_mock()
        sse_lines = [
            "event: node_started",
            'data: {"node_id": "n1"}',
            "",
            "event: workflow_completed",
            'data: {"result": "ok"}',
            "",
        ]
        mock_resp = _make_async_stream_response(lines=sse_lines, content_type="text/event-stream")
        mock_http.build_request.return_value = MagicMock()
        mock_http.send = AsyncMock(return_value=mock_resp)

        events = []
        async for ev in client.workflows.subscribe("wf-1"):
            events.append(ev)
        assert len(events) == 2
        assert events[0]["event"] == "node_started"
        assert events[1]["event"] == "workflow_completed"


@pytest.mark.unit
class TestAsyncDatasetResource:
    """AsyncDataset 9 个方法的端点契约."""

    @pytest.mark.asyncio
    async def test_list(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"items": []}, "request_id": "r"}
            )
        )
        await client.datasets.list(owner_id="alice", status="draft")
        args, kwargs = mock_http.request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://h/api/v1/datasets"
        assert kwargs["params"]["owner_id"] == "alice"
        assert kwargs["params"]["status"] == "draft"

    @pytest.mark.asyncio
    async def test_create(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"dataset_id": "ds-1"}, "request_id": "r"}
            )
        )
        await client.datasets.create(name="ds", schema={"fields": {}}, owner_id="alice")
        args, kwargs = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://h/api/v1/datasets"

    @pytest.mark.asyncio
    async def test_get(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"dataset_id": "ds-1"}, "request_id": "r"}
            )
        )
        await client.datasets.get("ds-1")
        args, _ = mock_http.request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://h/api/v1/datasets/ds-1"

    @pytest.mark.asyncio
    async def test_list_versions(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"versions": []}, "request_id": "r"}
            )
        )
        await client.datasets.list_versions("ds-1")
        args, _ = mock_http.request.call_args
        assert args[1] == "http://h/api/v1/datasets/ds-1/versions"

    @pytest.mark.asyncio
    async def test_commit_version(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"version": "1.0.0"}, "request_id": "r"}
            )
        )
        await client.datasets.commit_version("ds-1", records=[{"id": 1}], version="1.0.0")
        args, kwargs = mock_http.request.call_args
        assert args[1] == "http://h/api/v1/datasets/ds-1/commit"
        assert kwargs["json"]["records"] == [{"id": 1}]
        assert kwargs["json"]["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_deprecate(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"status": "deprecated"}, "request_id": "r"}
            )
        )
        await client.datasets.deprecate("ds-1", "1.0.0")
        args, kwargs = mock_http.request.call_args
        assert args[1] == "http://h/api/v1/datasets/ds-1/deprecate"
        assert kwargs["params"] == {"version": "1.0.0"}

    @pytest.mark.asyncio
    async def test_read_yields_rows(self):
        """read() 返回异步迭代器."""
        client, mock_http = _make_async_client_with_mock()
        jsonl_lines = [
            '{"id": 1}',
            '{"id": 2}',
            "",
        ]
        mock_resp = _make_async_stream_response(lines=jsonl_lines, content_type="application/x-ndjson")
        mock_http.build_request.return_value = MagicMock()
        mock_http.send = AsyncMock(return_value=mock_resp)

        rows = []
        async for row in client.datasets.read("ds-1", version="1.0.0"):
            rows.append(row)
        assert rows == [{"id": 1}, {"id": 2}]

    @pytest.mark.asyncio
    async def test_record_lineage(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"record_id": "lin-1"}, "request_id": "r"}
            )
        )
        await client.datasets.record_lineage({"target": "dataset://x/1.0.0"})
        args, kwargs = mock_http.request.call_args
        assert args[1] == "http://h/api/v1/datasets/lineage"
        assert kwargs["json"] == {"target": "dataset://x/1.0.0"}

    @pytest.mark.asyncio
    async def test_get_lineage(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"records": []}, "request_id": "r"}
            )
        )
        await client.datasets.get_lineage("dataset://x/1.0.0", direction="downstream", depth=3)
        args, kwargs = mock_http.request.call_args
        assert args[1] == "http://h/api/v1/datasets/lineage/dataset://x/1.0.0"
        assert kwargs["params"] == {"direction": "downstream", "depth": 3}

    @pytest.mark.asyncio
    async def test_get_lineage_invalid_direction_raises(self):
        client, _ = _make_async_client_with_mock()
        with pytest.raises(LomoValidationError):
            await client.datasets.get_lineage("x", direction="invalid")


@pytest.mark.unit
class TestAsyncSnapshotResource:
    """AsyncSnapshot 4 个方法的端点契约."""

    @pytest.mark.asyncio
    async def test_list(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"items": []}, "request_id": "r"}
            )
        )
        await client.snapshots.list(created_by="alice", detail=True)
        args, kwargs = mock_http.request.call_args
        assert args[1] == "http://h/api/v1/snapshots"
        assert kwargs["params"] == {"detail": True, "created_by": "alice"}

    @pytest.mark.asyncio
    async def test_create(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"snapshot_id": "snap-1"}, "request_id": "r"}
            )
        )
        await client.snapshots.create(
            model_uri="model://m",
            created_by="alice",
            config={"x": 1},
            metrics={"acc": 0.9},
        )
        args, kwargs = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://h/api/v1/snapshots"
        assert kwargs["json"]["model_uri"] == "model://m"
        assert kwargs["json"]["config"] == {"x": 1}
        assert kwargs["json"]["metrics"] == {"acc": 0.9}

    @pytest.mark.asyncio
    async def test_get(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"snapshot_id": "snap-1"}, "request_id": "r"}
            )
        )
        await client.snapshots.get("snap-1")
        args, _ = mock_http.request.call_args
        assert args[0] == "GET"
        assert args[1] == "http://h/api/v1/snapshots/snap-1"

    @pytest.mark.asyncio
    async def test_reproduce(self):
        client, mock_http = _make_async_client_with_mock()
        mock_http.request = AsyncMock(
            return_value=_make_async_response(
                json_body={"code": 0, "message": "OK", "data": {"workflow_run_id": "wf-2"}, "request_id": "r"}
            )
        )
        run_id = await client.snapshots.reproduce("snap-1")
        assert run_id == "wf-2"
        args, _ = mock_http.request.call_args
        assert args[0] == "POST"
        assert args[1] == "http://h/api/v1/snapshots/snap-1/reproduce"


# 15. 异步流式响应封装


@pytest.mark.asyncio
@pytest.mark.unit
async def test_async_streaming_jsonl_basic():
    """AsyncStreamingJSONL 解析 JSONL."""
    resp = MagicMock(spec=httpx.Response)

    async def _aiter():
        for line in ['{"a": 1}', "", '{"b": 2}']:
            yield line

    resp.aiter_lines = _aiter
    stream = AsyncStreamingJSONL(resp)
    rows = [row async for row in stream.iter_json()]
    assert rows == [{"a": 1}, {"b": 2}]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_async_streaming_jsonl_error_line():
    """AsyncStreamingJSONL 遇到 error 行抛 LomoAPIError."""
    resp = MagicMock(spec=httpx.Response)

    async def _aiter():
        yield '{"error": "x", "message": "bad"}'

    resp.aiter_lines = _aiter
    stream = AsyncStreamingJSONL(resp)
    with pytest.raises(LomoAPIError):
        async for _ in stream.iter_json():
            pass


@pytest.mark.asyncio
@pytest.mark.unit
async def test_async_sse_event_stream_basic():
    """AsyncSSEEventStream 解析 SSE 事件."""
    resp = MagicMock(spec=httpx.Response)

    async def _aiter():
        for line in [
            "event: node_started",
            'data: {"node_id": "n1"}',
            "",
            "event: workflow_completed",
            'data: {"ok": true}',
            "",
        ]:
            yield line

    resp.aiter_lines = _aiter
    stream = AsyncSSEEventStream(resp)
    events = [ev async for ev in stream.iter_events()]
    assert len(events) == 2
    assert events[0]["event"] == "node_started"
    assert events[1]["event"] == "workflow_completed"
    assert events[1]["data"] == {"ok": True}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_async_sse_event_stream_comment_ignored():
    """AsyncSSEEventStream 忽略注释行."""
    resp = MagicMock(spec=httpx.Response)

    async def _aiter():
        for line in [
            ": comment",
            "event: ping",
            "data: {}",
            "",
        ]:
            yield line

    resp.aiter_lines = _aiter
    stream = AsyncSSEEventStream(resp)
    events = [ev async for ev in stream.iter_events()]
    assert len(events) == 1
    assert events[0]["event"] == "ping"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "unit"])
