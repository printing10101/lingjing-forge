"""统一异常体系——基类与业务异常派生类。

每个异常类型具备:
- code: 唯一数字错误码
- message: 错误描述
- status_code: HTTP 状态码
- detail: 可选的详细信息
- error_level: 错误等级 (INFO/WARNING/ERROR/CRITICAL)
- retryable: 是否可重试
- hint: 用户友好提示
- circuit_breaker: 是否触发熔断
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorLevel(str, Enum):
    """错误等级"""

    INFO = "info"  # 信息级别，无需处理
    WARNING = "warning"  # 警告，可自动恢复
    ERROR = "error"  # 错误，需要人工介入
    CRITICAL = "critical"  # 严重错误，系统不可用


class AppException(Exception):
    """应用异常基类，所有业务异常均从此派生。

    Attributes:
        code: 数字错误码，全局唯一
        message: 用户可读的错误描述
        status_code: 对应 HTTP 状态码
        detail: 可选的调试/补充信息
        error_level: 错误等级
        retryable: 是否可重试
        hint: 用户友好提示
    """

    def __init__(
        self,
        code: int,
        message: str,
        status_code: int = 500,
        detail: Any = None,
        error_level: ErrorLevel = ErrorLevel.ERROR,
        retryable: bool = False,
        hint: str = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail
        self.error_level = error_level
        self.retryable = retryable
        self.hint = hint
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式，用于 API 响应"""
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "level": self.error_level.value,
            "hint": self.hint,
            "retryable": self.retryable,
        }
        if self.detail is not None:
            result["detail"] = self.detail
        return result


# 客户端错误 (1xxx, HTTP 4xx)


class NotFoundException(AppException):
    def __init__(self, message: str = "资源未找到", detail: Any = None):
        super().__init__(code=1001, message=message, status_code=404, detail=detail, hint="请检查资源 ID 是否正确")


class ValidationException(AppException):
    def __init__(self, message: str = "请求参数校验失败", detail: Any = None):
        super().__init__(code=1002, message=message, status_code=422, detail=detail, hint="请检查请求参数格式")


class UnauthorizedException(AppException):
    def __init__(self, message: str = "未认证或 Token 无效", detail: Any = None):
        super().__init__(code=1003, message=message, status_code=401, detail=detail, hint="请重新登录", retryable=True)


class ForbiddenException(AppException):
    def __init__(self, message: str = "权限不足", detail: Any = None):
        super().__init__(code=1004, message=message, status_code=403, detail=detail, hint="请联系管理员授权")


class ConflictException(AppException):
    def __init__(self, message: str = "资源冲突", detail: Any = None):
        super().__init__(code=1005, message=message, status_code=409, detail=detail, retryable=True)


class BadRequestException(AppException):
    def __init__(self, message: str = "请求参数错误", detail: Any = None):
        super().__init__(code=1006, message=message, status_code=400, detail=detail, hint="请检查请求格式")


class RateLimitException(AppException):
    def __init__(self, message: str = "请求频率超限", detail: Any = None):
        super().__init__(code=1007, message=message, status_code=429, detail=detail, hint="请稍后重试", retryable=True)


# 服务端错误 (2xxx, HTTP 5xx)


class InternalServerException(AppException):
    def __init__(self, message: str = "服务器内部错误", detail: Any = None):
        super().__init__(code=2001, message=message, status_code=500, detail=detail, hint="请稍后重试", retryable=True)


class ServiceUnavailableException(AppException):
    def __init__(self, message: str = "服务暂不可用", detail: Any = None):
        super().__init__(code=2002, message=message, status_code=503, detail=detail, hint="系统维护中", retryable=True)


class GatewayException(AppException):
    def __init__(self, message: str = "网关错误", detail: Any = None):
        super().__init__(code=2003, message=message, status_code=502, detail=detail, hint="请稍后重试", retryable=True)


class TimeoutException(AppException):
    def __init__(self, message: str = "请求超时", detail: Any = None):
        super().__init__(
            code=2004, message=message, status_code=504, detail=detail, hint="请检查网络连接", retryable=True
        )


# 仓库层错误 (3xxx)


class RepositoryException(AppException):
    def __init__(self, message: str = "数据仓库操作异常", detail: Any = None):
        super().__init__(
            code=3001, message=message, status_code=500, detail=detail, hint="请联系管理员", retryable=True
        )


class RecordNotFoundException(AppException):
    def __init__(self, message: str = "数据记录不存在", detail: Any = None):
        super().__init__(code=3002, message=message, status_code=404, detail=detail, hint="请检查记录 ID")


class StorageException(AppException):
    def __init__(self, message: str = "存储操作失败", detail: Any = None):
        super().__init__(
            code=3003, message=message, status_code=500, detail=detail, hint="请检查磁盘空间", retryable=True
        )


