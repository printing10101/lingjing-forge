"""LLM 服务自动探测引擎。

扫描本机常见端口和进程，自动发现已安装/已运行的本地 LLM 服务。
仅用于辅助用户初始化配置，不修改任何系统设置。

探测策略：
1. 端口扫描：检查常见 LLM 服务端口是否开放
2. 进程识别：扫描进程列表识别 LLM 服务进程名
3. API 探测：对开放端口发起轻量 API 请求确认服务类型
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from app.ai.llm.provider_base import ProviderConfig, ProviderType

logger = logging.getLogger(__name__)


# 探测目标定义

# 各 Provider 的默认探测配置：端口、进程名、确认 API 路径
_PROBE_TARGETS: list[dict[str, Any]] = [
    {
        "provider_type": ProviderType.OLLAMA,
        "port": 11434,
        "host": "127.0.0.1",
        "process_names": ["ollama", "ollama.exe", "ollama_llama_server"],
        "health_path": "/api/tags",
        "default_base_url": "http://127.0.0.1:11434",
        "display_name": "Ollama",
    },
    {
        "provider_type": ProviderType.LMSTUDIO,
        "port": 1234,
        "host": "127.0.0.1",
        "process_names": ["llama", "Llama", "lm-studio", "lmstudio"],
        "health_path": "/v1/models",
        "default_base_url": "http://127.0.0.1:1234/v1",
        "display_name": "LM Studio",
    },
    {
        "provider_type": ProviderType.LLAMACPP,
        "port": 8080,
        "host": "127.0.0.1",
        "process_names": ["llama", "main", "server", "llama-server", "llamafile"],
        "health_path": "/v1/models",
        "default_base_url": "http://127.0.0.1:8080/v1",
        "display_name": "llama.cpp",
    },
    {
        "provider_type": ProviderType.LLAMA,
        "port": 8081,
        "host": "127.0.0.1",
        "process_names": ["llama-server", "llama"],
        "health_path": "/v1/models",
        "default_base_url": "http://127.0.0.1:8081/v1",
        "display_name": "Llama (llama-server)",
    },
    {
        "provider_type": ProviderType.VLLM,
        "port": 8000,
        "host": "127.0.0.1",
        "process_names": ["vllm", "python", "python3"],  # vllm 通常以 python 进程运行
        "health_path": "/v1/models",
        "default_base_url": "http://127.0.0.1:8000/v1",
        "display_name": "vLLM",
    },
    {
        "provider_type": ProviderType.TGI,
        "port": 8090,
        "host": "127.0.0.1",
        "process_names": ["text-generation-launcher", "text-generation-server", "tgi"],
        "health_path": "/v1/models",
        "default_base_url": "http://127.0.0.1:8090/v1",
        "display_name": "Text Generation Inference",
    },
    {
        "provider_type": ProviderType.KOBOLDCPP,
        "port": 5001,
        "host": "127.0.0.1",
        "process_names": ["koboldcpp", "kobold_cpp", "koboldcpp.exe"],
        "health_path": "/v1/models",
        "default_base_url": "http://127.0.0.1:5001/v1",
        "display_name": "KoboldCpp",
    },
]


@dataclass
class DetectionResult:
    """单个 Provider 的探测结果。"""

    provider_type: ProviderType
    display_name: str
    host: str
    port: int
    base_url: str
    port_open: bool = False
    process_found: bool = False
    api_responsive: bool = False
    models: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def is_available(self) -> bool:
        """是否可用（端口开放且 API 响应）。"""
        return self.port_open and self.api_responsive

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_type": self.provider_type.value,
            "display_name": self.display_name,
            "host": self.host,
            "port": self.port,
            "base_url": self.base_url,
            "port_open": self.port_open,
            "process_found": self.process_found,
            "api_responsive": self.api_responsive,
            "is_available": self.is_available,
            "models": self.models,
            "error": self.error,
        }


class AutoDetector:
    """LLM 服务自动探测引擎。

    使用并发探测提高扫描速度，默认超时 2 秒/端口。
    """

    def __init__(self, timeout: float = 2.0) -> None:
        self.timeout = timeout

    async def detect_all(self) -> list[DetectionResult]:
        """并发探测所有预定义的本地 LLM 服务。

        Returns:
            探测结果列表（含可用和不可用的）

        P0-4 修复：return_exceptions=True 防止单个 Provider 探测异常导致全部结果丢失。
        原实现 return_exceptions=False（默认），任一 _detect_one 抛出未预期异常时，
        gather 会取消其余任务并向上抛出，导致整批探测结果为空 —— 运维误以为无任何
        本地 LLM 可用。改为 True 后，异常以 Exception 对象形式返回，此处过滤为
        带错误信息的 DetectionResult，保证其余正常探测结果不受影响。
        """
        tasks = [self._detect_one(target) for target in _PROBE_TARGETS]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        results: list[DetectionResult] = []
        for target, raw in zip(_PROBE_TARGETS, raw_results):
            if isinstance(raw, Exception):
                # 单个探测失败不应影响其余结果，构造带错误信息的降级结果
                err_result = DetectionResult(
                    provider_type=target["provider_type"],
                    display_name=target["display_name"],
                    host=target["host"],
                    port=target["port"],
                    base_url=target["default_base_url"],
                    error=f"探测异常: {type(raw).__name__}: {raw}",
                )
                results.append(err_result)
                logger.warning(
                    "LLM 探测异常 (%s): %s",
                    target["display_name"],
                    raw,
                    exc_info=raw,
                )
            elif isinstance(raw, DetectionResult):
                results.append(raw)
        return results

    async def detect_one(self, provider_type: ProviderType) -> DetectionResult | None:
        """探测单个 Provider 类型。"""
        target = next((t for t in _PROBE_TARGETS if t["provider_type"] == provider_type), None)
        if target is None:
            return None
        return await self._detect_one(target)

    async def _detect_one(self, target: dict[str, Any]) -> DetectionResult:
        """探测单个目标。"""
        result = DetectionResult(
            provider_type=target["provider_type"],
            display_name=target["display_name"],
            host=target["host"],
            port=target["port"],
            base_url=target["default_base_url"],
        )

        # 端口扫描
        result.port_open = await self._check_port(target["host"], target["port"])

        # 进程识别（辅助信息，移至线程池避免阻塞事件循环）
        result.process_found = await asyncio.to_thread(self._check_processes, target["process_names"])

        # API 探测（仅当端口开放时）
        if result.port_open:
            try:
                result.api_responsive, result.models = await self._probe_api(
                    target["default_base_url"], target["health_path"]
                )
            except Exception as e:
                result.error = "API 探测失败: 服务不可用或响应超时"
                logger.debug(
                    "API probe failed for %s: %s",
                    target["display_name"],
                    e,
                )

        return result

    async def _check_port(self, host: str, port: int) -> bool:
        """检查端口是否开放（异步 socket 连接）。"""
        try:
            future = asyncio.open_connection(host, port)
            reader, writer = await asyncio.wait_for(future, timeout=self.timeout)
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionError) as close_err:
                # wait_closed 失败不影响端口可达性判断，仅记录便于排查
                logger.debug("writer.wait_closed failed (host=%s port=%d): %s", host, port, close_err, exc_info=True)
            return True
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
            logger.debug("Port %d:%d closed: %s", host, port, e)
            return False

    def _check_processes(self, process_names: list[str]) -> bool:
        """检查是否有匹配的进程在运行（best-effort）。"""
        try:
            import psutil
        except ImportError:
            # psutil 未安装时降级为 False
            return False

        try:
            names_lower = {n.lower() for n in process_names}
            for proc in psutil.process_iter(["name"]):
                proc_name = (proc.info.get("name") or "").lower()
                if any(target in proc_name for target in names_lower):
                    return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as proc_err:
            # 进程枚举失败（权限/进程消失）属于 best-effort 检测的预期异常，
            # 记录 debug 级别便于排查，不影响整体探测结果（返回 False）
            logger.debug("process scan failed: %s", proc_err, exc_info=True)
        return False

    async def _probe_api(self, base_url: str, health_path: str) -> tuple[bool, list[str]]:
        """探测 API 是否响应，并尝试获取模型列表。

        Returns:
            (api_responsive, models_list)
        """
        from app.ai.llm_client import get_shared_http_client

        client = await get_shared_http_client()
        url = f"{base_url.rstrip('/')}{health_path}"
        try:
            response = await client.get(url, timeout=self.timeout)
            if response.status_code != 200:
                return False, []
            # 尝试解析模型列表
            models = self._parse_models(response.json())
            return True, models
        except Exception as e:
            logger.debug("API probe to %s failed: %s", url, e)
            return False, []

    @staticmethod
    def _parse_models(data: dict[str, Any]) -> list[str]:
        """解析模型列表（兼容 Ollama 和 OpenAI 格式）。"""
        models: list[str] = []
        # OpenAI 格式: {"data": [{"id": "model-name"}, ...]}
        for item in data.get("data", []):
            model_id = item.get("id") or item.get("name", "")
            if model_id:
                models.append(model_id)
        # Ollama 格式: {"models": [{"name": "model-name"}, ...]}
        for item in data.get("models", []):
            model_name = item.get("name", "")
            if model_name:
                models.append(model_name)
        return models

    def generate_provider_configs(self, results: list[DetectionResult]) -> list[ProviderConfig]:
        """根据探测结果生成可用的 Provider 配置列表。

        仅包含 is_available=True 的结果。
        """
        configs: list[ProviderConfig] = []
        for result in results:
            if not result.is_available:
                continue
            config = ProviderConfig(
                provider_id=f"{result.provider_type.value}-auto",
                name=result.display_name,
                provider_type=result.provider_type,
                base_url=result.base_url,
                default_model=result.models[0] if result.models else "",
                enabled=True,
            )
            configs.append(config)
        return configs


# 全局单例（双重检查锁，线程安全）
_detector: AutoDetector | None = None
_detector_lock = threading.Lock()


def get_detector() -> AutoDetector:
    """获取全局 AutoDetector 实例。"""
    global _detector
    if _detector is not None:
        return _detector
    with _detector_lock:
        if _detector is None:
            _detector = AutoDetector()
    return _detector
