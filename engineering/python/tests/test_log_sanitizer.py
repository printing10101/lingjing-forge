"""
Test Log Sanitizer Implementation

Tests for:
- LogSanitizer: Log data sanitization for sensitive information
- Process parameter masking
- API key redaction
- File content protection
- User input truncation
"""

import pytest
from app.core.log_sanitizer import LogSanitizer


class TestLogSanitizerInitialization:
    """Test sanitizer initialization"""

    def test_default_initialization(self):
        sanitizer = LogSanitizer()
        assert len(sanitizer._compiled_patterns) > 0
        assert len(sanitizer._api_key_compiled_patterns) > 0

    def test_process_param_keys_defined(self):
        sanitizer = LogSanitizer()
        assert "cutting_speed" in [k.lower() for k in sanitizer.PROCESS_PARAM_KEYS]
        assert "feed_rate" in [k.lower() for k in sanitizer.PROCESS_PARAM_KEYS]

    def test_api_key_keys_defined(self):
        sanitizer = LogSanitizer()
        assert "api_key" in [k.lower() for k in sanitizer.API_KEY_KEYS]
        assert "token" in [k.lower() for k in sanitizer.API_KEY_KEYS]


class TestLogSanitizerProcessParams:
    """Test process parameter sanitization"""

    def test_sanitize_process_param_numeric(self):
        sanitizer = LogSanitizer()
        result = sanitizer._sanitize_process_param_value("cutting_speed", 150.5)
        assert result == "[工艺参数已脱敏]"

    def test_sanitize_process_param_dict(self):
        sanitizer = LogSanitizer()
        data = {"rpm": 3000, "type": "m/min"}
        result = sanitizer._sanitize_process_param_value("cutting_speed", data)
        assert result["rpm"] == "[工艺参数已脱敏]"
        assert result["type"] == "m/min"

    def test_sanitize_process_param_string(self):
        sanitizer = LogSanitizer()
        result = sanitizer._sanitize_process_param_value(
            "cutting_speed", "切削速度: 150 m/min"
        )
        assert "[工艺参数已脱敏]" in result

    def test_sanitize_chinese_process_params(self):
        sanitizer = LogSanitizer()
        text = "切削速度: 200 rpm"
        result = sanitizer._sanitize_process_param_patterns(text)
        assert "[工艺参数已脱敏]" in result

    def test_sanitize_english_process_params(self):
        sanitizer = LogSanitizer()
        text = "cutting speed: 150 m/min"
        result = sanitizer._sanitize_process_param_patterns(text)
        assert "[工艺参数已脱敏]" in result

    def test_sanitize_multiple_params_in_text(self):
        sanitizer = LogSanitizer()
        text = "切削速度: 100rpm, 进给量: 0.3mm/rev"
        result = sanitizer._sanitize_process_param_patterns(text)
        assert result.count("[工艺参数已脱敏]") == 2


class TestLogSanitizerApiKeys:
    """Test API key sanitization"""

    def test_sanitize_short_api_key(self):
        sanitizer = LogSanitizer()
        result = sanitizer._sanitize_api_key("abc")
        assert result == "[已脱敏]"

    def test_sanitize_long_api_key(self):
        sanitizer = LogSanitizer()
        result = sanitizer._sanitize_api_key("sk_test1234567890abcdef")
        assert result.startswith("[已脱敏]")
        assert result.endswith("cdef")

    def test_sanitize_non_string_api_key(self):
        sanitizer = LogSanitizer()
        result = sanitizer._sanitize_api_key(12345)
        assert result == "[已脱敏]"

    def test_sanitize_api_key_pattern_openai(self):
        sanitizer = LogSanitizer()
        text = "sk_test1234567890abcdefghij"
        result = sanitizer._sanitize_api_key_patterns(text)
        assert "[已脱敏]" in result

    def test_sanitize_api_key_pattern_github(self):
        sanitizer = LogSanitizer()
        text = "ghp_abcdefgh1234567890"
        result = sanitizer._sanitize_api_key_patterns(text)
        assert "[已脱敏]" in result

    def test_sanitize_bearer_token(self):
        sanitizer = LogSanitizer()
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        result = sanitizer._sanitize_api_key_patterns(text)
        assert "Bearer" in result
        assert "eyJ" not in result or "[已脱敏]" in result


class TestLogSanitizerFileContent:
    """Test file content sanitization"""

    def test_sanitize_file_content_string(self):
        sanitizer = LogSanitizer()
        result = sanitizer._sanitize_file_content("file_content", "G00 X100 Y200")
        assert "[文件内容已脱敏" in result
        assert "100" not in result

    def test_sanitize_file_content_bytes(self):
        sanitizer = LogSanitizer()
        data = b"G00 X100 Y200"
        result = sanitizer._sanitize_file_content("file_content", data)
        assert "[文件内容已脱敏" in result
        assert "字节" in result

    def test_sanitize_file_content_dict(self):
        sanitizer = LogSanitizer()
        data = {"type": "gcode", "size": 1024, "format": "txt"}
        result = sanitizer._sanitize_file_content("file_content", data)
        assert "[文件内容已脱敏" in result
        assert "gcode" in result

    def test_sanitize_cad_content(self):
        sanitizer = LogSanitizer()
        result = sanitizer._sanitize_file_content("cad_content", "CAD_MODEL_DATA")
        assert "[文件内容已脱敏" in result

    def test_sanitize_gcode_content(self):
        sanitizer = LogSanitizer()
        result = sanitizer._sanitize_file_content("gcode", "G01 X50 F100")
        assert "[文件内容已脱敏" in result


