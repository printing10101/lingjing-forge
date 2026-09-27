#!/usr/bin/env python
"""
智能修复 LLM providers 异常处理

功能：
1. 自动分析 provider 文件
2. 将泛型 except Exception 替换为精确异常处理
3. 集成新异常类
4. 生成备份文件
5. 报告修改统计

使用方法:
python scripts/fix_llm_providers.py
"""

import re
from pathlib import Path
from datetime import datetime
import json


# 异常类型映射表
EXCEPTION_MAPPINGS = {
    # 网络相关
    r"except\s+Exception\s+as\s+e:\s*#\s*noqa:\s*BLE001": 'except (ConnectionError, aiohttp.ClientConnectionError) as e:\n        raise LLMProviderException(provider=self.config.provider_type.name, message=f"连接失败：{e}") from e',
    # HTTP 错误
    r"except\s+Exception\s+as\s+e": 'except aiohttp.ClientResponseError as e:\n        raise LLMProviderException(provider=self.config.provider_type.name, message=f"HTTP {e.status}: {e.message}") from e\n    except asyncio.TimeoutError as e:\n        raise LLMTimeoutException(provider=self.config.provider_type.name) from e\n    except aiohttp.ClientTimeout as e:\n        raise LLMTimeoutException(provider=self.config.provider_type.name, message=f"超时：{e}") from e\n    except aiohttp.ClientError as e:\n        raise LLMProviderException(provider=self.config.provider_type.name, message=f"客户端错误：{e}") from e',
    # 简单替换模式
    "except Exception as e:": 'except Exception as e:  # noqa: PERF203, BLE001\n            logger.exception(f"[{self.config.provider_type.name}] Provider error: %s", e)\n            raise LLMException(provider=self.config.provider_type.name, message=str(e)) from e',
}


def analyze_file(file_path: Path) -> dict:
    """分析 provider 文件结构"""
    content = file_path.read_text(encoding="utf-8")

    return {
        "path": str(file_path.relative_to(Path.cwd())),
        "lines": len(content.splitlines()),
        "has_imports": "from app.ai.llm.provider_base import" in content,
        "has_exceptions": bool(re.search(r"except\s+Exception\s+as\s+e", content)),
        "exception_count": len(re.findall(r"except\s+Exception\s+as\s+e:\s*\n\s*logger", content)),
        "async_methods": len(re.findall(r"async def\s+\w+\s*\(", content)),
    }


def fix_provider_file(file_path: Path) -> dict:
    """修复单个 provider 文件"""
    result = {
        "file": str(file_path.relative_to(Path.cwd())),
        "status": "pending",
        "changes": 0,
        "errors": [],
    }

    try:
        if not file_path.exists():
            result["status"] = "skipped"
            result["errors"].append("File not found")
            return result

        content = file_path.read_text(encoding="utf-8")
        original = content
        changes_made = 0

        # 1. 添加必要的异常导入
        if "from app.core.exceptions import" not in content:
            # 创建导入块
            new_import = """
from app.core.exceptions import (
    LLMException,
    LLMProviderException,
    LLMTimeoutException,
    LLMRateLimitException,
)
"""
            # 找到合适的插入位置 (在 logging 导入后)
            if "import logging" in content:
                content = content.replace("import logging", "import logging\n" + new_import)
                changes_made += 1

        # 2. 替换具体的异常捕获模式
        patterns_to_fix = [
            # 模式 1: except Exception 后接 logger.debug (detect/health_check 等)
            (
                r"(async def\s+\w+\s*\([^)]*\).*?\n.*?)(except\s+Exception\s+as\s+e:\s*#\s*noqa:\s*BLE001\s*\n\s*logger\.debug\(.*?\n.*?\))",
                lambda m: (
                    m.group(1)
                    + """            except (ConnectionError, aiohttp.ClientConnectionError) as e:
                logger.warning(f"Connection error in {self.__class__.__name__}: {e}")
                return False
            except asyncio.TimeoutError as e:
                logger.warning(f"Timeout in {self.__class__.__name__}: {e}")
                return False
            except Exception as e:
                logger.debug(f"Unknown error in {self.__class__.__name__}: {e}")
                return False
"""
                ),
            ),
            # 模式 2: except Exception 后接 logger.warning (list 等操作)
            (
                r"(async def\s+\w+\s*\([^)]*\).*?\n.*?)(except\s+Exception\s+as\s+e:\s*#\s*noqa:\s*BLE001\s*\n\s*logger\.warning\(.*?\n.*?\))",
                lambda m: (
                    m.group(1)
                    + """            except aiohttp.ClientResponseError as e:
                logger.warning(f"HTTP error in {self.__class__.__name__}: {e}")
                return []
            except Exception as e:
                logger.warning(f"Unexpected error in {self.__class__.__name__}: {e}")
                return []
"""
                ),
            ),
            # 模式 3: 最后的通用 except
            (
                r"(\n.*?except\s+Exception\s+as\s+e:\s*#\s*noqa:\s*BLE001\s*\n\s*logger\.info\(.*?\))",
                lambda m: (
                    m.group(1).replace("logger.info", "logger.exception")
                    + "\n            raise LLMException(provider=self.config.provider_type.name, message=str(e)) from e"
                ),
            ),
        ]

        # 应用所有替换模式
        for pattern, replacer in patterns_to_fix:
            new_content = re.sub(pattern, replacer, content, flags=re.DOTALL)
            if new_content != content:
                content = new_content
                changes_made += 1

        result["changes"] = changes_made

        # 3. 写入修改
        if content != original:
            # 创建备份
            backup = file_path.with_suffix(".py.backup")
            backup.write_bytes(original.encode("utf-8"))
            result["backup"] = str(backup.relative_to(Path.cwd()))

            # 写入修改
            file_path.write_text(content, encoding="utf-8")
            result["status"] = "fixed"
            result["messages"] = f"Modified {changes_made} places"
        else:
            result["status"] = "unchanged"
            result["messages"] = "No changes required"

        return result

    except Exception as e:
        result["status"] = "error"
        result["errors"].append(str(e))
        return result


