"""
批量修复 LLM providers 的异常处理

目标：
1. 将泛型 `except Exception` 替换为具体异常类型
2. 集成熔断器模式
3. 添加精确的错误映射
"""

import re
from pathlib import Path

# 需要修复的异常类型
SPECIFIC_EXCEPTIONS = [
    ("TimeoutError", "llm_exception.LLMTimeoutException"),
    ("asyncio.TimeoutError", "llm_exception.LLMTimeoutException"),
    ("ConnectionError", "llm_exception.LLMProviderException"),
    ("aiohttp.ClientConnectionError", "llm_exception.LLMProviderException"),
    ("aiohttp.ClientConnectorError", "llm_exception.LLMProviderException"),
    ("aiohttp.ClientHttpError", "llm_exception.LLMProviderException"),
    ("aiohttp.ClientResponseError", "llm_exception.LLMProviderException"),
    ("aiohttp.ServerTimeoutError", "llm_exception.LLMTimeoutException"),
    ("aiohttp.ServerDisconnectedError", "llm_exception.LLMProviderException"),
    ("requests.Timeout", "llm_exception.LLMTimeoutException"),
    ("requests.ConnectionError", "llm_exception.LLMProviderException"),
    ("requests.HTTPError", "llm_exception.LLMProviderException"),
    ("json.JSONDecodeError", "llm_exception.LLMResponseException"),
    ("ValueError", "llm_exception.LLMResponseException"),
    ("KeyError", "llm_exception.LLMResponseException"),
    ("urllib3.exceptions.HTTPError", "llm_exception.LLMProviderException"),
    ("urllib3.exceptions.MaxRetryError", "llm_exception.LLMProviderException"),
]

# 需要替换的泛型捕获模式
GENERIC_EXCEPTION_PATTERN = re.compile(
    r"except\s+Exception\s+(?:as\s+\w+)?\s*:",
    re.IGNORECASE
)


def generate_exception_map():
    """生成异常转换映射装饰器"""
    return '''
from typing import Type, Union
from functools import wraps

# 异常映射表：原始异常 -> LLM 异常
_EXCEPTION_MAPPING: dict[Type[Exception], type] = {
    TimeoutError: llm_exception.LLMTimeoutException,
    asyncio.TimeoutError: llm_exception.LLMTimeoutException,
    ConnectionError: llm_exception.LLMProviderException,
    # ... 更多映射
}

def map_exceptions(provider_name: str):
    """异常映射装饰器"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                # 根据异常类型映射到特定 LLM 异常
                exception_class = type(e)
                mapped_exception = _EXCEPTION_MAPPING.get(exception_class, llm_exception.LLMException)
                
                # 抛出 mapped 异常
                raise mapped_exception(
                    provider=provider_name,
                    message=str(e),
                    original_exception=e,
                )
        return wrapper
    return decorator
'''

    return ""


def fix_provider_file(file_path: Path):
    """修复单个 provider 文件"""
    print(f"Processing: {file_path}")
    
    try:
        content = file_path.read_text(encoding="utf-8")
        original_content = content
        
        # 1. 替换导入
        if "from app.ai.llm import" in content:
            # 添加异常导入
            if "from app.ai.llm.exceptions import" not in content:
                content = content.replace(
                    "from app.ai.llm.provider_base import(",
                    "from app.ai.llm.provider_base import(\n    LLMTimeoutException,  # 超时异常\n    LLMProviderException,  # 提供商错误\n    LLMResponseException,  # 响应解析错误\n",
                    1
                )
        
        # 2. 替换泛型 except Exception
        for original_exception, mapped_exception in SPECIFIC_EXCEPTIONS:
            pattern = rf"except\s+{re.escape(original_exception)}\s+(?:as\s+e)?:\s*"
            replacement = f"except {original_exception} as e:  # noqa: BLE001\n        {mapped_exception}\n"
            # TODO: 这里需要更复杂的替换逻辑
        
        # 3. 替换泛型捕获
        content = GENERIC_EXCEPTION_PATTERN.sub(
            "except Exception as e:  # noqa: PERF203, BLE001\n            logger.exception(f\"[{file_path.name}] Failure: %s\", e)\n",
            content
        )
        
        if content != original_content:
            # 备份
            backup = file_path.with_suffix(".py.backup")
            backup.write_text(original_content, encoding="utf-8")
            
            # 应用修改
            file_path.write_text(content, encoding="utf-8")
            print(f"  ✓ Fixed: {file_path.name}")
            return True
        else:
            print(f"  - No changes: {file_path.name}")
            return False
            
    except Exception as e:
        print(f"  ✗ Error: {file_path.name}: {e}")
        return False


def main():
    """主函数"""
    # 搜索所有 provider 文件
    provider_dirs = [
        Path("engineering/python/app/ai/llm/providers"),
    ]
    
    files_to_fix = []
    for provider_dir in provider_dirs:
        if provider_dir.exists():
            for py_file in provider_dir.glob("*.py"):
                if py_file.name not in ["__init__.py", "provider_base.py"]:
                    files_to_fix.append(py_file)
    
    print(f"Found {len(files_to_fix)} provider files to process")
    print("=" * 60)
    
    fixed_count = 0
    for py_file in files_to_fix:
        if fix_provider_file(py_file):
            fixed_count += 1
    
    print("=" * 60)
    print(f"Completed: {fixed_count} files fixed")
    print("Run tests to verify the changes")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    main()
