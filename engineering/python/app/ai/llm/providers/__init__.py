"""本地 LLM Provider 实现集合。"""

from app.ai.llm.providers.ollama import OllamaProvider
from app.ai.llm.providers.lmstudio import LMStudioProvider
from app.ai.llm.providers.llamacpp import LlamaCppProvider
from app.ai.llm.providers.llama import LlamaProvider
from app.ai.llm.providers.vllm import VllmProvider
from app.ai.llm.providers.tgi import TGIProvider
from app.ai.llm.providers.koboldcpp import KoboldCppProvider

__all__ = [
    "OllamaProvider",
    "LMStudioProvider",
    "LlamaCppProvider",
    "LlamaProvider",
    "VllmProvider",
    "TGIProvider",
    "KoboldCppProvider",
]