class TestLogSanitizerUserInput:
    """Test user input sanitization"""

    def test_sanitize_short_user_input(self):
        sanitizer = LogSanitizer()
        result = sanitizer._sanitize_user_input("短文本")
        assert result == "短文本"

    def test_sanitize_long_user_input_truncation(self):
        sanitizer = LogSanitizer()
        long_text = "a" * 100
        result = sanitizer._sanitize_user_input(long_text)
        assert len(result) == 50
        assert result.endswith("...")

    def test_sanitize_user_input_at_boundary(self):
        sanitizer = LogSanitizer()
        boundary_text = "a" * 50
        result = sanitizer._sanitize_user_input(boundary_text)
        assert len(result) == 50
        assert not result.endswith("...")

    def test_sanitize_none_user_input(self):
        sanitizer = LogSanitizer()
        result = sanitizer._sanitize_user_input(None)
        assert result is None

    def test_sanitize_numeric_user_input(self):
        sanitizer = LogSanitizer()
        result = sanitizer._sanitize_user_input(12345)
        assert result == "12345"


class TestLogSanitizerKeyDetection:
    """Test key detection logic"""

    def test_is_process_param_key_case_insensitive(self):
        sanitizer = LogSanitizer()
        assert sanitizer._is_process_param_key("Cutting_Speed")
        assert sanitizer._is_process_param_key("CUTTING_SPEED")
        assert sanitizer._is_process_param_key("切削速度")

    def test_is_file_content_key_case_insensitive(self):
        sanitizer = LogSanitizer()
        assert sanitizer._is_file_content_key("File_Content")
        assert sanitizer._is_file_content_key("CAD_CONTENT")

    def test_is_api_key_key_case_insensitive(self):
        sanitizer = LogSanitizer()
        assert sanitizer._is_api_key_key("API_KEY")
        assert sanitizer._is_api_key_key("Token")

    def test_is_user_input_key_case_insensitive(self):
        sanitizer = LogSanitizer()
        assert sanitizer._is_user_input_key("Description")
        assert sanitizer._is_user_input_key("备注")


class TestLogSanitizerMainSanitize:
    """Test main sanitize method"""

    def test_sanitize_dict_full(self):
        sanitizer = LogSanitizer()
        data = {
            "cutting_speed": 150,
            "description": "Test machining process",
            "file_content": "G-code data",
        }
        result = sanitizer.sanitize(data)
        assert result["cutting_speed"] == "[工艺参数已脱敏]"
        assert "Test machining process" in result["description"]
        assert "[文件内容已脱敏" in result["file_content"]

    def test_sanitize_list(self):
        sanitizer = LogSanitizer()
        data = [
            {"cutting_speed": 100},
            {"feed_rate": 0.5},
        ]
        result = sanitizer.sanitize(data)
        assert result[0]["cutting_speed"] == "[工艺参数已脱敏]"
        assert result[1]["feed_rate"] == "[工艺参数已脱敏]"

    def test_sanitize_string_only(self):
        sanitizer = LogSanitizer()
        text = "切削速度: 150rpm, 备注: 加工测试"
        result = sanitizer.sanitize(text)
        assert "[工艺参数已脱敏]" in result

    def test_sanitize_primitive_types(self):
        sanitizer = LogSanitizer()
        assert sanitizer.sanitize(42) == 42
        assert sanitizer.sanitize(3.14) == 3.14
        assert sanitizer.sanitize(True) is True

    def test_sanitize_none(self):
        sanitizer = LogSanitizer()
        assert sanitizer.sanitize(None) is None

    def test_sanitize_nested_dict(self):
        sanitizer = LogSanitizer()
        data = {
            "level1": {
                "level2": {"cutting_speed": 200, "nested_list": [{"feed_rate": 0.3}]}
            }
        }
        result = sanitizer.sanitize(data)
        assert result["level1"]["level2"]["cutting_speed"] == "[工艺参数已脱敏]"
        assert (
            result["level1"]["level2"]["nested_list"][0]["feed_rate"]
            == "[工艺参数已脱敏]"
        )


class TestLogSanitizerEdgeCases:
    """Test edge cases"""

    def test_sanitize_empty_dict(self):
        sanitizer = LogSanitizer()
        result = sanitizer.sanitize({})
        assert result == {}

    def test_sanitize_empty_list(self):
        sanitizer = LogSanitizer()
        result = sanitizer.sanitize([])
        assert result == []

    def test_sanitize_special_characters_in_keys(self):
        sanitizer = LogSanitizer()
        data = {"cutting-speed": 100, "feed.rate": 0.3}
        result = sanitizer.sanitize(data)
        assert result["cutting-speed"] == "[工艺参数已脱敏]"
        assert result["feed.rate"] == "[工艺参数已脱敏]"

    def test_sanitize_unicode_process_params(self):
        sanitizer = LogSanitizer()
        text = "主轴转速: 3000rpm"
        result = sanitizer._sanitize_process_param_patterns(text)
        assert "[工艺参数已脱敏]" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
