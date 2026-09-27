"""
Enhanced Log Sanitizer

Filters sensitive information from logs and error responses:
- API tokens and authentication credentials
- File paths with username/personal info
- System configuration and secret keys
- Internal file paths and stack traces
- Database structure and sensitive config
"""

import logging
import re
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable
import getpass

logger = logging.getLogger(__name__)


@dataclass
class SanitizationRule:
    name: str
    pattern: re.Pattern | None = None
    keys: list[str] | None = None
    handler: Callable | None = None


class LogSanitizer:
    PROCESS_PARAM_KEYS = [
        "cutting_speed",
        "feed_rate",
        "spindle_speed",
        "depth_of_cut",
        "v_c",
        "f",
        "a_p",
        "n",
        "ap",
        "ae",
        "vf",
        "fz",
        "切削速度",
        "进给量",
        "主轴转速",
        "切削深度",
        "进给速度",
    ]

    PROCESS_PARAM_PATTERNS = [
        r"(切削速度[:\s]*)(\d+\.?\d*)\s*(rpm|m/min)",
        r"(进给量[:\s]*)(\d+\.?\d*)\s*(mm/rev|mm/min)",
        r"(主轴转速[:\s]*)(\d+\.?\d*)\s*(rpm)",
        r"(切削深度[:\s]*)(\d+\.?\d*)\s*(mm)",
        r"(进给速度[:\s]*)(\d+\.?\d*)\s*(mm/min)",
        r"(spindle\s*speed[:\s]*)(\d+\.?\d*)\s*(rpm)",
        r"(cutting\s*speed[:\s]*)(\d+\.?\d*)\s*(m/min)",
        r"(feed\s*rate[:\s]*)(\d+\.?\d*)\s*(mm/rev|mm/min)",
        r"(depth\s*of\s*cut[:\s]*)(\d+\.?\d*)\s*(mm)",
    ]

    FILE_CONTENT_KEYS = [
        "file_content",
        "cad_content",
        "nc_code",
        "gcode",
        "model_data",
        "binary_data",
        "upload_data",
        "file_data",
        "image_data",
        "design_content",
        "drawing_content",
        "program_content",
        "文件内容",
        "cad数据",
        "nc程序",
        "加工代码",
    ]

    USER_INPUT_KEYS = [
        "description",
        "comment",
        "remark",
        "note",
        "user_input",
        "feedback",
        "suggestion",
        "detail",
        "memo",
        "描述",
        "备注",
        "评论",
        "说明",
        "用户输入",
        "反馈",
        "建议",
    ]

    API_KEY_KEYS = [
        "api_key",
        "token",
        "secret",
        "credential",
        "access_key",
        "secret_key",
        "auth_token",
        "api_secret",
        "password",
        "authorization",
        "bearer_token",
        "api_token",
        "密钥",
        "令牌",
        "访问令牌",
        "授权码",
        "凭证",
    ]

    API_KEY_PATTERNS = [
        r"(sk_[a-zA-Z0-9]{8})[a-zA-Z0-9]+",
        r"(key-[a-zA-Z0-9]{8})[a-zA-Z0-9]+",
        r"(ghp_[a-zA-Z0-9]{8})[a-zA-Z0-9]+",
        r"(xox[bapsr]-[a-zA-Z0-9]{8})[a-zA-Z0-9]+",
        r"(Bearer\s+)[a-zA-Z0-9_.-]+",
        r"(Authorization:\s*)(?!Bearer\s)[a-zA-Z0-9_.-]+",
        r'(token["\s:=]+)([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})',
    ]

    PATH_PATTERNS = [
        r"C:\\Users\\([^\\]+)",
        r"/Users/([^/]+)",
        r"/home/([^/]+)",
    ]

    CONFIG_KEYS = [
        "database_url",
        "db_password",
        "redis_url",
        "aws_secret",
        "private_key",
        "encryption_key",
        "jwt_secret",
    ]

    USER_INPUT_MAX_LENGTH = 50

    def __init__(self):
        self._compiled_patterns = []
        for pattern in self.PROCESS_PARAM_PATTERNS:
            self._compiled_patterns.append(re.compile(pattern, re.IGNORECASE))

        self._api_key_compiled_patterns = []
        for pattern in self.API_KEY_PATTERNS:
            self._api_key_compiled_patterns.append(re.compile(pattern))

        self._path_compiled_patterns = []
        for pattern in self.PATH_PATTERNS:
            self._path_compiled_patterns.append(re.compile(pattern))

        try:
            self._current_user: str | None = getpass.getuser()
        except (OSError, RuntimeError) as e:
            # getuser() 失败时记录警告但不阻塞初始化
            logger.warning("Failed to get current user for log sanitization: %s", e)
            self._current_user = None

    def sanitize(self, data: Any) -> Any:
        if data is None:
            return None
        if isinstance(data, dict):
            return self._sanitize_dict(data)
        elif isinstance(data, list):
            return [self.sanitize(item) for item in data]
        elif isinstance(data, str):
            return self._sanitize_string(data)
        elif isinstance(data, (int, float, bool)):
            return data
        else:
            return str(data)

    def sanitize_error_response(self, error: Exception) -> dict:
        return {
            "error": type(error).__name__,
            "message": "Internal server error",
        }

    def sanitize_paths(self, text: str) -> str:
        """仅脱敏文本中的用户路径（适用于堆栈跟踪等纯文本场景）。

        与 :meth:`sanitize` 不同，本方法不做工艺参数/API Key 级别的
        语义脱敏，避免破坏错误信息对排障的价值。
        """
        sanitized = self._sanitize_file_paths(text)
        if self._current_user:
            sanitized = sanitized.replace(self._current_user, "[user]")
        return sanitized

    def _sanitize_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        sanitized = {}
        for key, value in data.items():
            if self._is_process_param_key(key):
                sanitized[key] = self._sanitize_process_param_value(key, value)
            elif self._is_file_content_key(key):
                sanitized[key] = self._sanitize_file_content(key, value)
            elif self._is_api_key_key(key) or self._is_config_key(key):
                sanitized[key] = self._sanitize_api_key(value)
            elif self._is_user_input_key(key):
                sanitized[key] = self._sanitize_user_input(value)
            else:
                sanitized[key] = self.sanitize(value)
        return sanitized

    def _sanitize_string(self, text: str) -> str:
        text = self._sanitize_process_param_patterns(text)
        text = self._sanitize_api_key_patterns(text)
        text = self._sanitize_file_paths(text)
        if self._current_user:
            text = text.replace(self._current_user, "[user]")
        return text

    def _is_process_param_key(self, key: str) -> bool:
        key_lower = key.lower()
        # 分隔符归一化：连字符/点与下划线等价（cutting-speed / feed.rate 均识别）
        key_normalized = key_lower.replace("-", "_").replace(".", "_")
        return key_normalized in [k.lower() for k in self.PROCESS_PARAM_KEYS]

    def _is_file_content_key(self, key: str) -> bool:
        key_lower = key.lower()
        return key_lower in [k.lower() for k in self.FILE_CONTENT_KEYS]

    def _is_api_key_key(self, key: str) -> bool:
        key_lower = key.lower()
        return key_lower in [k.lower() for k in self.API_KEY_KEYS]

    def _is_config_key(self, key: str) -> bool:
        key_lower = key.lower()
        return key_lower in [k.lower() for k in self.CONFIG_KEYS]

    def _is_user_input_key(self, key: str) -> bool:
        key_lower = key.lower()
        return key_lower in [k.lower() for k in self.USER_INPUT_KEYS]

    def _sanitize_process_param_value(self, key: str, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized = {}
            for k, v in value.items():
                if isinstance(v, (int, float)):
                    sanitized[k] = "[工艺参数已脱敏]"
                else:
                    sanitized[k] = v
            return sanitized
        elif isinstance(value, (int, float)):
            return "[工艺参数已脱敏]"
        elif isinstance(value, str):
            return self._sanitize_process_param_patterns(value)
        else:
            return value

    def _sanitize_process_param_patterns(self, text: str) -> str:
        for pattern in self._compiled_patterns:
            match = pattern.search(text)
            while match:
                prefix = match.group(1)
                unit = match.group(3) if len(match.groups()) >= 3 else ""
                replacement = f"{prefix}[工艺参数已脱敏]"
                if unit:
                    replacement += f" {unit}"
                text = text[: match.start()] + replacement + text[match.end() :]
                match = pattern.search(text)
        return text

    def _sanitize_file_content(self, key: str, value: Any) -> str:
        if isinstance(value, str):
            length = len(value)
            return f"[文件内容已脱敏 - 长度: {length}字符]"
        elif isinstance(value, bytes):
            length = len(value)
            return f"[文件内容已脱敏 - 长度: {length}字节]"
        elif isinstance(value, dict):
            file_type = value.get("type", value.get("file_type", "unknown"))
            file_size = value.get("size", value.get("file_size", "unknown"))
            file_format = value.get("format", value.get("file_format", "unknown"))
            return f"[文件内容已脱敏 - 类型: {file_type}, 大小: {file_size}, 格式: {file_format}]"
        else:
            return "[文件内容已脱敏]"

    def _sanitize_user_input(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            # [S-H1] 日志注入防御：过滤 \n \r 控制字符，防止攻击者伪造日志行
            # 违反 FDA 21 CFR Part 11 审计日志完整性要求
            sanitized = value.replace("\r", "\\r").replace("\n", "\\n")
            # 同时过滤其他危险控制字符（\t 保留，制表符在日志中通常无害）
            sanitized = "".join(ch if ch == "\t" or (ord(ch) >= 0x20) else f"\\x{ord(ch):02x}" for ch in sanitized)
            if len(sanitized) > self.USER_INPUT_MAX_LENGTH:
                # 截断后总长（含省略号）不超过上限
                return sanitized[: self.USER_INPUT_MAX_LENGTH - 3] + "..."
            return sanitized
        return str(value)

    def _sanitize_api_key(self, value: Any) -> str:
        if isinstance(value, str):
            if len(value) <= 4:
                return "[已脱敏]"
            return f"[已脱敏]{value[-4:]}"
        return "[已脱敏]"

    def _sanitize_api_key_patterns(self, text: str) -> str:
        for pattern in self._api_key_compiled_patterns:
            match = pattern.search(text)
            while match:
                prefix = match.group(1)
                full_match = match.group(0)
                masked_value = full_match[len(prefix) :]
                if len(masked_value) > 4:
                    masked_value = "[已脱敏]" + masked_value[-4:]
                else:
                    masked_value = "[已脱敏]"
                text = text[: match.start()] + prefix + masked_value + text[match.end() :]
                match = pattern.search(text)
        return text

    def _sanitize_file_paths(self, text: str) -> str:
        for pattern in self._path_compiled_patterns:
            text = pattern.sub(lambda m: m.group(0).split(m.group(1))[0] + "[user]", text)
        text = re.sub(r"\\([^\\]+)\\AppData", "[user]\\\\AppData", text)
        return text


sanitizer = LogSanitizer()
