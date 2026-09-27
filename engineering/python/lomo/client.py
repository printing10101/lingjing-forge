"""lomo SDK HTTP 客户端。

通过 :class:`LomoClient` 调用后端 ``/api/v1/`` REST 接口。客户端封装：

1. 配置管理（base_url / token / timeout，支持环境变量）
2. 请求头注入（Authorization / Content-Type / Accept）
3. 响应信封解析（``code == 0`` 返回 ``data``，非零抛 :class:`LomoAPIError` 子类）
4. 网络错误转换（httpx 异常 → :class:`LomoConnectionError` / :class:`LomoTimeoutError`）
5. 流式响应（SSE 事件流 + JSONL 数据集读取）

资源访问器（``client.workflows`` / ``client.datasets`` / ``client.snapshots``）
采用懒加载，避免与 :mod:`lomo.workflow` / :mod:`lomo.dataset` / :mod:`lomo.snapshot`
形成循环依赖。

环境变量:
    LOMO_BASE_URL: 后端服务地址，默认 ``http://127.0.0.1:8000``
    LOMO_TOKEN:    Bearer token，用于鉴权

示例::

    from lomo import LomoClient

    client = LomoClient()
    ds = client.datasets.create(
        name="phm2010",
        schema={"fields": {...}, "primary_key": ["sample_id"]},
        owner_id="alice",
    )
    print(ds["dataset_id"])
"""

from __future__ import annotations

import json as _json
import os
from typing import Any, Iterator, Optional

import httpx

from lomo.exceptions import (
    LomoConnectionError,
    LomoTimeoutError,
    _raise_for_envelope,
)

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 30.0
_API_PREFIX = "/api/v1"


# 流式响应封装


class StreamingJSONL:
    """JSONL 流式响应封装，用于 :meth:`Dataset.read` 等。

    迭代器产出每行反序列化后的 dict；遇到错误行（``{"error": ...}``）会抛
    :class:`LomoAPIError`。
    """

    def __init__(self, resp: httpx.Response):
        self._resp = resp

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return self.iter_json()

    def iter_lines(self) -> Iterator[str]:
        for line in self._resp.iter_lines():
            yield line

    def iter_json(self) -> Iterator[dict[str, Any]]:
        from lomo.exceptions import LomoAPIError

        for line in self._resp.iter_lines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = _json.loads(line)
            except _json.JSONDecodeError as e:
                raise LomoConnectionError(f"流式响应中存在非法 JSON 行: {e}; line={line[:200]}") from e
            if isinstance(obj, dict) and "error" in obj:
                # 后端在流中以 {"error": ..., "message": ...} 形式报告错误
                from lomo.exceptions import LomoAPIError

                raise LomoAPIError(
                    str(obj.get("message", "stream error")),
                    code=1002,
                    detail=obj.get("error"),
                )
            yield obj

    @property
    def status_code(self) -> int:
        return self._resp.status_code


class SSEEventStream:
    """SSE 事件流封装，用于 :meth:`Workflow.subscribe`。

    迭代器产出解析后的 dict 事件：``{"event": <type>, "data": <parsed json>}``。
    """

    def __init__(self, resp: httpx.Response):
        self._resp = resp

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return self.iter_events()

    def iter_events(self) -> Iterator[dict[str, Any]]:
        event_type: Optional[str] = None
        data_buf: list[str] = []
        for line in self._resp.iter_lines():
            if not line:
                # 空行 = 事件边界
                if event_type is not None or data_buf:
                    data_str = "\n".join(data_buf)
                    try:
                        data = _json.loads(data_str) if data_str else {}
                    except _json.JSONDecodeError:
                        data = {"raw": data_str}
                    yield {"event": event_type or "message", "data": data}
                    event_type = None
                    data_buf = []
                continue
            if line.startswith(":"):
                continue  # SSE 注释行
            if line.startswith("event:"):
                event_type = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_buf.append(line[len("data:") :].strip())
            else:
                # 未知前缀，按 data 处理
                data_buf.append(line)
        # 流结束时若仍有缓冲，刷出最后一个事件
        if event_type is not None or data_buf:
            data_str = "\n".join(data_buf)
            try:
                data = _json.loads(data_str) if data_str else {}
            except _json.JSONDecodeError:
                data = {"raw": data_str}
            yield {"event": event_type or "message", "data": data}


# 客户端基类（同步/异步共享配置）