# 执行锁错误 (4xxx)


class LockException(AppException):
    def __init__(self, message: str = "锁操作异常", detail: Any = None):
        super().__init__(code=4001, message=message, status_code=409, detail=detail, hint="请稍后重试", retryable=True)


class LockConflictException(AppException):
    def __init__(self, message: str = "锁冲突", detail: Any = None):
        super().__init__(code=4002, message=message, status_code=409, detail=detail, hint="请稍后重试", retryable=True)


class LockNotFoundException(AppException):
    def __init__(self, message: str = "锁不存在", detail: Any = None):
        super().__init__(code=4003, message=message, status_code=404, detail=detail, hint="请检查锁 ID")


class LockExpiredException(AppException):
    def __init__(self, message: str = "锁已过期", detail: Any = None):
        super().__init__(
            code=4004, message=message, status_code=409, detail=detail, hint="请重新获取锁", retryable=True
        )


class LockOwnershipException(AppException):
    def __init__(self, message: str = "锁所属权错误", detail: Any = None):
        super().__init__(code=4005, message=message, status_code=403, detail=detail, hint="请确认锁所有权")


# 状态持久化错误 (5xxx)


class StateException(AppException):
    def __init__(self, message: str = "状态操作异常", detail: Any = None):
        super().__init__(code=5001, message=message, status_code=409, detail=detail, hint="请稍后重试", retryable=True)


class StateConflictException(AppException):
    def __init__(self, message: str = "状态冲突", detail: Any = None):
        super().__init__(code=5002, message=message, status_code=409, detail=detail, hint="请稍后重试", retryable=True)


class StateNotFoundException(AppException):
    def __init__(self, message: str = "状态不存在", detail: Any = None):
        super().__init__(code=5003, message=message, status_code=404, detail=detail, hint="请检查状态 ID")


# AI/LLM错误 (6xxx)


class LLMException(AppException):
    def __init__(self, message: str = "大模型调用异常", detail: Any = None):
        super().__init__(
            code=6001, message=message, status_code=502, detail=detail, hint="请检查 AI 服务状态", retryable=True
        )


class LLMProviderException(LLMException):
    """AI 提供商服务失败

    修复历史 bug：原实现以 ``super().__init__(code=...)`` 调用
    ``LLMException.__init__(message, detail)``，签名不匹配——本类一旦
    被真正 raise 就抛 TypeError（这也是 6xxx 异常长期"零使用"未被发现
    的原因）。现显式调用 ``AppException.__init__``，兼容双继承桥接
    （见 ``app.ai.llm._resilience``）。
    """

    def __init__(self, provider: str = "LLM", message: str = None, detail: Any = None):
        AppException.__init__(
            self,
            code=6010,
            status_code=502,
            message=message or f"{provider} 服务异常",
            detail=detail,
            hint=f"请检查 {provider} 服务状态或切换备用提供商",
            retryable=True,
        )


class LLMTimeoutException(LLMException):
    """AI 服务超时"""

    def __init__(self, provider: str = "LLM", timeout_sec: float = None, detail: Any = None):
        AppException.__init__(
            self,
            code=6011,
            status_code=504,
            message=f"{provider} 服务响应超时" + (f" ({timeout_sec}s)" if timeout_sec else ""),
            detail=detail,
            hint="请检查网络状态或增加超时时间",
            retryable=True,
        )


class LLMRateLimitException(LLMException):
    """AI 服务限流"""

    def __init__(self, provider: str = "LLM", retry_after: int = None, detail: Any = None):
        AppException.__init__(
            self,
            code=6012,
            status_code=429,
            message=f"{provider} 服务达到限流阈值",
            detail=detail,
            hint="请稍后重试" + (f"或等待{retry_after}s" if retry_after else ""),
            retryable=True,
        )


class LLMAuthException(LLMException):
    """AI 认证失败"""

    def __init__(self, provider: str = "LLM", message: str = None, detail: Any = None):
        AppException.__init__(
            self,
            code=6013,
            status_code=401,
            message=message or f"{provider} 认证失败",
            detail=detail,
            hint="请检查 API Key 配置",
            retryable=False,
        )


class LLMResponseException(AppException):
    def __init__(self, message: str = "LLM 响应无效", detail: Any = None):
        super().__init__(code=6003, message=message, status_code=502, detail=detail, hint="请重试请求", retryable=True)


# CAD错误 (7xxx)


class CadException(AppException):
    def __init__(self, message: str = "CAD 操作异常", detail: Any = None):
        super().__init__(code=7001, message=message, status_code=500, detail=detail, hint="请检查文件格式")


