#!/usr/bin/env python
"""
批量修复 LLM provider 异常处理

将 50+ 处泛型 except Exception 转换为精确异常处理 + 熔断器
"""

import re
from pathlib import Path
from datetime import datetime


PROVIDER_FILES = [
    "engineering/python/app/ai/llm/providers/ollama.py",
    "engineering/python/app/ai/llm/providers/koboldcpp.py",
    "engineering/python/app/ai/llm/providers/llamacpp.py",
    "engineering/python/app/ai/llm/providers/lmstudio.py",
    "engineering/python/app/ai/llm/providers/tgi.py",
    "engineering/python/app/ai/llm/providers/vllm.py",
    "engineering/python/app/ai/llm/providers/cloud/anthropic_provider.py",
    "engineering/python/app/ai/llm/providers/cloud/deepseek_provider.py",
    "engineering/python/app/ai/llm/providers/cloud/gemini_provider.py",
    "engineering/python/app/ai/llm/providers/cloud/openai_compatible.py",
    "engineering/python/app/ai/llm/providers/cloud/openai_provider.py",
    "engineering/python/app/ai/llm/providers/cloud/qwen_provider.py",
]


def fix_provider_exceptions(file_path: Path) -> bool:
    """修复单个 provider 文件"""
    if not file_path.exists():
        print(f"Skipping (not found): {file_path.name}")
        return False

    print(f"Processing: {file_path.relative_to(Path.cwd())}")

    try:
        content = file_path.read_text(encoding="utf-8")
        original = content
        changes = []

        # 1. 添加必要的导入
        if "from app.core.exceptions import" not in content:
            # 在已有的核心导入后添加
            content = content.replace(
                "from app.ai.llm.provider_base import(",
                content.split("from app.ai.llm.provider_base import(")[1]
                .split(")")[0]
                .replace("from app.ai.llm.provider_base import(", ""),
            )

            # 添加异常导入
            if "import logging" in content:
                content = content.replace(
                    "import logging",
                    "import logging\n"
                    + "from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitBreakerRegistry\n"
                    + "from app.core.exceptions import (\n"
                    + "    LLMException,\n"
                    + "    LLMProviderException,\n"
                    + "    LLMTimeoutException,\n"
                    + "    LLMRateLimitException,\n"
                    + ")\n",
                )

        # 2. 替换特定的异常捕获模式
        # 模式 1: except Exception as e: # noqa: BLE001
        # 修复为带详细记录的捕获
        pattern_leading_code = (
            r"except\s+Exception\s+as\s+e:\s*#\s*noqa:\s*BLE001\s*\n\s*logger\.debug\(.*?\n\s*return.*?\n"
        )

        def replace_provider_exception(match):
            """替换 provider 的泛型异常捕获"""
            # 提取上下文
            full_match = match.group(0)

            # 生成新的异常处理
            new_handler = """        except (ConnectionError, aiohttp.ClientConnectionError) as e:
            logger.exception("Provider connection error: %s", e)
            raise LLMProviderException(provider=self.config.provider_type.name, message=str(e)) from e
        except (TimeoutError, asyncio.TimeoutError, aiohttp.ServerTimeoutError) as e:
            logger.exception("Provider timeout: %s", e)
            raise LLMTimeoutException(provider=self.config.provider_type.name) from e
        except aiohttp.ClientResponseError as e:
            logger.exception("Provider HTTP error: %s", e)
            raise LLMProviderException(provider=self.config.provider_type.name, message=f"HTTP {e.status}: {e.message}") from e
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.exception("Provider response parse error: %s", e)
            raise LLMResponseException(message=str(e)) from e
        except Exception as e:
            logger.exception("Provider failed: %s", e)
            raise LLMException(provider=self.config.provider_type.name, message=str(e)) from e
"""
            return new_handler

        # 先处理更具体的异常类型
        content = re.sub(r"except\s+\(ConnectionError.*?\n", replace_provider_exception, content, count=1)

        # 3. 为每个重要方法添加熔断器保护
        # 检测是否需要熔断器
        if "async def detect" in content or "async def health_check" in content:
            # 在类级别添加熔断器初始化
            if "self._circuit_breaker:" not in content:
                content = re.sub(
                    r"(\s+)def __init__\(self, config: ProviderConfig\).*:\n",
                    r"\1def __init__(self, config: ProviderConfig) -> None:\n"
                    + r"\1    self._circuit_breaker = CircuitBreaker(\n"
                    + r'\1        f"{self.config.provider_type.name}_provider",\n'
                    + r"\1        config=CircuitBreakerConfig(\n"
                    + r"\1            failure_threshold=5,\n"
                    + r"\1            recovery_timeout=30,\n"
                    + r"\1        )\n"
                    + r"\1    )",
                    content,
                    count=1,
                )

            # 在 detect 和 health_check 中包装
            content = re.sub(
                r"(async def detect\(self\).*?\n.*?)(\n\s+except Exception)",
                lambda m: (
                    f"{m.group(1)}        return await self._circuit_breaker.execute(self._execute_detect)"
                    + f"\n{m.group(2)}"
                ),
                content,
                flags=re.DOTALL,
            )

        # 写入修改
        if content != original:
            # 备份
            backup = file_path.with_suffix(".py.backup")
            backup.write_bytes(original.encode("utf-8"))

            # 写入
            file_path.write_text(content, encoding="utf-8")
            print(f"  ✓ Modified: {file_path.name}")
            changes.append(f"异常处理改进 + 熔断器集成")
            return True
        else:
            print(f"  No changes needed")
            return False

    except Exception as e:
        print(f"  Failed: {e}")
        return False


def main():
    """主函数"""
    print("=" * 80)
    print("批量修复 LLM Provider 异常处理")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    cwd = Path.cwd()
    files_processed = 0
    files_modified = 0

    for relative_path in PROVIDER_FILES:
        file_path = cwd / relative_path
        if fix_provider_exceptions(file_path):
            files_modified += 1
        files_processed += 1

    print("=" * 80)
    print(f"Completed: {files_modified}/{files_processed} files modified")
    print()
    print("Next steps:")
    print("   1. Review modified files for correctness")
    print("   2. Run tests: unset PYTHONPATH && py -3.14 -m pytest")
    print("   3. Fix any breakages")
    print("=" * 80)


if __name__ == "__main__":
    print("This script modifies multiple provider files.")
    print("   Backup created automatically.\n")

    input("PressEnter to continue, Ctrl+C to cancel: ")
    main()
