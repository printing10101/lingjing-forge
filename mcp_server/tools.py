"""lingjing-mcp: 灵境制造 Agent Gateway MCP工具集。

通过Agent Gateway API与后端通信，提供LNN模型管理、预测和训练能力。
所有工具均通过HTTP调用FastAPI后端，使用Bearer Token认证。

统一错误处理策略：
- 所有工具返回标准化的文本响应（JSON格式）
- 网络异常和API错误均被捕获并返回结构化错误信息
- 输入参数经过严格的物理边界校验
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re as _re
import secrets
from typing import Any

import httpx

logger = logging.getLogger("lingjing-mcp")

AGENT_TOKEN: str = os.environ.get("LINGJING_AGENT_TOKEN", "")
BASE_URL: str = os.environ.get("LINGJING_API_URL", "http://localhost:8765")

_DEFAULT_TIMEOUT = 30.0
_USER_AGENT = "lingjing-mcp/1.0"

_ALLOWED_MODEL_NAME_PATTERN = _re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")

# 输入长度限制，防止 DoS
_MAX_INPUT_SIZE = 100_000  # predict() 输入的浮点数列表最大长度
_MAX_DATA_PATH_LEN = 1024  # train() 的 data_path 最大字符数
_MAX_MODEL_NAME_LEN = 128  # 模型名称最大字符数
_MAX_JOB_ID_LEN = 256  # job_id 最大字符数


def _sanitize_model_name(name: str) -> str:
    """校验并清理模型名称，防止路径遍历和注入。"""
    if not name or len(name) > _MAX_MODEL_NAME_LEN:
        raise ValueError(f"模型名称不能为空且最长 {_MAX_MODEL_NAME_LEN} 字符")
    if ".." in name or "/" in name or "\\" in name:
        raise ValueError(f"模型名称包含非法字符: {name!r}")
    if not _ALLOWED_MODEL_NAME_PATTERN.match(name):
        raise ValueError(f"模型名称格式无效: {name!r}")
    return name


def _sanitize_job_id(job_id: str) -> str:
    """校验 job_id 格式。"""
    if not job_id or len(job_id) > _MAX_JOB_ID_LEN:
        raise ValueError(f"job_id 不能为空且最长 {_MAX_JOB_ID_LEN} 字符")
    if not _ALLOWED_MODEL_NAME_PATTERN.match(job_id):
        raise ValueError(f"job_id 格式无效: {job_id!r}")
    return job_id


def _sanitize_data_path(path: str) -> str:
    """校验 data_path，防止路径遍历。"""
    if not path or len(path) > _MAX_DATA_PATH_LEN:
        raise ValueError(f"data_path 不能为空且最长 {_MAX_DATA_PATH_LEN} 字符")
    if ".." in path:
        raise ValueError(f"data_path 包含非法路径遍历字符: {path!r}")
    return path


def _validate_predict_input(input_data: list[float]) -> list[float]:
    """校验 predict() 输入数据的尺寸和值域。"""
    if not input_data:
        raise ValueError("input_data 不能为空")
    if len(input_data) > _MAX_INPUT_SIZE:
        raise ValueError(f"input_data 长度 {len(input_data)} 超过最大限制 {_MAX_INPUT_SIZE}")
    # 检查 NaN/Inf
    import math

    for i, v in enumerate(input_data):
        if math.isnan(v):
            raise ValueError(f"input_data[{i}] = NaN")
        if math.isinf(v):
            raise ValueError(f"input_data[{i}] = Inf")
    return input_data


_USER_AGENT = "lingjing-mcp/1.0"

# 安全修复：启动时校验 AGENT_TOKEN 强度，避免空 token 导致认证失效。
# 开发环境（LINGJING_MCP_DEV=1）可跳过校验，但会打印警告。
_DEV_MODE = os.environ.get("LINGJING_MCP_DEV", "").lower() in ("1", "true", "yes")
if not AGENT_TOKEN:
    if _DEV_MODE:
        logger.warning(
            "LINGJING_AGENT_TOKEN is empty. Running in dev mode with a random "
            "ephemeral token. This MUST NOT be used in production."
        )
        AGENT_TOKEN = secrets.token_urlsafe(32)
    else:
        raise RuntimeError(
            "LINGJING_AGENT_TOKEN environment variable is required "
            "(must be >= 32 chars). Set LINGJING_MCP_DEV=1 only for local development."
        )
elif len(AGENT_TOKEN) < 32 and not _DEV_MODE:
    raise RuntimeError(
        f"LINGJING_AGENT_TOKEN too short ({len(AGENT_TOKEN)} chars, need >= 32). "
        "Set LINGJING_MCP_DEV=1 only for local development."
    )

# 强制 BASE_URL 使用 HTTPS，除非是 localhost 开发环境
if not BASE_URL.startswith("https://"):
    if "localhost" not in BASE_URL and "127.0.0.1" not in BASE_URL:
        if not _DEV_MODE:
            raise RuntimeError(f"LINGJING_API_URL must use HTTPS in production: {BASE_URL}")
    else:
        logger.debug("Using non-HTTPS BASE_URL for localhost development: %s", BASE_URL)


_PARAM_CONSTRAINTS = {
    "learning_rate": (1e-6, 0.1),
    "epochs": (1, 10000),
    "batch_size": (1, 1024),
    "poll_interval": (0.5, 60.0),
}


def _headers() -> dict[str, str]:
    h = {
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }
    if AGENT_TOKEN:
        # 使用 hmac.compare_digest 进行 token 比较时序攻击防护
        # （此处仅设置头，实际比较在后端）
        h["Authorization"] = f"Bearer {AGENT_TOKEN}"
    return h


def _client() -> httpx.AsyncClient:
    """共享 HTTP 客户端：目标为 localhost 网关，禁用环境代理。

    trust_env=False 防止系统代理（HTTP(S)_PROXY / Clash 等）劫持
    127.0.0.1 请求——实测代理会让本地请求 404/超时（与本地 LLM
    推理同款修复，见 app/ai/llm/provider_base.py）。
    """
    return httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, trust_env=False)


def _generate_idempotency_key() -> str:
    import uuid

    return str(uuid.uuid4())


def _format_error(message: str, detail: Any = None) -> str:
    error_response = {"error": True, "message": message}
    if detail is not None:
        error_response["detail"] = detail
    return json.dumps(error_response, indent=2, ensure_ascii=False)


def _format_success(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _validate_range(name: str, value: float, constraints: tuple[float, float]) -> str | None:
    lo, hi = constraints
    if value < lo or value > hi:
        return f"{name}={value}超出有效范围[{lo}, {hi}]"
    return None


async def list_models() -> dict[str, Any]:
    async with _client() as client:
        resp = await client.get(f"{BASE_URL}/api/agent/v1/models", headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def get_model_info(name: str) -> dict[str, Any]:
    name = _sanitize_model_name(name)
    async with _client() as client:
        resp = await client.get(f"{BASE_URL}/api/agent/v1/models/{name}/info", headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def predict(model_name: str, input_data: list[float], return_confidence: bool = False) -> dict[str, Any]:
    model_name = _sanitize_model_name(model_name)
    input_data = _validate_predict_input(input_data)
    payload = {
        "model_name": model_name,
        "input_data": input_data,
        "return_confidence": return_confidence,
    }
    async with _client() as client:
        resp = await client.post(
            f"{BASE_URL}/api/agent/v1/predict",
            headers={**_headers(), "Idempotency-Key": _generate_idempotency_key()},
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def train(
    model_name: str,
    data_path: str,
    learning_rate: float = 0.001,
    epochs: int = 100,
    batch_size: int = 32,
    optimizer: str = "adam",
    device: str = "auto",
) -> dict[str, Any]:
    model_name = _sanitize_model_name(model_name)
    data_path = _sanitize_data_path(data_path)
    for param_name, value in [
        ("learning_rate", learning_rate),
        ("epochs", float(epochs)),
        ("batch_size", float(batch_size)),
    ]:
        if param_name in _PARAM_CONSTRAINTS:
            err = _validate_range(param_name, value, _PARAM_CONSTRAINTS[param_name])
            if err:
                raise ValueError(err)

    valid_optimizers = frozenset({"adam", "sgd", "adamw", "rmsprop"})
    if optimizer.lower() not in valid_optimizers:
        raise ValueError(f"optimizer='{optimizer}'无效，可选: {sorted(valid_optimizers)}")

    payload = {
        "model_name": model_name,
        "data_path": data_path,
        "hyperparameters": {
            "learning_rate": learning_rate,
            "epochs": epochs,
            "batch_size": batch_size,
            "optimizer": optimizer,
        },
        "device": device,
    }
    async with _client() as client:
        resp = await client.post(
            f"{BASE_URL}/api/agent/v1/train",
            headers={**_headers(), "Idempotency-Key": _generate_idempotency_key()},
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def get_train_status(job_id: str) -> dict[str, Any]:
    job_id = _sanitize_job_id(job_id)
    async with _client() as client:
        resp = await client.get(f"{BASE_URL}/api/agent/v1/train/{job_id}", headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def wait_for_training(
    job_id: str,
    poll_interval: float = 2.0,
    timeout: float = 3600.0,
) -> dict[str, Any]:
    job_id = _sanitize_job_id(job_id)
    # 限制最大超时时间为 24 小时，防止无限制轮询
    timeout = min(timeout, 86400.0)
    err = _validate_range("poll_interval", poll_interval, (0.5, 60.0))
    if err:
        raise ValueError(err)

    start = asyncio.get_event_loop().time()
    while True:
        result = await get_train_status(job_id)
        status = result.get("data", {}).get("status", "")
        if status in ("success", "failed", "cancelled"):
            return result
        if asyncio.get_event_loop().time() - start > timeout:
            return {"error": "timeout", "job_id": job_id, "message": "训练超时"}
        await asyncio.sleep(poll_interval)


def register_tools(server) -> None:
    """在MCP Server实例上注册所有工具。

    注册 6 个标准化 LNN 工具 + 仿真工厂 4 个工具 + 演示设备自动生成工具
    （W10.1 扩面）+ CAM 主链路只读工具 4 个（W11 扩面）：
    - lnn_list_models: 列出所有模型 (R)
    - lnn_get_model_info: 获取模型详情 (R)
    - lnn_predict: 预测推理 (R)
    - lnn_train: 启动训练 (B)
    - lnn_get_train_status: 查询训练状态 (R)
    - lnn_wait_for_training: 等待训练完成 (R)
    - factory_run_cycle / factory_get_status / factory_get_kpis / factory_step:
      语言驱动仿真工厂（闭环生产 / 感知状态 / KPI / 单步推进）
    - {device_id}_*: 演示设备描述符自动生成工具（AAS 元数据 → 工具，A2M 思路）
    - gcode_get_failure_stats / gcode_list_failure_cases /
      cam_recommend_process / cam_get_quadruple_stats:
      CAM 主链路只读（失败案例库 + 工艺四元组推荐，Phase 0 自进化感知面）
    - gcode_create_job (B) / gcode_get_job_status / gcode_list_jobs:
      G 代码生成任务 job 化（W12；输入路径后端白名单 fail-closed）
    - dxf_describe_file: DXF 图纸解析摘要（W13；CAM 链路最上游感知）
    - process_plan_run (B): 端到端工艺规划（W14；孔描述→知识库→排序→G 代码）

    权限类: R = Read, B = Budgeted Write
    扩面开关: ``LINGJING_MCP_FACTORY_TOOLS=0`` 关闭工厂/设备工具。
    故障隔离: 工厂/设备注册失败仅告警，不影响 LNN 工具可用性。
    """

    @server.tool(name="lnn_list_models", description="列出所有已注册的LNN模型及其基本元数据")
    async def lnn_list_models() -> list[dict]:
        try:
            result = await list_models()
            return [{"type": "text", "text": _format_success(result)}]
        except Exception as exc:
            logger.exception("lnn_list_models failed")
            return [{"type": "text", "text": _format_error(str(exc))}]

    @server.tool(
        name="lnn_get_model_info",
        description="获取指定LNN模型的详细信息，包括架构、参数量和性能指标",
    )
    async def lnn_get_model_info(name: str) -> list[dict]:
        try:
            result = await get_model_info(name)
            return [{"type": "text", "text": _format_success(result)}]
        except Exception as exc:
            logger.exception("lnn_get_model_info failed for %s", name)
            return [{"type": "text", "text": _format_error(str(exc))}]

    @server.tool(
        name="lnn_predict",
        description=("使用指定LNN模型对输入数据进行预测推理。input_data为浮点数列表，长度需与模型输入维度匹配"),
    )
    async def lnn_predict(
        model_name: str,
        input_data: list[float],
        return_confidence: bool = False,
    ) -> list[dict]:
        try:
            if not input_data:
                return [{"type": "text", "text": _format_error("input_data不能为空")}]
            result = await predict(model_name, input_data, return_confidence)
            return [{"type": "text", "text": _format_success(result)}]
        except Exception as exc:
            logger.exception(
                "lnn_predict failed for %s with %d inputs",
                model_name,
                len(input_data),
            )
            return [{"type": "text", "text": _format_error(str(exc))}]

    @server.tool(
        name="lnn_train",
        description=(
            "启动LNN模型异步训练任务，返回job_id用于追踪进度。支持配置学习率、epoch数、batch_size、优化器和计算设备"
        ),
    )
    async def lnn_train(
        model_name: str,
        data_path: str,
        learning_rate: float = 0.001,
        epochs: int = 100,
        batch_size: int = 32,
        optimizer: str = "adam",
        device: str = "auto",
    ) -> list[dict]:
        try:
            result = await train(model_name, data_path, learning_rate, epochs, batch_size, optimizer, device)
            return [{"type": "text", "text": _format_success(result)}]
        except ValueError as exc:
            logger.warning("lnn_train validation error: %s", exc)
            return [{"type": "text", "text": _format_error(str(exc))}]
        except Exception as exc:
            logger.exception("lnn_train failed for %s", model_name)
            return [{"type": "text", "text": _format_error(str(exc))}]

    @server.tool(
        name="lnn_get_train_status",
        description="查询指定训练任务的当前状态（pending/running/success/failed/cancelled）",
    )
    async def lnn_get_train_status(job_id: str) -> list[dict]:
        try:
            result = await get_train_status(job_id)
            return [{"type": "text", "text": _format_success(result)}]
        except Exception as exc:
            logger.exception("lnn_get_train_status failed for %s", job_id)
            return [{"type": "text", "text": _format_error(str(exc))}]

    @server.tool(
        name="lnn_wait_for_training",
        description="等待训练任务完成，以poll_interval秒间隔轮询直至状态为终态",
    )
    async def lnn_wait_for_training(job_id: str, poll_interval: float = 2.0) -> list[dict]:
        try:
            result = await wait_for_training(job_id, poll_interval)
            return [{"type": "text", "text": _format_success(result)}]
        except Exception as exc:
            logger.exception("lnn_wait_for_training failed for %s", job_id)
            return [{"type": "text", "text": _format_error(str(exc))}]

    # 设备元数据 MCP 工具自动生成（Phase 2：A2M 思路）
    # 按 DeviceDescriptor 的 capabilities 自动注册 {device_id}_{op} 工具，
    # 后端为仿真设备（SimulatedDevice），参数越界 fail-closed。
    # 注册失败不影响既有 6 个 LNN 工具。
    factory_tools_enabled = os.environ.get("LINGJING_MCP_FACTORY_TOOLS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    if factory_tools_enabled:
        try:
            from mcp_server.device_registry import build_demo_registry
            from mcp_server.device_tools import register_device_tools

            for descriptor in build_demo_registry():
                register_device_tools(server, descriptor)
        except Exception as exc:  # noqa: BLE001 - 设备工具注册失败不阻断 LNN 工具
            logger.warning("设备工具注册失败（不影响 LNN 工具）: %s", exc)

        # 仿真工厂工具（升级①：语言驱动仿真工厂，SUPCON 思路）
        try:
            from mcp_server.factory_tools import register_factory_tools

            register_factory_tools(server)
        except Exception as exc:  # noqa: BLE001 - 工厂工具注册失败不影响既有工具
            logger.warning("仿真工厂工具注册失败（不影响 LNN/设备工具）: %s", exc)
    else:
        logger.info("LINGJING_MCP_FACTORY_TOOLS=0：工厂/设备工具未注册（仅 LNN 工具面）")

    # CAM 主链路只读工具组（W11 扩面：失败案例库 + 工艺四元组推荐）
    # 独立开关，故障隔离——注册失败不影响 LNN/工厂/设备工具。
    cam_tools_enabled = os.environ.get("LINGJING_MCP_CAM_TOOLS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    if cam_tools_enabled:
        try:
            from mcp_server.cam_tools import register_cam_tools

            register_cam_tools(server)
        except Exception as exc:  # noqa: BLE001 - CAM 工具注册失败不影响既有工具
            logger.warning("CAM 工具注册失败（不影响既有工具）: %s", exc)
    else:
        logger.info("LINGJING_MCP_CAM_TOOLS=0：CAM 只读工具未注册")

    # G 代码生成任务工具组（W12 扩面：写类 job 化，1 B + 2 R）
    # 独立开关，故障隔离——注册失败不影响既有工具。
    gcode_tools_enabled = os.environ.get(
        "LINGJING_MCP_GCODE_TOOLS", "1"
    ).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    if gcode_tools_enabled:
        try:
            from mcp_server.gcode_job_tools import register_gcode_job_tools

            register_gcode_job_tools(server)
        except Exception as exc:  # noqa: BLE001 - 注册失败不影响既有工具
            logger.warning("G 代码任务工具注册失败（不影响既有工具）: %s", exc)
    else:
        logger.info("LINGJING_MCP_GCODE_TOOLS=0：G 代码任务工具未注册")

    # DXF 图纸解析只读工具（W13 扩面）
    dxf_tools_enabled = os.environ.get("LINGJING_MCP_DXF_TOOLS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    if dxf_tools_enabled:
        try:
            from mcp_server.dxf_tools import register_dxf_tools

            register_dxf_tools(server)
        except Exception as exc:  # noqa: BLE001 - 注册失败不影响既有工具
            logger.warning("DXF 工具注册失败（不影响既有工具）: %s", exc)
    else:
        logger.info("LINGJING_MCP_DXF_TOOLS=0：DXF 工具未注册")

    # 工艺规划工具（W14 扩面 · 写类同步）
    process_tools_enabled = os.environ.get(
        "LINGJING_MCP_PROCESS_TOOLS", "1"
    ).strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )
    if process_tools_enabled:
        try:
            from mcp_server.process_planning_tools import register_process_planning_tools

            register_process_planning_tools(server)
        except Exception as exc:  # noqa: BLE001 - 注册失败不影响既有工具
            logger.warning("工艺规划工具注册失败（不影响既有工具）: %s", exc)
    else:
        logger.info("LINGJING_MCP_PROCESS_TOOLS=0：工艺规划工具未注册")