class CadScriptException(AppException):
    def __init__(self, message: str = "CAD 脚本执行失败", detail: Any = None):
        super().__init__(code=7002, message=message, status_code=500, detail=detail, hint="请检查脚本语法")


class CadExportException(AppException):
    def __init__(self, message: str = "CAD 导出失败", detail: Any = None):
        super().__init__(code=7003, message=message, status_code=500, detail=detail, hint="请检查导出格式支持")


class CadParserException(CadException):
    """CAD 解析失败"""

    def __init__(self, message: str = "CAD 解析失败", format_hint: str = None, detail: Any = None):
        hint = "请检查文件格式"
        if format_hint:
            hint += f"（仅支持 {format_hint}）"
        super().__init__(code=7010, message=message, detail=detail, hint=hint, retryable=False)


class CadFileNotFoundError(CadException):
    """CAD 文件未找到"""

    def __init__(self, file_path: str = None, detail: Any = None):
        super().__init__(
            code=7011,
            message="CAD 文件未找到" + (f": {file_path}" if file_path else ""),
            detail=detail,
            hint="请检查文件路径",
            retryable=False,
        )


# NC 代码生成错误 (8xxx)


class NCCodeException(AppException):
    def __init__(self, message: str = "NC 代码生成异常", detail: Any = None):
        super().__init__(code=8001, message=message, status_code=500, detail=detail, hint="请检查工艺参数")


class NCCodeGenerationException(NCCodeException):
    """NC 代码生成失败"""

    def __init__(self, message: str = "NC 代码生成失败", detail: Any = None):
        super().__init__(code=8010, message=message, detail=detail, hint="请检查输入参数")


class PostprocessorException(NCCodeException):
    """后处理器错误"""

    def __init__(self, postprocessor_name: str, message: str = None, detail: Any = None):
        super().__init__(
            code=8011,
            message=message or f"{postprocessor_name} 后处理器异常",
            detail=detail,
            hint=f"请检查 {postprocessor_name} 配置" or "请尝试其他后处理器",
            retryable=True,
        )


class NCCodeValidationException(NCCodeException):
    """NC 代码验证失败"""

    def __init__(self, errors: list[str] = None, detail: Any = None):
        super().__init__(
            code=8012,
            message="NC 代码验证失败" + (f": {', '.join(errors[:3])}" if errors else ""),
            detail=detail,
            hint="请修正 G/M 代码错误",
            retryable=False,
        )


# 熔断器错误 (9xxx)


class CircuitBreakerOpenException(AppException):
    """熔断器已打开，拒绝调用"""

    def __init__(self, service: str, opened_at: str = None, detail: Any = None):
        super().__init__(
            code=9001,
            message=f"服务 [{service}] 熔断器已打开",
            detail=detail,
            hint="请稍后重试，系统将自动恢复",
            retryable=True,
        )


class CircuitBreakerHalfOpenException(AppException):
    """熔断器半开状态，正在探测"""

    def __init__(self, service: str, attempts: int = None, detail: Any = None):
        super().__init__(
            code=9002,
            message=f"服务 [{service}] 正在健康检查",
            detail=detail,
            hint=f"当前状态：半开/剩余尝试 {attempts}" if attempts else "",
            retryable=True,
        )


# 错误码映射：AppException.code -> 异常类

EXCEPTION_CODE_MAP: dict[int, type[AppException]] = {
    # 客户端错误
    1001: NotFoundException,
    1002: ValidationException,
    1003: UnauthorizedException,
    1004: ForbiddenException,
    1005: ConflictException,
    1006: BadRequestException,
    1007: RateLimitException,
    # 服务端错误
    2001: InternalServerException,
    2002: ServiceUnavailableException,
    2003: GatewayException,
    2004: TimeoutException,
    # 仓库层
    3001: RepositoryException,
    3002: RecordNotFoundException,
    3003: StorageException,
    # 执行锁
    4001: LockException,
    4002: LockConflictException,
    4003: LockNotFoundException,
    4004: LockExpiredException,
    4005: LockOwnershipException,
    # 状态持久化
    5001: StateException,
    5002: StateConflictException,
    5003: StateNotFoundException,
    # AI/LLM
    6001: LLMException,
    6003: LLMResponseException,
    6010: LLMProviderException,
    6011: LLMTimeoutException,
    6012: LLMRateLimitException,
    6013: LLMAuthException,
    # CAD
    7001: CadException,
    7002: CadScriptException,
    7003: CadExportException,
    7010: CadParserException,
    7011: CadFileNotFoundError,
    # NC 代码
    8001: NCCodeException,
    8010: NCCodeGenerationException,
    8011: PostprocessorException,
    8012: NCCodeValidationException,
    # 熔断器
    9001: CircuitBreakerOpenException,
    9002: CircuitBreakerHalfOpenException,
}
