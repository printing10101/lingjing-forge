"""工艺规则版本工具（从 rule_db 拆出）。"""

from __future__ import annotations

import json
import logging


from app.database._constants import VERSION_FILE

logger = logging.getLogger(__name__)


def get_project_version() -> str:
    """从项目根目录的VERSION文件动态读取版本号"""
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
        logger.warning("无法读取VERSION文件: %s，使用默认版本 0.0.0", e)
        return "0.0.0"


def parse_version(version_str: str) -> tuple[int, int, int]:
    """解析版本字符串为 (major, minor, patch) 元组"""
    try:
        parts = version_str.strip().split(".")
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
        return (major, minor, patch)
    except (ValueError, IndexError):
        return (0, 0, 0)


def check_version_compatibility(import_version: str, current_version: str) -> tuple[bool, str]:
    """
    检查导入文件版本与当前项目版本的兼容性

    兼容规则：
    - 主版本号相同 → 兼容
    - 主版本号不同 → 不兼容

    Returns:
        (是否兼容, 提示信息)
    """
    import_major, _, _ = parse_version(import_version)
    current_major, current_minor, current_patch = parse_version(current_version)

    if import_major == current_major:
        if import_version == current_version:
            return True, f"版本完全匹配 ({current_version})"
        else:
            return True, (
                f"版本兼容 (导入文件: {import_version}, 当前项目: {current_version})。主版本号相同，数据格式兼容。"
            )
    else:
        return False, (
            f"版本不兼容！导入文件版本 {import_version} 与当前项目版本 {current_version} 主版本号不同。"
            f"强制导入可能导致数据异常，请确认文件来源或使用匹配版本的项目。"
        )