def main():
    """主函数"""
    print("=" * 80)
    print("智能修复 LLM Providers 异常处理")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 定义 provider 文件列表 (按优先级排序)
    provider_files = [
        # Phase 1: Core providers (最重要)
        Path("engineering/python/app/ai/llm/providers/cloud/openai_provider.py"),
        Path("engineering/python/app/ai/llm/providers/cloud/deepseek_provider.py"),
        Path("engineering/python/app/ai/llm/providers/cloud/gemini_provider.py"),
        # Phase 2: Local providers
        Path("engineering/python/app/ai/llm/providers/ollama.py"),
        Path("engineering/python/app/ai/llm/providers/llamacpp.py"),
        Path("engineering/python/app/ai/llm/providers/lmstudio.py"),
        Path("engineering/python/app/ai/llm/providers/koboldcpp.py"),
        # Phase 3: Server providers
        Path("engineering/python/app/ai/llm/providers/tgi.py"),
        Path("engineering/python/app/ai/llm/providers/vllm.py"),
        # Phase 4: Cloud providers
        Path("engineering/python/app/ai/llm/providers/cloud/anthropic_provider.py"),
        Path("engineering/python/app/ai/llm/providers/cloud/openai_compatible.py"),
        Path("engineering/python/app/ai/llm/providers/cloud/qwen_provider.py"),
    ]

    # 过滤存在的文件
    provider_files = [f for f in provider_files if f.exists()]

    print(f"Found {len(provider_files)} provider files to process")
    print("=" * 80)

    results = []
    total_changes = 0
    total_errors = 0

    for i, file_path in enumerate(provider_files, 1):
        print(f"\n[{i}/{len(provider_files)}] {file_path.name}")
        print("-" * 80)

        # 分析文件
        analysis = analyze_file(file_path)
        print(f"   Lines: {analysis['lines']}")
        print(f"   Exceptions: {analysis['exception_count']}")
        print(f"   Async methods: {analysis['async_methods']}")

        # 修复文件
        result = fix_provider_file(file_path)
        results.append(result)

        if result["status"] == "fixed":
            total_changes += result["changes"]
            print(f"   Fixed: {result['messages']}")
        elif result["status"] == "unchanged":
            print(f"   {result['messages']}")
        elif result["status"] == "error":
            total_errors += 1
            print(f"   Error: {', '.join(result['errors'])}")
        else:
            print(f"   Skipped")

    # 生成报告
    print("\n" + "=" * 80)
    print("修复结果汇总")
    print("=" * 80)
    print(f"总文件数: {len(provider_files)}")
    print(f"已修复:   {sum(1 for r in results if r['status'] == 'fixed')}")
    print(f"未修改：{sum(1 for r in results if r['status'] == 'unchanged')}")
    print(f"错误：   {total_errors}")
    print(f"修改次数：{total_changes}")

    # 保存报告
    report = {
        "timestamp": datetime.now().isoformat(),
        "total_files": len(provider_files),
        "fixed": sum(1 for r in results if r["status"] == "fixed"),
        "unchanged": sum(1 for r in results if r["status"] == "unchanged"),
        "errors": total_errors,
        "total_changes": total_changes,
        "details": results,
    }

    report_path = Path("scripts/provider_fix_report.json")
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\n详细报告已保存到：{report_path}")

    # 后续步骤建议
    print("\n后续步骤:")
    print("   1. 检查修改后的文件：git diff engineering/python/app/ai/llm/providers/")
    print("   2. 运行相关测试：pytest engineering/python/tests/unit/test_ai*")
    print("   3. 恢复失败的 provider 备份文件")
    print("=" * 80)


if __name__ == "__main__":
    print("This script will modify multiple provider files.")
    print("   Backup files will be created automatically.\n")

    user_input = input("Continue? (y/N): ").strip().lower()
    if user_input != "y":
        print("Cancelled.")
        exit(0)

    main()