class _BaseClient:
    """同步与异步客户端共享的配置与 URL 构造逻辑。"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        token: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        client: Any = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("LOMO_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.token = token or os.getenv("LOMO_TOKEN")
        self.timeout = float(timeout)
        # 三大资源访问器（懒加载）
        self._workflows = None
        self._datasets = None
        self._snapshots = None

    def _build_url(self, path: str) -> str:
        """将相对路径拼成完整 URL。

        - ``http://`` / ``https://`` 开头视为绝对 URL，直接返回
        - 其他路径自动追加 ``/api/v1`` 前缀
        """
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{_API_PREFIX}{path}"

    def _headers(self, *, stream: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if not stream:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


# 同步客户端


class LomoClient(_BaseClient):
    """同步 HTTP 客户端。

    使用 ``with`` 语句管理底层 httpx 连接池；不使用上下文管理器时，需
    手动调用 :meth:`close` 释放连接。

    参数:
        base_url: 后端服务地址。默认读取 ``LOMO_BASE_URL`` 环境变量，否则
            使用 ``http://127.0.0.1:8000``。
        token: Bearer token。默认读取 ``LOMO_TOKEN`` 环境变量。
        timeout: 单次请求超时秒数，默认 30s。
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        token: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(base_url, token=token, timeout=timeout)
        self._client = httpx.Client(timeout=self.timeout)

    # 生命周期

    def __enter__(self) -> "LomoClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """释放底层 httpx 连接池。"""
        self._client.close()

    # 资源访问器（懒加载，避免循环导入）

    @property
    def workflows(self):
        if self._workflows is None:
            from lomo.workflow import Workflow

            self._workflows = Workflow(self)
        return self._workflows

    @property
    def datasets(self):
        if self._datasets is None:
            from lomo.dataset import Dataset

            self._datasets = Dataset(self)
        return self._datasets

    @property
    def snapshots(self):
        if self._snapshots is None:
            from lomo.snapshot import Snapshot

            self._snapshots = Snapshot(self)
        return self._snapshots

    # HTTP 方法

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Optional[dict[str, Any]] = None,
        stream: bool = False,
    ) -> Any:
        """发送 HTTP 请求并解析响应信封。

        参数:
            method: HTTP 方法（GET/POST/PUT/DELETE）。
            path: 相对路径（如 ``/datasets``）或绝对 URL。
            json: 请求体（将被 httpx 序列化为 JSON）。
            params: 查询参数。
            stream: True 时返回流式响应对象（StreamingJSONL / SSEEventStream），
                不解析响应信封；False 时返回 ``data`` 字段。

        返回:
            stream=True 时返回 :class:`StreamingJSONL` 或 :class:`SSEEventStream`；
            否则返回响应信封中的 ``data`` 字段。

        抛出:
            LomoTimeoutError: 请求超时。
            LomoConnectionError: 网络错误或非 JSON 响应。
            LomoAPIError 及子类: 后端返回非零 ``code``。
        """
        url = self._build_url(path)
        headers = self._headers(stream=stream)
        try:
            if stream:
                req = self._client.build_request(method, url, json=json, params=params, headers=headers)
                resp = self._client.send(req, stream=True)
                # 检查 Content-Type 决定封装类型
                ctype = (resp.headers.get("content-type") or "").lower()
                if "text/event-stream" in ctype:
                    return SSEEventStream(resp)
                return StreamingJSONL(resp)
            resp = self._client.request(method, url, json=json, params=params, headers=headers)
        except httpx.TimeoutException as e:
            raise LomoTimeoutError(f"请求超时: {e}") from e
        except httpx.HTTPError as e:
            raise LomoConnectionError(f"网络错误: {e}") from e

        try:
            payload = resp.json()
        except ValueError as e:
            raise LomoConnectionError(f"非 JSON 响应 (HTTP {resp.status_code}): {resp.text[:200]}") from e

        _raise_for_envelope(payload)
        return payload.get("data")

    def get(self, path: str, *, params: Optional[dict] = None, stream: bool = False):
        return self.request("GET", path, params=params, stream=stream)

    def post(
        self,
        path: str,
        *,
        json: Any = None,
        params: Optional[dict] = None,
        stream: bool = False,
    ):
        return self.request("POST", path, json=json, params=params, stream=stream)

    def put(self, path: str, *, json: Any = None, params: Optional[dict] = None):
        return self.request("PUT", path, json=json, params=params)

    def delete(self, path: str, *, params: Optional[dict] = None):
        return self.request("DELETE", path, params=params)


__all__ = [
    "LomoClient",
    "StreamingJSONL",
    "SSEEventStream",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
]
