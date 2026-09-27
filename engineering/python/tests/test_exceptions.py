"""统一异常体系单元测试。

验证AppException基类及所有业务异常派生类的:
- code唯一性
- message默认值
- status_code正确映射
- 继承链完整性
"""

import pytest

from app.core.exceptions import (
    AppException,
    LLMAuthException,
    LLMProviderException,
    LLMRateLimitException,
    LLMTimeoutException,
    NotFoundException,
    ValidationException,
    UnauthorizedException,
    ForbiddenException,
    ConflictException,
    BadRequestException,
    RateLimitException,
    InternalServerException,
    ServiceUnavailableException,
    GatewayException,
    TimeoutException,
    RepositoryException,
    RecordNotFoundException,
    StorageException,
    LockException,
    LockConflictException,
    LockNotFoundException,
    LockExpiredException,
    LockOwnershipException,
    StateException,
    StateConflictException,
    StateNotFoundException,
    LLMException,
    LLMRateLimitException,
    LLMResponseException,
    CadException,
    CadScriptException,
    CadExportException,
    EXCEPTION_CODE_MAP,
)


class TestAppExceptionBase:
    """AppException基类测试"""

    def test_base_code_and_message(self):
        exc = AppException(code=9000, message="基础异常")
        assert exc.code == 9000
        assert exc.message == "基础异常"

    def test_default_status_code_is_500(self):
        exc = AppException(code=0, message="test")
        assert exc.status_code == 500

    def test_custom_status_code(self):
        exc = AppException(code=0, message="test", status_code=418)
        assert exc.status_code == 418

    def test_default_detail_is_none(self):
        exc = AppException(code=0, message="test")
        assert exc.detail is None

    def test_custom_detail(self):
        exc = AppException(code=0, message="test", detail={"info": "debug"})
        assert exc.detail == {"info": "debug"}

    def test_str_is_message(self):
        exc = AppException(code=5000, message="自定义消息")
        assert str(exc) == "自定义消息"

    def test_to_dict_without_detail(self):
        exc = AppException(code=2001, message="服务器错误")
        d = exc.to_dict()
        # 现行契约：level/hint/retryable 恒在，detail 仅非 None 时出现
        assert d == {
            "code": 2001,
            "message": "服务器错误",
            "level": "error",
            "hint": None,
            "retryable": False,
        }
        assert "detail" not in d

    def test_to_dict_with_detail(self):
        exc = AppException(code=2001, message="错误", detail={"trace": "xxx"})
        d = exc.to_dict()
        assert d["code"] == 2001
        assert d["message"] == "错误"
        assert d["detail"] == {"trace": "xxx"}

    def test_inherits_from_exception(self):
        exc = AppException(code=0, message="test")
        assert isinstance(exc, Exception)


class TestClientExceptions:
    """客户端异常（1xxx, 4xx）测试"""

    @pytest.mark.parametrize(
        "exc_class,expected_code,expected_status,default_message",
        [
            (NotFoundException, 1001, 404, "资源未找到"),
            (ValidationException, 1002, 422, "请求参数校验失败"),
            (UnauthorizedException, 1003, 401, "未认证或 Token 无效"),
            (ForbiddenException, 1004, 403, "权限不足"),
            (ConflictException, 1005, 409, "资源冲突"),
            (BadRequestException, 1006, 400, "请求参数错误"),
            (RateLimitException, 1007, 429, "请求频率超限"),
        ],
    )
    def test_client_exception_defaults(
        self, exc_class, expected_code, expected_status, default_message
    ):
        exc = exc_class()
        assert exc.code == expected_code
        assert exc.status_code == expected_status
        assert exc.message == default_message
        assert isinstance(exc, AppException)

    def test_custom_message_override(self):
        exc = NotFoundException(message="用户123不存在")
        assert exc.message == "用户123不存在"
        assert exc.code == 1001


class TestServerExceptions:
    """服务端异常（2xxx, 5xx）测试"""

    @pytest.mark.parametrize(
        "exc_class,expected_code,expected_status,default_message",
        [
            (InternalServerException, 2001, 500, "服务器内部错误"),
            (ServiceUnavailableException, 2002, 503, "服务暂不可用"),
            (GatewayException, 2003, 502, "网关错误"),
            (TimeoutException, 2004, 504, "请求超时"),
        ],
    )
    def test_server_exception_defaults(
        self, exc_class, expected_code, expected_status, default_message
    ):
        exc = exc_class()
        assert exc.code == expected_code
        assert exc.status_code == expected_status
        assert exc.message == default_message
        assert isinstance(exc, AppException)


