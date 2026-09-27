"""Pilot 专用的 OpenAI 兼容 LLM 客户端（DeepSeek / llama-server）。

不复用 app/ai/llm 的 provider 注册表：pilot 是可复现实验，需要与运行时
配置/鉴权解耦的直连调用，base_url/model/temperature 全部记录进 run manifest。
两个后端都是 OpenAI 兼容协议，一个类覆盖。
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

# llm_backends.py → machinability_pilot → benchmarks → app → python → engineering → 仓库根
REPO_ROOT = Path(__file__).resolve().parents[5]


@dataclass(frozen=True)
class OpenAICompatBackend:
    """一个可调用的 OpenAI 兼容 chat 后端。"""

    name: str
    base_url: str
    model: str
    api_key: str | None = None
    timeout_seconds: float = 90.0
    max_tokens: int = 2048
    temperature: float = 0.2

    def chat(self, prompt: str, system: str | None = None, retries: int = 2) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        # llama-server 偶发瞬时空响应（多槽位上下文争用等），传输层重试兜底
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = requests.post(
                    f"{self.base_url.rstrip('/')}/chat/completions",
                    json=body,
                    headers=headers,
                    timeout=self.timeout_seconds,
                )
                resp.raise_for_status()
                payload = resp.json()
                content = payload["choices"][0]["message"].get("content") or ""
                if content:
                    return content
                last_error = RuntimeError("LLM 返回空内容")
            except requests.RequestException as exc:
                last_error = exc
            time.sleep(1.0 + attempt)
        raise RuntimeError(f"LLM 调用失败（重试 {retries} 次后）: {last_error}")

    async def call(self, prompt: str, system: str | None = None) -> str:
        return await asyncio.to_thread(self.chat, prompt, system)


def load_api_key(name: str) -> str | None:
    """从环境变量或仓库根 .env 读取 key（只读取，不落日志）。"""
    val = os.environ.get(name)
    if val:
        return val.strip()
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{name}="):
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def deepseek_backend() -> OpenAICompatBackend:
    key = load_api_key("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置（环境变量或仓库根 .env）")
    return OpenAICompatBackend(
        name="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        api_key=key,
    )


def llama_server_backend(model: str = "qwen3-8b") -> OpenAICompatBackend:
    """本地 llama-server。默认端口 1234，可用环境变量 LLAMA_SERVER_URL 覆盖
    （如 http://127.0.0.1:1235/v1，用于本机已有其他 llama-server 占用默认端口的场景）。
    请求超时用 LLM_TIMEOUT_S 覆盖（思考型模型 + 慢推理需调大，如 600）。

    平台定制版 llama-server 强制 Bearer 鉴权，key 取自 LLAMA_API_KEY /
    LLM_API_KEY 环境变量，回退读取运动综合数据平台 backend/.env 的 AI_API_KEY
    （该 key 即平台 lm_manager 拉起 llama-server 时下发的值）。
    """
    key = os.environ.get("LLAMA_API_KEY") or os.environ.get("LLM_API_KEY")
    if not key:
        platform_env = Path(r"D:\运动综合数据平台\backend\.env")
        if platform_env.exists():
            for line in platform_env.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("AI_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    return OpenAICompatBackend(
        name=f"llama-server:{model}",
        base_url=os.environ.get("LLAMA_SERVER_URL", "http://localhost:1234/v1"),
        model=model,
        api_key=key,
        timeout_seconds=float(os.environ.get("LLM_TIMEOUT_S", "90")),
        max_tokens=int(os.environ.get("LLM_MAX_TOKENS", "2048")),
    )


def backend_by_name(name: str) -> OpenAICompatBackend:
    if name == "deepseek":
        return deepseek_backend()
    if name.startswith("llama:"):
        return llama_server_backend(model=name.split(":", 1)[1])
    if name == "llama":
        return llama_server_backend()
    raise ValueError(f"未知后端: {name}（可选 deepseek / llama[:model]）")
