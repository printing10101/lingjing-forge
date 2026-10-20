"""MCP tools for pending knowledge only; no approval, machine or DNC capability."""
import json
import logging
import uuid

import httpx

from mcp_server.tools import BASE_URL, _DEFAULT_TIMEOUT, _format_error, _format_success, _headers

logger = logging.getLogger("lingjing-mcp")
BASE = "/api/agent/v1/cam/knowledge-candidates"


def _http_error(error: httpx.HTTPStatusError) -> str:
    status = error.response.status_code
    retryable = status >= 500 or status in {408, 429}
    messages = {
        400: "候选格式或请求头无效，请核对输入后提交",
        401: "认证已失效，请恢复认证后再查询或提交",
        403: "当前 Agent 权限不足，不能提交或读取此候选",
        404: "候选不存在或不属于当前 Agent",
        409: "提交编号与已有内容冲突，请核对原候选，不要换编号掩盖变化",
        413: "候选超过大小上限，请缩减请求",
        422: "候选字段或参数无效，请核对输入；原请求不能直接重试",
    }
    message = messages.get(status, "网关请求未完成，请检查服务；提交状态未确认时保持相同提交编号和内容")
    return _format_error(message, {"http_status": status, "retryable": retryable})


async def _request(method: str, path: str, payload: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, trust_env=False) as client:
        headers = _headers()
        if method == "POST":
            headers["Idempotency-Key"] = uuid.uuid4().hex
        response = await client.request(method, BASE_URL + path, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


async def _handle_submit(submission_id: str, content: dict) -> str:
    try:
        if not isinstance(content, dict) or len(json.dumps(content, allow_nan=False).encode()) > 64 * 1024:
            raise ValueError("候选必须为不超过 64KB 的 JSON 对象")
        if any(key in content for key in ("review", "review_history", "actor", "record_id", "submission", "source", "confidence")):
            raise ValueError("提交候选不能指定审核状态、身份或置信度")
        from mcp_server.cam_tools import _sanitize_identifier
        submission_id = _sanitize_identifier(submission_id, "submission_id")
        result = await _request("POST", BASE, {"submission_id": submission_id, "content": content})
        return _format_success(result)
    except (ValueError, TypeError) as exc:
        return _format_error(str(exc))
    except httpx.HTTPStatusError as exc:
        return _http_error(exc)
    except Exception:  # noqa: BLE001 - MCP must return a useful observation on transport failure.
        logger.exception("Knowledge candidate submission failed")
        return _format_error("候选提交未确认，请使用相同 submission_id 和内容重试，不能推定已入库")


async def _handle_status(record_id: str) -> str:
    try:
        from mcp_server.cam_tools import _sanitize_identifier
        record_id = _sanitize_identifier(record_id, "record_id")
        return _format_success(await _request("GET", f"{BASE}/{record_id}"))
    except ValueError as exc:
        return _format_error(str(exc))
    except httpx.HTTPStatusError as exc:
        return _http_error(exc)
    except Exception:  # noqa: BLE001
        logger.exception("Knowledge candidate status failed")
        return _format_error("无法确认候选状态，请稍后查询；不要自动批准或生成正式推荐")


def register_knowledge_candidate_tools(server) -> None:
    @server.tool(name="cam_submit_knowledge_candidate", description="提交带来源的待审工艺候选（R+W）。同一 submission_id 和内容可安全重试；不批准知识。")
    async def submit(submission_id: str, content: dict) -> list[dict]:
        return [{"type": "text", "text": await _handle_submit(submission_id, content)}]

    @server.tool(name="cam_get_knowledge_candidate", description="查询当前 Agent 提交的候选及人工审核状态（R）；只有有效批准后才能再次查询正式推荐。")
    async def status(record_id: str) -> list[dict]:
        return [{"type": "text", "text": await _handle_status(record_id)}]