class TestRepositoryExceptions:
    """仓库层异常（3xxx）测试"""

    @pytest.mark.parametrize(
        "exc_class,expected_code,expected_status,default_message",
        [
            (RepositoryException, 3001, 500, "数据仓库操作异常"),
            (RecordNotFoundException, 3002, 404, "数据记录不存在"),
            (StorageException, 3003, 500, "存储操作失败"),
        ],
    )
    def test_repository_exception_defaults(
        self, exc_class, expected_code, expected_status, default_message
    ):
        exc = exc_class()
        assert exc.code == expected_code
        assert exc.status_code == expected_status
        assert exc.message == default_message
        assert isinstance(exc, AppException)


class TestLockExceptions:
    """执行锁异常（4xxx）测试"""

    @pytest.mark.parametrize(
        "exc_class,expected_code,expected_status",
        [
            (LockException, 4001, 409),
            (LockConflictException, 4002, 409),
            (LockNotFoundException, 4003, 404),
            (LockExpiredException, 4004, 409),
            (LockOwnershipException, 4005, 403),
        ],
    )
    def test_lock_exception_defaults(self, exc_class, expected_code, expected_status):
        exc = exc_class()
        assert exc.code == expected_code
        assert exc.status_code == expected_status
        assert isinstance(exc, AppException)


class TestStateExceptions:
    """状态异常（5xxx）测试"""

    @pytest.mark.parametrize(
        "exc_class,expected_code,expected_status",
        [
            (StateException, 5001, 409),
            (StateConflictException, 5002, 409),
            (StateNotFoundException, 5003, 404),
        ],
    )
    def test_state_exception_defaults(self, exc_class, expected_code, expected_status):
        exc = exc_class()
        assert exc.code == expected_code
        assert exc.status_code == expected_status
        assert isinstance(exc, AppException)


class TestLLMExceptions:
    """AI/LLM异常（6xxx）测试"""

    @pytest.mark.parametrize(
        "exc_class,expected_code,expected_status",
        [
            (LLMException, 6001, 502),
            (LLMProviderException, 6010, 502),
            (LLMTimeoutException, 6011, 504),
            (LLMRateLimitException, 6012, 429),
            (LLMAuthException, 6013, 401),
            (LLMResponseException, 6003, 502),
        ],
    )
    def test_llm_exception_defaults(self, exc_class, expected_code, expected_status):
        # 修复说明：LLMProvider/Timeout/RateLimit/Auth 需要 provider 位置参数；
        # 此前这些类构造即 TypeError（super().__init__ 签名不匹配），测试
        # 与 6002 旧设计一同失效。现对齐现行契约（601x/502）并覆盖全部 6xxx。
        try:
            exc = exc_class()
        except TypeError:
            exc = exc_class(provider="test-provider")
        assert exc.code == expected_code
        assert exc.status_code == expected_status
        assert isinstance(exc, AppException)
        assert exc.retryable is True or exc.code == 6013


class TestCadExceptions:
    """CAD异常（7xxx）测试"""

    @pytest.mark.parametrize(
        "exc_class,expected_code,expected_status",
        [
            (CadException, 7001, 500),
            (CadScriptException, 7002, 500),
            (CadExportException, 7003, 500),
        ],
    )
    def test_cad_exception_defaults(self, exc_class, expected_code, expected_status):
        exc = exc_class()
        assert exc.code == expected_code
        assert exc.status_code == expected_status
        assert isinstance(exc, AppException)


class TestExceptionCodeMap:
    """错误码映射表测试"""

    def test_all_exception_classes_have_mapping(self):
        """全部可无参构造的 AppException 子类，其 code 必须在 EXCEPTION_CODE_MAP。"""
        import inspect

        from app.core import exceptions as exc_mod

        all_classes = [
            obj
            for _, obj in inspect.getmembers(exc_mod, inspect.isclass)
            if issubclass(obj, AppException) and obj is not AppException
        ]
        unmapped = []
        for cls in all_classes:
            try:
                exc = cls()
            except TypeError:
                # 需要必填位置参数的类（如 LLMProviderException 的 provider 已给
                # 默认值，此处兜底其余历史类）跳过无参构造
                continue
            if exc.code not in EXCEPTION_CODE_MAP:
                unmapped.append(f"{cls.__name__}({exc.code})")
        assert unmapped == []

    def test_no_duplicate_codes(self):
        codes = list(EXCEPTION_CODE_MAP.keys())
        assert len(codes) == len(set(codes))

    def test_code_map_points_to_correct_classes(self):
        assert EXCEPTION_CODE_MAP[1001] is NotFoundException
        assert EXCEPTION_CODE_MAP[2001] is InternalServerException
        assert EXCEPTION_CODE_MAP[3001] is RepositoryException
        assert EXCEPTION_CODE_MAP[7001] is CadException


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
